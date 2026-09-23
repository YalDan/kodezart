"""A seed_issue edits the fixture issue and is none of the writes a case counts."""

from tests.tracker.conftest import APPROVED_ISSUE


async def test_a_seed_lands_on_the_issue_without_a_write(
    tracker, tracker_writes, seed_issue
):
    before = await tracker.read_issue(issue_key=APPROVED_ISSUE)
    writes = tracker_writes()

    seed_issue(issue_key=APPROVED_ISSUE, title="a seeded title", body="a seeded body")

    after = await tracker.read_issue(issue_key=APPROVED_ISSUE)
    assert (after.title, after.body) == ("a seeded title", "a seeded body")
    assert after.state_name == before.state_name
    assert tracker_writes() == writes


async def test_a_seed_moves_the_issues_stamp_forward(tracker, clock, seed_issue):
    """A seed is an edit, so the stamp an edit moves moves on both arms.

    The double stamps a seed with the clock the case holds, so the clock is
    moved first; the vendor workspace moves the stamp on its own.
    """
    before = await tracker.read_issue(issue_key=APPROVED_ISSUE)
    clock.advance(seconds=60)

    seed_issue(issue_key=APPROVED_ISSUE, body="a seeded body")

    after = await tracker.read_issue(issue_key=APPROVED_ISSUE)
    assert after.updated_at > before.updated_at
