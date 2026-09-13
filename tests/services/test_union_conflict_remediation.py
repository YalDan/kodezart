"""Native conflict remediation survives caching and disappears on repair."""

import asyncio
from pathlib import Path

import pytest

from kodezart.domain.errors import MergeConflictError
from kodezart.types.domain.union import UnionCompositionResult, UnionOutcome
from tests.services.test_union_tick import current as current
from tests.services.test_union_tick import repository as repository


async def test_actual_conflict_has_one_entry_without_claiming_a_failed_check(current):
    await current.advance("z", branch="work/z", path="clash.txt")
    await current.advance("a", path="clash.txt")
    tick = current.consumer()
    result = await tick.verify(lane_branches=current.branches)
    assert result.outcome is UnionOutcome.RED
    assert result.checks is None and current.runner.calls == []
    assert result.remediation is not None
    assert result.remediation.merge_conflict == result.merge_conflict
    assert result.remediation.merge_conflict.lane_key == "a"
    assert result.remediation.merge_conflict.paths == ("clash.txt",)
    assert result.remediation.root_step_names == ()
    assert result.remediation.cascade_step_names == ()
    assert result.remediation.detail == (
        "Resolve union merge conflict for lane a in: clash.txt."
    )
    assert (
        UnionCompositionResult.model_validate_json(result.model_dump_json()) == result
    )
    assert not Path(result.scratch_path).exists()
    assert current.git.removed == current.git.created

    assert await tick.verify(lane_branches=current.branches) is result
    assert len(current.git.created) == 1

    repaired = await current.advance("z", path="clash.txt")
    green = await tick.verify(lane_branches=current.branches)
    assert green.outcome is UnionOutcome.GREEN
    assert green.remediation is None and green.merge_conflict is None
    assert green.lane_heads[-1].head_sha == repaired
    assert len(current.runner.calls) == 1
    assert len(current.git.created) == 2
    assert current.git.removed == current.git.created


async def test_cancellation_cannot_become_a_successful_conflict_result(
    current, monkeypatch
):
    await current.advance("z", branch="work/z", path="clash.txt")
    target = await current.advance("a", path="clash.txt")
    entered, release = asyncio.Event(), asyncio.Event()
    native_merge = current.git.merge_scratch_head
    conflicts = []

    async def merge(**arguments):
        if arguments["head_sha"] == target:
            entered.set()
            await release.wait()
        try:
            await native_merge(**arguments)
        except MergeConflictError as exc:
            conflicts.append(exc.paths)
            raise

    monkeypatch.setattr(current.git, "merge_scratch_head", merge)
    tick = current.consumer()
    task = asyncio.create_task(tick.verify(lane_branches=current.branches))
    try:
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert conflicts == [("clash.txt",)]
        assert current.git.removed == current.git.created
        assert current.runner.calls == []
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
