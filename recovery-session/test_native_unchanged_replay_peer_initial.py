"""A repeatable no-change session never duplicates committed effects on replay."""

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.chains.criteria import TrackerCriteria
from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.errors import FireSpecEntryError
from kodezart.types.domain.native_execution import PreparedNativeExecution
from tests.chains.test_native_fire import tracker
from tests.chains.test_native_parent_resume import actual_fire, prepared_parent
from tests.services.test_native_amendments import Executor, git, repository

__all__ = ["repository"]


async def test_unchanged_session_replay_preserves_git_history_without_duplicate_effects(
    repository, monkeypatch
):
    port, saver = tracker(), InMemorySaver()
    after_writer = False
    actual_scan = port.scope_issues

    async def outage_after_writer(**kwargs):
        if after_writer:
            raise ConnectionError(
                "current authority unavailable after completed SDK result"
            )
        return await actual_scan(**kwargs)

    monkeypatch.setattr(port, "scope_issues", outage_after_writer)

    async def no_change_then_outage(title, payload, kwargs):
        nonlocal after_writer
        if title == "NativeWriterOutput":
            Path(kwargs["cwd"], "change.py").unlink()
            after_writer = True

    executor = Executor(claim=False, mutate=no_change_then_outage)
    fire, workspace, spec, config = await prepared_parent(
        repository, executor, port, saver
    )
    fresh_workspace = None
    try:
        with pytest.raises((NativeWriteRefusalError, FireSpecEntryError)):
            await fire.native_graph.ainvoke(None, config=config)
        writers = [
            row
            for row in executor.calls
            if row.get("output_format", {}).get("schema", {}).get("title")
            == "NativeWriterOutput"
        ]
        assert len(writers) == 1
        original_path = writers[0]["cwd"]
        assert Path(original_path).exists()
        phase = next(
            item.checkpoint["channel_values"]["execution"]
            for item in saver.list(None)
            if "execution" in item.checkpoint["channel_values"]
        )
        assert isinstance(phase, PreparedNativeExecution)
        assert not await git(repository[0], "ls-remote", "origin", "refs/heads/native-loop")
        assert await git(repository[0], "rev-list", "--count", "main..native-loop") == "0"
        after_writer = False

        async def no_change_and_grade(title, payload, kwargs):
            if title == "NativeWriterOutput":
                Path(kwargs["cwd"], "change.py").unlink()
            if title == "AcceptanceCriteriaOutput":
                current = await TrackerCriteria(tracker=port).read_current(spec=spec)
                payload.update(
                    criteria_results=[
                        {
                            "criterion_id": c.id,
                            "criterion": c.text,
                            "passed": True,
                            "reasoning": "Exact obligation.",
                        }
                        for c in current.criteria
                    ],
                    sherlock_flags=[],
                )

        second = Executor(claim=False, mutate=no_change_and_grade)
        fresh, fresh_workspace = await actual_fire(repository, second, port, saver)
        try:
            await fresh.native_graph.ainvoke(
                None, config=config, interrupt_after=["run_ralph_loop"]
            )
        except NativeWriteRefusalError:
            pass
        titles = [
            row.get("output_format", {}).get("schema", {}).get("title")
            for row in second.calls
        ]
        assert titles == ["NativeWriterOutput", "AcceptanceCriteriaOutput"]
        assert await git(repository[0], "rev-list", "--count", "main..native-loop") == "0"
        remote = await git(repository[0], "ls-remote", "origin", "refs/heads/native-loop")
        assert remote.split()[0] == repository[1]
        assert not port.comments
        assert not fresh_workspace._workspaces
    finally:
        after_writer = False
        for owner in (workspace, fresh_workspace):
            if owner is not None:
                for path in tuple(owner._workspaces):
                    if Path(path).exists():
                        await owner.release(path)
                    else:
                        owner._workspaces.pop(path, None)
