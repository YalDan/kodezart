"""Keep the late-head probe late when validation callbacks are reordered."""

from pathlib import Path

from kodezart.domain.errors import UnionHeadReadError, UnionUnstableError
from tests.chains.test_union_exit_invariance import build_delivery
from tests.services import test_union_composition as pinned


async def test_head_moved_after_actual_composition_is_never_returned_stale(
    tmp_path, monkeypatch
):
    fixture = await build_delivery(tmp_path / "world")
    original = fixture.tracker.work_refs
    pushed_sha = None
    completed_at_push = None

    async def read_refs(*, issue_key):
        nonlocal pushed_sha, completed_at_push
        answer = await original(issue_key=issue_key)
        if fixture.git.removed and pushed_sha is None:
            completed_at_push = tuple(fixture.git.removed)
            await pinned.git(fixture.author, "checkout", "work/z")
            (fixture.author / "after-measurement.txt").write_text("new head\n")
            await pinned.git(fixture.author, "add", "after-measurement.txt")
            await pinned.git(fixture.author, "commit", "-m", "move after real composition")
            await pinned.git(fixture.author, "push", str(fixture.remote), "work/z")
            pushed_sha = await pinned.git(fixture.author, "rev-parse", "HEAD")
        return answer

    monkeypatch.setattr(fixture.tracker, "work_refs", read_refs)
    try:
        result = await fixture.coordinator().verify()
    except (UnionHeadReadError, UnionUnstableError):
        assert pushed_sha is not None
    else:
        assert next(head.head_sha for head in result.lane_heads if head.lane_key == "z") == pushed_sha
    finally:
        assert fixture.git.created == fixture.git.removed
        assert all(not Path(path).exists() for path in fixture.git.created)
    assert completed_at_push

