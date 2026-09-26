"""Actual criterion reads use native keys even when all human text is identical."""

import pytest

from kodezart.chains.criteria import TrackerCriteria
from tests.fakes import FakeMcpIssue
from tests.tracker import test_fire_spec_reader as fixtures
from tests.tracker.conftest import FIRE_ENTRY_LABELS, STATE_TYPES, fixture_server

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
            # Unstarted, so the subject is one a fire can enter: the entry
            # answers the captured spec and the obligation out of one reading.
            status="Todo",
            status_type=STATE_TYPES["Todo"],
        )
    return value


async def test_duplicate_text_and_later_wording_do_not_locate_a_criterion(
    tracker, tracker_writes, seed_issue
):
    before = tracker_writes()
    members = await tracker.read_criteria(issue_key=SUBJECT)
    entry = TrackerCriteria(tracker=tracker)
    first, _ = await entry.read_entry(issue_key=SUBJECT)
    assert len(first.criteria) == 2
    assert first.criteria == tuple(row.issue_key for row in members)
    assert set(first.criteria) == {SECOND, fixtures.CRITERION}
    assert tracker_writes() == before

    seed_issue(issue_key=SECOND, body="**Check:** Completely amended wording.")
    after_edit = tracker_writes()
    second, _ = await entry.read_entry(issue_key=SUBJECT)
    assert second.criteria == first.criteria
    assert second.subject == first.subject == SUBJECT
    assert second.body == first.body == fixtures.BODY
    assert tracker_writes() == after_edit
