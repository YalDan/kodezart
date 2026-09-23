"""Gap membership over an issue's SUBTREE criteria: one arm per tracker state.

The gap is not read off a criterion's parent, its Evidence prose or a
grading vocabulary of its own — it is a function of the criterion record's
tracker state and nothing else, taken over the whole subtree beneath the
issue that owes it.  This table states what each state does, one row per
member of the state enum, and asserts the table covers the enum exactly:
a state added tomorrow has no arm here until somebody writes one, exactly
as the pure predicate has no ``else`` to fall into.

Both readings that are NOT graded — a criterion nobody ever graded and one
that lapsed back out of Done — are owed identically.  Neither is refuted,
and no fourth state separates them: what separates them is the graded sha
the lapse left behind in its Evidence row, which the gap preserves and
never reads.
"""

import pytest

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.domain.criterion_evidence import (
    parse_criterion_evidence,
    render_evidence_field,
)
from kodezart.domain.errors import ScopePlanRefusalError
from kodezart.domain.fire_spec import criterion_field_bodies
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind
from tests.chains.test_scope_ready import LABELS, PROJECT, row
from tests.chains.test_scope_ready import ready_fixture as ready_fixture

LANE = "lane"
NESTED = "nested"
DEEP_CHECK = "deep-check"

GRADED_SHA = "c" * 40
GRADED_TEST = "tests/chains/test_scope_gap_membership.py::test_case"

#: What the subtree read does with the one criterion under test.
OWED = "owed"
DISCHARGED = "discharged"
EXCLUDED = "excluded"
REFUSED_BACKLOG = "refused_backlog"

#: One arm per tracker state, and nothing else may be written here: the
#: identity assertion below is what turns "one per state" into a fact.
#: ``refuted`` is deliberately not a member — a criterion nobody graded is
#: owed, which is what the two OWED rows over graded and ungraded records
#: below demonstrate.
MEMBERSHIP: dict[WorkflowStateKind, str] = {
    WorkflowStateKind.TRIAGE: OWED,
    WorkflowStateKind.BACKLOG: REFUSED_BACKLOG,
    WorkflowStateKind.UNSTARTED: OWED,
    WorkflowStateKind.STARTED: OWED,
    WorkflowStateKind.COMPLETED: DISCHARGED,
    WorkflowStateKind.CANCELED: EXCLUDED,
    WorkflowStateKind.DUPLICATE: EXCLUDED,
}


def graded_body(sha: str) -> str:
    """A criterion body whose Evidence row records one complete grading."""
    return "**Check:** The contract.\n**Do:** The mechanism.\n" + render_evidence_field(
        CriterionEvidence(graded_sha=sha, test=GRADED_TEST)
    )


#: A criterion nobody has graded: the template's Evidence row, unfilled.
UNGRADED_BODY = "**Check:** The contract.\n**Do:** The mechanism.\n**Evidence:** —"


def subtree(*, kind: str, body: str):
    """A lane whose own check is graded and whose CHILD holds the criterion.

    Nested one level down so the reading under test is the subtree's, never
    a lane's own criterion family: what an issue owes is what everything
    beneath it owes.
    """
    rows = [
        row(LANE),
        row("lane-check", parent=LANE, label="criterion", kind="completed"),
        row(NESTED, parent=LANE),
        row(DEEP_CHECK, parent=NESTED, label="criterion", kind=kind),
    ]
    rows[-1].description = body
    return rows


def test_the_membership_table_carries_one_arm_per_tracker_state():
    """A state the enum gains has no arm here until somebody writes one."""
    assert set(MEMBERSHIP) == set(WorkflowStateKind)
    assert set(MEMBERSHIP.values()) == {
        OWED,
        DISCHARGED,
        EXCLUDED,
        REFUSED_BACKLOG,
    }


@pytest.mark.parametrize("state", list(WorkflowStateKind))
async def test_one_gap_arm_per_criterion_state_over_the_subtree(
    ready_fixture, state: WorkflowStateKind
) -> None:
    """The table, executed: every state of the enum, the same board shape."""
    fixture = await ready_fixture(subtree(kind=state.value, body=UNGRADED_BODY))
    expected = MEMBERSHIP[state]

    if expected == REFUSED_BACKLOG:
        with pytest.raises(ScopePlanRefusalError) as backlog:
            await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)
        assert backlog.value.backlog_criteria == (DEEP_CHECK,)
        return
    selection = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)

    owed = {
        lane.issue.issue_key: [item.issue_key for item in lane.gap]
        for lane in selection.ready
    }
    if expected == OWED:
        assert owed == {LANE: [DEEP_CHECK], NESTED: [DEEP_CHECK]}
    else:
        assert owed == {}
    if expected == EXCLUDED:
        assert selection.excluded == (DEEP_CHECK,)
    else:
        assert selection.excluded == ()
    fixture.assert_read_only()


async def test_supersession_prose_on_an_owed_criterion_keeps_it_on_every_gap(
    ready_fixture,
) -> None:
    """A body naming a supersession is prose: the unstarted state still owes it."""
    fixture = await ready_fixture(subtree(kind="unstarted", body="Superseded by X-1"))

    selection = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)

    owed = {
        lane.issue.issue_key: [item.issue_key for item in lane.gap]
        for lane in selection.ready
    }
    assert owed == {LANE: [DEEP_CHECK], NESTED: [DEEP_CHECK]}
    assert selection.excluded == ()
    assert DEEP_CHECK in selection.unresolved
    fixture.assert_read_only()


#: Labels named like the two excluding states and a supersession, configured
#: on the adapter so the read carries them onto the criterion record.
EXTRA_LABELS = {name: f"tag/{name}" for name in ("superseded", "canceled", "duplicate")}


async def test_labels_named_like_an_exclusion_leave_an_owed_criterion_on_every_gap(
    ready_fixture,
) -> None:
    """Membership is on state alone: an unstarted criterion's labels exclude nothing."""
    rows = subtree(kind="unstarted", body=UNGRADED_BODY)
    rows[-1].labels = [*rows[-1].labels, *EXTRA_LABELS.values()]
    fixture = await ready_fixture(rows, labels={**LABELS, **EXTRA_LABELS})

    selection = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)

    owed = {
        lane.issue.issue_key: [item.issue_key for item in lane.gap]
        for lane in selection.ready
    }
    assert owed == {LANE: [DEEP_CHECK], NESTED: [DEEP_CHECK]}
    assert selection.excluded == ()
    (deep,) = (item for item in selection.criteria if item.issue_key == DEEP_CHECK)
    assert deep.issue_labels == frozenset({"criterion", *EXTRA_LABELS})
    fixture.assert_read_only()


async def test_a_criterion_moved_back_from_done_is_owed_again_with_its_graded_sha(
    ready_fixture,
) -> None:
    """The lapse: discharged one tick, owed the next, its grading preserved."""
    fixture = await ready_fixture(
        subtree(kind="completed", body=graded_body(GRADED_SHA))
    )
    assert (await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)).ready == ()

    fixture.state(DEEP_CHECK, "unstarted")
    selection = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)

    (owed,) = selection.ready[0].gap
    assert owed.issue_key == DEEP_CHECK
    assert owed.state_kind is WorkflowStateKind.UNSTARTED
    assert parse_criterion_evidence(owed.body).graded_sha == GRADED_SHA
    fixture.assert_read_only()


async def test_a_criterion_never_graded_is_owed_with_no_graded_sha_and_not_refuted(
    ready_fixture,
) -> None:
    """No grading on record is a criterion still owed, never one knocked down.

    The record reaches the gap exactly as the tracker holds it — no verdict
    is attached to it, and no state of it is read as anything but the state
    the board holds.
    """
    fixture = await ready_fixture(subtree(kind="unstarted", body=UNGRADED_BODY))

    selection = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)

    (owed,) = selection.ready[0].gap
    assert owed == await fixture.tracker.read_issue(issue_key=DEEP_CHECK)
    assert owed.state_kind is WorkflowStateKind.UNSTARTED
    with pytest.raises(ValueError, match="Evidence"):
        parse_criterion_evidence(owed.body)
    fixture.assert_read_only()


async def test_the_two_ungraded_readings_are_told_apart_by_the_sha_alone(
    ready_fixture,
) -> None:
    """Never graded and lapsed from graded: one state, one membership, one sha.

    Read side by side because that is the claim — that the arithmetic has
    no fourth state for the lapse, and the only thing that distinguishes
    the two records is what the earlier grading left in the Evidence row.
    """
    lapsed = await ready_fixture(
        subtree(kind="completed", body=graded_body(GRADED_SHA))
    )
    lapsed.state(DEEP_CHECK, "unstarted")
    never = await ready_fixture(subtree(kind="unstarted", body=UNGRADED_BODY))

    (from_lapse,) = (
        (await read_scope_ready(ref=PROJECT, tracker=lapsed.tracker)).ready[0].gap
    )
    (from_nothing,) = (
        (await read_scope_ready(ref=PROJECT, tracker=never.tracker)).ready[0].gap
    )

    assert from_lapse.state_kind is from_nothing.state_kind
    assert from_lapse.state_name == from_nothing.state_name
    assert from_lapse.issue_key == from_nothing.issue_key == DEEP_CHECK
    assert parse_criterion_evidence(from_lapse.body).graded_sha == GRADED_SHA
    with pytest.raises(ValueError, match="Evidence"):
        parse_criterion_evidence(from_nothing.body)
    assert from_lapse != from_nothing


#: The grading the phantom's Evidence row records: a sha and a test id no
#: criterion on any board here carries, so a grading read out of a parent's
#: body cannot pass for the criterion's own.
PHANTOM_SHA = "d" * 40
PHANTOM_TEST = "tests/chains/test_scope_gap_membership.py::phantom"

#: A parent body shaped like a criterion checklist, naming a criterion that
#: exists nowhere on the board. A walk that read criteria out of a parent's
#: description would mint it; the sub-issue read cannot. Beside the checkbox
#: and the ``AC-n`` line it carries rows in the live template grammar — a
#: Check row and a graded Evidence row at column 0 — which the one sanctioned
#: field reader does read, so a fallback that mints or grades through that
#: reader out of a parent's body has something here to find.
PHANTOM_CHECKLIST = (
    "- [ ] **Check:** phantom\n"
    "AC-9 phantom\n"
    "**Check:** phantom\n\n"
    + render_evidence_field(
        CriterionEvidence(graded_sha=PHANTOM_SHA, test=PHANTOM_TEST)
    )
    + "\n"
)

#: An ungraded criterion directly under the lane, beside the graded one: a
#: fallback that graded a lane's own criterion out of the lane's body would
#: have this one to grade.
LANE_OPEN = "lane-open"


def graded_sha_or_none(issue: TrackerIssue) -> str | None:
    """The graded sha a criterion's own Evidence row records, or None ungraded."""
    try:
        return parse_criterion_evidence(issue.body).graded_sha
    except ValueError:
        return None


@pytest.mark.parametrize(
    "lane_body", [PHANTOM_CHECKLIST, ""], ids=["checklist", "no-checklist"]
)
async def test_the_walk_reads_each_criterion_as_its_sub_issue_and_no_parent_body(
    ready_fixture, monkeypatch, lane_body: str
) -> None:
    """Key, label, state and Evidence come off the criterion sub-issues alone.

    The lane has one criterion Done and graded and one open and never
    graded, the deep one is open and was never graded, and the description
    of the lane and of the deliverable parent above the deep one is either a
    checklist naming a criterion that does not exist or nothing at all.
    Either way the read returns the same three sub-issues with the same four
    facts, never reads a body of a row that is not a criterion, leaves both
    parents' bodies as they are, and owes only the open ones.

    The trap's reach: it sees ``body`` read as an attribute of a
    ``TrackerIssue``. A read through ``__dict__``, ``vars()`` or
    ``model_dump()``, pydantic's own ``__eq__``, and an adapter's read of its
    wire model's description before any ``TrackerIssue`` exists are outside
    it. Those are held by the parent's live-grammar rows, which no read may
    turn into a criterion, a grading or a key, and by the tree-wide scan in
    ``tests/domain/test_criterion_body_scan_sites.py``.
    """
    if lane_body:
        # The live-grammar rows really are readable by the sanctioned reader,
        # so a fallback through it would find a Check and an Evidence here.
        assert criterion_field_bodies(lane_body, field="Check") != ()
        assert criterion_field_bodies(lane_body, field="Evidence") != ()
    rows = subtree(kind="unstarted", body=UNGRADED_BODY)
    rows[0].description = lane_body
    rows[1].description = graded_body(GRADED_SHA)
    rows[2].description = lane_body
    rows.append(row(LANE_OPEN, parent=LANE, label="criterion", kind="unstarted"))
    rows[-1].description = UNGRADED_BODY
    fixture = await ready_fixture(rows)
    body_reads: list[str] = []
    original = TrackerIssue.__getattribute__

    def trapped(issue: TrackerIssue, name: str) -> object:
        if name == "body" and "criterion" not in original(issue, "issue_labels"):
            body_reads.append(original(issue, "issue_key"))
        return original(issue, name)

    with monkeypatch.context() as patch:
        patch.setattr(TrackerIssue, "__getattribute__", trapped)
        selection = await read_scope_ready(ref=PROJECT, tracker=fixture.tracker)

    assert body_reads == []
    (lane,) = (item for item in selection.ready if item.issue.issue_key == LANE)
    assert lane.issue.body == lane_body
    (nested,) = (item for item in selection.ready if item.issue.issue_key == NESTED)
    assert nested.issue.body == lane_body
    assert [
        (
            criterion.issue_key,
            criterion.issue_labels,
            criterion.state_kind,
            graded_sha_or_none(criterion),
        )
        for criterion in lane.criteria
    ] == [
        (
            "lane-check",
            frozenset({"criterion"}),
            WorkflowStateKind.COMPLETED,
            GRADED_SHA,
        ),
        (DEEP_CHECK, frozenset({"criterion"}), WorkflowStateKind.UNSTARTED, None),
        (LANE_OPEN, frozenset({"criterion"}), WorkflowStateKind.UNSTARTED, None),
    ]
    # The ungraded row is read as exactly the row the board holds, not merely
    # as a row no grading could be parsed out of.
    (deep,) = (item for item in lane.criteria if item.issue_key == DEEP_CHECK)
    assert criterion_field_bodies(deep.body, field="Evidence") == ("—",)
    assert [criterion.issue_key for criterion in selection.criteria] == [
        "lane-check",
        DEEP_CHECK,
        LANE_OPEN,
    ]
    assert [criterion.issue_key for criterion in lane.gap] == [DEEP_CHECK, LANE_OPEN]
    fixture.assert_read_only()
