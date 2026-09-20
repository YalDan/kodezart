"""The label-as-record roster: who a stage owes its marker to, purely."""

import pytest

from kodezart.domain.organize import owes_stage_label, stage_pending, stage_unlabelled
from tests.fakes import make_tracker_issue

MARKER = "body complete"


def member(key, *labels):
    return make_tracker_issue(key, issue_labels=frozenset(labels))


@pytest.mark.parametrize(
    ("labels", "owes"),
    [
        ((), True),
        (("body complete",), True),
        (("criterion",), False),
        (("tracker",), False),
        (("decision",), True),
        (("decision", "tracker"), False),
    ],
)
def test_who_owes_a_stage_its_label(labels, owes):
    """An escalated member owes the label; a criterion and a record do not."""
    assert owes_stage_label(member("A", *labels)) is owes


@pytest.mark.parametrize("state", ["Todo", "In Progress", "Done", "Canceled"])
def test_an_escalated_member_owes_the_label_whatever_its_workflow_state(state):
    """Removing the escalation label is the act that returns it, not a state move."""
    issue = make_tracker_issue(
        "A", issue_labels=frozenset({"decision"}), state_name=state
    )
    assert owes_stage_label(issue) is True


def test_the_unlabelled_roster_is_in_snapshot_order():
    issues = [
        member("C"),
        member("A", MARKER),
        member("B"),
        member("B/check", "criterion"),
        member("R", "tracker"),
        member("D", "decision"),
    ]
    assert stage_unlabelled(issues=issues, marker=MARKER) == ("C", "B", "D")


@pytest.mark.parametrize("marker", ["", "   "])
def test_a_blank_marker_refuses_a_roster(marker):
    with pytest.raises(ValueError, match="nonempty marker key"):
        stage_unlabelled(issues=[member("A")], marker=marker)


def test_a_repeated_key_refuses_a_roster():
    with pytest.raises(ValueError, match="one record per issue"):
        stage_unlabelled(issues=[member("A"), member("A")], marker=MARKER)


def test_a_run_stage_owes_every_unlabelled_member_whatever_its_admission():
    assert stage_pending(
        unlabelled=("A", "B"), admitted={"A": True, "B": False}, under_approval=True
    ) == ("A", "B")


def test_a_pre_approval_phase_owes_only_the_admitted_members():
    assert stage_pending(
        unlabelled=("A", "B"), admitted={"A": True, "B": False}, under_approval=False
    ) == ("A",)


def test_a_pre_approval_phase_that_admits_nobody_is_not_open_here():
    assert (
        stage_pending(unlabelled=("A",), admitted={"A": False}, under_approval=False)
        is None
    )


def test_a_run_stage_with_nothing_unlabelled_is_open_and_owes_nothing():
    """An empty tuple is not absence: the stage is here, and it is complete."""
    assert stage_pending(unlabelled=(), admitted={}, under_approval=True) == ()
