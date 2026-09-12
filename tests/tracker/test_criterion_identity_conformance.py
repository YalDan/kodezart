"""Actual criterion reads use native keys even when all human text is identical."""

import pytest

from tests.fakes import FakeMcpIssue
from tests.tracker import test_fire_spec_reader as fixtures
from tests.tracker.conftest import FIRE_ENTRY_LABELS, fixture_server

SUBJECT = fixtures.SUBJECT
SECOND = "condition/second"
SAME = "**Check:** The identical text describes two separately owned conditions."


@pytest.fixture
def server():
    value = fixture_server()
    value.issues[SUBJECT] = FakeMcpIssue(
        id=SUBJECT, labels=FIRE_ENTRY_LABELS, description=fixtures.BODY
    )
    for key in (SECOND, fixtures.CRITERION):
        value.issues[key] = FakeMcpIssue(
            id=key,
            parent_id=SUBJECT,
            title="Identical criterion title",
            labels=[fixtures.LABEL],
            description=SAME,
        )
    return value


async def test_duplicate_text_and_later_wording_do_not_locate_a_criterion(
    tracker, tracker_writes
):
    before = tracker_writes()
    members = await tracker.read_criteria(issue_key=SUBJECT)
    first = await tracker.read_fire_spec(issue_key=SUBJECT)
    assert len(first.criteria) == 2
    assert first.criteria == tuple(row.issue_key for row in members)
    assert set(first.criteria) == {SECOND, fixtures.CRITERION}
    assert tracker_writes() == before

    await tracker.update_issue(
        issue_key=SECOND, body="**Check:** Completely amended wording."
    )
    after_edit = tracker_writes()
    second = await tracker.read_fire_spec(issue_key=SUBJECT)
    assert second.criteria == first.criteria
    assert second.subject == first.subject == SUBJECT
    assert second.body == first.body == fixtures.BODY
    assert tracker_writes() == after_edit
