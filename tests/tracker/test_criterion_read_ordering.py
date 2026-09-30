"""Criterion satisfaction is keyed on the sub-issue, never on its position.

The retired body parser read a criterion out of the owning issue's prose,
where a criterion's identity was its row — so moving a row, or rewrapping
the paragraph around it, moved the satisfaction with it.  The native read
is anchored on each sub-issue's own key instead, and the two mutations
that would have broken the old reader are applied here together: the
children are laid out in the opposite order in the backing store, and the
owning issue's prose is reflowed underneath them.  The four children are
given identical titles as well, so a read keyed on a child's text rather
than on its key — a dedupe by title, say — cannot answer either case.
"""

import pytest

from kodezart.chains.criteria import TrackerCriteria
from kodezart.types.domain.tracker_writes import DescriptionEditResult
from tests.fakes import FakeMcpIssue, FakeTrackerPort
from tests.tracker.conftest import FIRE_ENTRY_LABELS, STATE_TYPES, fixture_server

SUBJECT = "subject/7"
LABEL = "acceptance-condition"
BODY = "**Outcome:** The owning prose, stated on one line before any reflow."
REFLOWED = "**Outcome:**\n\nThe owning prose,\nrewrapped\nonto several lines.\n"

#: One satisfaction state per criterion, stated out of key order so a read
#: that echoed the backing layout would not agree with a keyed read even
#: once, let alone across the reorder below.
SATISFACTION = {
    "condition/gamma": "Done",
    "condition/alpha": "Backlog",
    "condition/delta": "Done",
    "condition/beta": "Todo",
}


@pytest.fixture
def server():
    value = fixture_server()
    value.issues[SUBJECT] = FakeMcpIssue(
        id=SUBJECT, labels=FIRE_ENTRY_LABELS, description=BODY
    )
    for key, state in SATISFACTION.items():
        value.issues[key] = FakeMcpIssue(
            id=key,
            parent_id=SUBJECT,
            title="A title that says nothing about position",
            labels=[LABEL],
            description=f"**Check:** {key} is observable.\n\n**Evidence:** —",
            status=state,
            status_type=STATE_TYPES[state],
        )
    return value


def _store(tracker, server):
    """Whichever store the implementation under test actually lists from."""
    return tracker.issues if isinstance(tracker, FakeTrackerPort) else server.issues


def _layout(tracker, server):
    """The order the criterion children are laid out in that store."""
    return tuple(key for key in _store(tracker, server) if key in SATISFACTION)


def _reorder(tracker, server):
    """Re-lay every criterion child in the opposite order."""
    store = _store(tracker, server)
    moved = [(key, store.pop(key)) for key in reversed(_layout(tracker, server))]
    store.update(moved)


async def test_reordered_children_and_reflowed_prose_read_back_identically(
    tracker, server, tracker_writes
):
    subject = await tracker.read_issue(issue_key=SUBJECT)
    before = tuple(await tracker.read_criteria(issue_key=SUBJECT))
    entry = TrackerCriteria(tracker=tracker)
    spec, _ = await entry.read_entry(issue_key=SUBJECT)
    assert {row.issue_key: row.state_name for row in before} == SATISFACTION
    assert spec.criteria == tuple(row.issue_key for row in before)
    laid_out = _layout(tracker, server)

    _reorder(tracker, server)
    assert _layout(tracker, server) == tuple(reversed(laid_out))
    edited = await tracker.edit_description(
        target=SUBJECT, expected=subject.body, replacement=REFLOWED
    )
    assert edited is DescriptionEditResult.EDITED
    assert (await tracker.read_issue(issue_key=SUBJECT)).body == REFLOWED
    written = tracker_writes()

    after = tuple(await tracker.read_criteria(issue_key=SUBJECT))
    assert [row.model_dump_json() for row in after] == [
        row.model_dump_json() for row in before
    ]
    again, _ = await entry.read_entry(issue_key=SUBJECT)
    assert again.criteria == spec.criteria
    assert tracker_writes() == written


async def test_the_read_order_is_the_keyed_order_not_the_stored_one(tracker, server):
    _reorder(tracker, server)
    criteria = await tracker.read_criteria(issue_key=SUBJECT)
    assert [row.issue_key for row in criteria] == sorted(SATISFACTION)
    assert _layout(tracker, server) != tuple(sorted(SATISFACTION))
