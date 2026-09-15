"""A native head move during the coordinator's final tracker reread."""

from pathlib import Path

import pytest

from kodezart.domain.errors import UnionHeadReadError, UnionUnstableError
from tests.chains.test_union_exit_invariance import build_delivery
from tests.services import test_union_composition as pinned


@pytest.mark.parametrize("move_head", [False, True], ids=["unchanged", "head-moved"])
async def test_result_is_current_after_final_tracker_roster_read(
    tmp_path, monkeypatch, move_head
):
    fixture = await build_delivery(tmp_path / "world")
    original = fixture.tracker.work_refs
    calls = []
    pushed_sha = None

    async def read_refs(*, issue_key):
        nonlocal pushed_sha
        answer = await original(issue_key=issue_key)
        calls.append(issue_key)
        # Two participant refs were read to compose the union. The next
        # read is after UnionTick has completed its final remote-head check.
        if move_head and len(calls) == 3:
            await pinned.git(fixture.author, "checkout", "work/z")
            (fixture.author / "later.txt").write_text("another actor's new head\n")
            await pinned.git(fixture.author, "add", "later.txt")
            await pinned.git(
                fixture.author, "commit", "-m", "head moved during roster read"
            )
            await pinned.git(fixture.author, "push", str(fixture.remote), "work/z")
            pushed_sha = await pinned.git(fixture.author, "rev-parse", "HEAD")
        return answer

    monkeypatch.setattr(fixture.tracker, "work_refs", read_refs)
    try:
        result = await fixture.coordinator().verify()
    except (UnionHeadReadError, UnionUnstableError):
        assert move_head and pushed_sha is not None
    else:
        remote_sha = await pinned.git(fixture.remote, "rev-parse", "refs/heads/work/z")
        observed = next(
            head.head_sha for head in result.lane_heads if head.lane_key == "z"
        )
        assert observed == remote_sha, (
            "the union result became stale during its awaited final tracker reread",
            observed,
            remote_sha,
            calls,
        )
    finally:
        assert fixture.git.created == fixture.git.removed
        assert all(not Path(path).exists() for path in fixture.git.created)
    assert len(calls) >= 4
