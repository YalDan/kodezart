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
