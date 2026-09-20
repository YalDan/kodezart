"""One record per lane tally address: what the next tick should leave there.

Every expected outcome is written out here rather than derived from the rule
under test, so the table of record decisions and the code are compared and not
merely both read off one expression.
"""

import pytest

from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.domain.run_shape import COMMITS_WITHOUT_CLOSURE_BOUND, tally_unmoved
from kodezart.domain.tally_record import (
    alarm_event_due,
    anchor_of,
    is_raised,
    lane_start,
    next_tally_record,
)
from kodezart.types.domain.branch import BranchAssociation, BranchRole
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    CountEvidence,
    LaneSubject,
    LaneTally,
    ReferencesEvidence,
    RunAlarm,
    TallyEvidence,
)
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.run_state import LaneCommit, LaneRunState
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import make_tracker_issue

SCOPE_KEY = "scoped-project"
LANE = LaneSubject(scope_key=SCOPE_KEY, lane_key="LANE-1")
BOUND = 1
HOLDER = "kodezart/supervisor"
HEAD = "a" * 40
FIRST = "c-one"
SECOND = "c-two"


def criterion(key, *, closed=False, parent=LANE.lane_key):
    return make_tracker_issue(
        key,
        parent_key=parent,
        issue_labels=frozenset({"criterion"}),
        state_name="Done" if closed else "Todo",
        state_kind=WorkflowStateKind.COMPLETED
        if closed
        else WorkflowStateKind.UNSTARTED,
    )


def lane_record(*, commits=()):
    """The lane's run-state record, as the committing loop leaves it."""
    loop = f"kodezart/{LANE.lane_key}-loop"
    deliverable = f"kodezart/{LANE.lane_key}"
    return LaneRunState(
        lane_key=LANE.lane_key,
        branch=loop,
        branch_url=f"https://forge.invalid/{loop}",
        head_sha=HEAD,
        pushed_head_sha=HEAD,
        commits_ahead=len(commits),
        files_changed=len(commits),
        commits=[
            LaneCommit(sha=sha, subject="feat: one", issue_id=LANE.lane_key)
            for sha in commits
        ],
        body_digest=None,
        associations=[
            BranchAssociation(
                branch=deliverable,
                role=BranchRole.DELIVERABLE,
                derived_from="trunk",
                run_id="first-job",
            ),
            BranchAssociation(
                branch=loop,
                role=BranchRole.LOOP,
                derived_from=deliverable,
                run_id="first-job",
            ),
        ],
    )


def stored_record(*, anchor, latest, closed=(), bound=BOUND):
    """A record already at the address, composed the way a tick composes one."""
    readings = (
        AlarmReading(
            source_ref=LANE.lane_key, value=TallyEvidence(value=anchor), at_sha=HEAD
        ),
        AlarmReading(
            source_ref=LANE.lane_key, value=TallyEvidence(value=latest), at_sha=HEAD
        ),
        AlarmReading(
            source_ref=LANE.lane_key,
            value=ReferencesEvidence(value=tuple(closed)),
            at_sha=HEAD,
        ),
        AlarmReading(
            source_ref=COMMITS_WITHOUT_CLOSURE_BOUND,
            value=CountEvidence(value=bound),
            at_sha=HEAD,
        ),
    )
    raised = tally_unmoved(
        subject=LANE, readings=readings, raised_at_sha=HEAD, raised_by=HOLDER
    )
    return raised or RunAlarm(
        subject=LANE,
        signal=AlarmSignal.TALLY_UNMOVED,
        readings=readings,
        bound=None,
        raised_at_sha=HEAD,
        raised_by=HOLDER,
    )


def tally(open_keys, commits=()):
    return LaneTally(open=tuple(open_keys), commits=tuple(commits))


#: The roster both criteria of the lane are read from, and the two readings of
#: it a tick makes: everything owed, and the part of it still open.
ROSTER = (criterion(FIRST), criterion(SECOND))
BOTH_OPEN = (criterion(FIRST), criterion(SECOND))
ONE_OPEN = (criterion(FIRST),)

#: The record already at the address for each row of the table below.
NOTHING_STORED = None
QUIET_STORED = stored_record(anchor=tally((FIRST, SECOND)), latest=tally((FIRST,)))
RAISED_STORED = stored_record(
    anchor=tally((FIRST, SECOND)),
    latest=tally((FIRST, SECOND), ("sha-one", "sha-two")),
)


def test_the_stored_fixtures_are_the_two_states_the_table_names():
    """The table's two stored states, asserted rather than assumed."""
    assert not is_raised(QUIET_STORED)
    assert is_raised(RAISED_STORED)


@pytest.mark.parametrize(
    ("row", "stored", "gap", "commits", "writes", "raised_after"),
    [
        (
            "none/not moved, within the bound",
            NOTHING_STORED,
            BOTH_OPEN,
            ("sha-one",),
            False,
            False,
        ),
        (
            "none/not moved, over the bound, work open",
            NOTHING_STORED,
            BOTH_OPEN,
            ("sha-one", "sha-two"),
            True,
            True,
        ),
        ("none/moved, work open", NOTHING_STORED, ONE_OPEN, ("sha-one",), True, False),
        ("none/moved, nothing open", NOTHING_STORED, (), ("sha-one",), False, False),
        ("quiet/moved, nothing open", QUIET_STORED, (), ("sha-one",), False, False),
        (
            "quiet/not moved, within the bound",
            QUIET_STORED,
            BOTH_OPEN,
            ("sha-one",),
            False,
            False,
        ),
        ("quiet/moved, work open", QUIET_STORED, ONE_OPEN, ("sha-one",), True, False),
        (
            "quiet/not moved, over the bound, work open",
            QUIET_STORED,
            BOTH_OPEN,
            ("sha-one", "sha-two"),
            True,
            True,
        ),
        (
            "raised/not moved, still over the bound",
            RAISED_STORED,
            BOTH_OPEN,
            ("sha-one", "sha-two"),
            False,
            True,
        ),
        (
            "raised/nothing open",
            RAISED_STORED,
            (),
            ("sha-one", "sha-two"),
            True,
            False,
        ),
        (
            "raised/moved, work open",
            RAISED_STORED,
            ONE_OPEN,
            ("sha-one", "sha-two"),
            True,
            False,
        ),
        (
            "raised/now within the bound",
            RAISED_STORED,
            BOTH_OPEN,
            ("sha-one",),
            True,
            False,
        ),
    ],
)
def test_each_row_of_the_record_table(row, stored, gap, commits, writes, raised_after):
    desired = next_tally_record(
        subject=LANE,
        stored=stored,
        roster=ROSTER,
        gap=gap,
        criteria=ROSTER,
        record=lane_record(commits=commits),
        max_commits_without_closure=BOUND,
        raised_by=HOLDER,
    )

    assert (desired is not None) == writes, row
    assert is_raised(desired if writes else stored) == raised_after, row


def test_a_raise_names_its_configured_bound_and_the_work_it_observed():
    desired = next_tally_record(
        subject=LANE,
        stored=None,
        roster=ROSTER,
        gap=BOTH_OPEN,
        criteria=ROSTER,
        record=lane_record(commits=("sha-one", "sha-two")),
        max_commits_without_closure=BOUND,
        raised_by=HOLDER,
    )

    assert desired is not None
    assert desired.bound is not None
    assert desired.bound.config_field == COMMITS_WITHOUT_CLOSURE_BOUND
    assert desired.bound.configured_value == BOUND
    assert desired.bound.observed_value == 2
    assert desired.raised_at_sha == HEAD
    assert desired.raised_by == HOLDER


def test_a_record_showing_closed_work_anchors_the_next_tick_at_its_latest_reading():
    moved = next_tally_record(
        subject=LANE,
        stored=None,
        roster=ROSTER,
        gap=ONE_OPEN,
        criteria=ROSTER,
        record=lane_record(commits=("sha-one",)),
        max_commits_without_closure=BOUND,
        raised_by=HOLDER,
    )

    assert moved is not None
    assert anchor_of(moved) == tally((FIRST,), ("sha-one",))
    # A record that saw no movement keeps the anchor it already carries, so a
    # stall stays measurable however many ticks it lasts.
    assert anchor_of(RAISED_STORED) == tally((FIRST, SECOND))


def test_a_second_stall_is_measured_from_the_reading_the_movement_left():
    moved = next_tally_record(
        subject=LANE,
        stored=None,
        roster=ROSTER,
        gap=ONE_OPEN,
        criteria=ROSTER,
        record=lane_record(commits=("sha-one",)),
        max_commits_without_closure=BOUND,
        raised_by=HOLDER,
    )

    raised = next_tally_record(
        subject=LANE,
        stored=moved,
        roster=ROSTER,
        gap=ONE_OPEN,
        criteria=ROSTER,
        record=lane_record(commits=("sha-one", "sha-two", "sha-three")),
        max_commits_without_closure=BOUND,
        raised_by=HOLDER,
    )

    assert raised is not None
    assert raised.bound is not None
    assert raised.bound.observed_value == 2


def test_the_lane_start_holds_the_whole_roster_and_no_commit():
    assert lane_start(ROSTER) == tally((FIRST, SECOND))
    assert lane_start(()) == tally(())


def test_a_closure_under_a_deliverable_child_is_movement():
    """The lane owes its whole subtree, so a closure anywhere in it moved."""
    nested = criterion("c-nested", parent="LANE-1/deliverable")
    roster = (criterion(FIRST), nested)

    desired = next_tally_record(
        subject=LANE,
        stored=None,
        roster=roster,
        gap=(criterion(FIRST),),
        criteria=roster,
        record=lane_record(commits=("sha-one", "sha-two", "sha-three")),
        max_commits_without_closure=BOUND,
        raised_by=HOLDER,
    )

    assert desired is not None
    assert not is_raised(desired)
    assert desired.readings[2].value.value == ("c-nested",)


def test_a_stored_record_whose_replay_disagrees_with_its_bound_refuses():
    claimed = RAISED_STORED.model_copy(update={"bound": None})

    with pytest.raises(RunShapeReadError, match="replays to a bound"):
        is_raised(claimed)

    with pytest.raises(RunShapeReadError, match="replays to a bound"):
        next_tally_record(
            subject=LANE,
            stored=claimed,
            roster=ROSTER,
            gap=BOTH_OPEN,
            criteria=ROSTER,
            record=lane_record(commits=("sha-one", "sha-two")),
            max_commits_without_closure=BOUND,
            raised_by=HOLDER,
        )


def event(kind, *, subject_key=AlarmSignal.TALLY_UNMOVED.value):
    return LaneRunEvent(kind=kind, lane_key=LANE.lane_key, subject_key=subject_key)


def test_the_event_due_is_read_from_this_signals_own_events():
    """Another signal's entries on the same lane are not this signal speaking.

    Both directions have to be read, because an unkeyed reader is wrong both
    ways: another signal's raise would answer for this signal's owed raise,
    and another signal's clear would answer for its owed clear. Only entries
    keyed to this signal count, so a foreign raise leaves the owed raise owed
    and a foreign clear leaves the owed clear owed.
    """
    foreign_raised = event(
        RunEventKind.RUN_ALARM_RAISED, subject_key="escalation_ageing"
    )

    due = alarm_event_due(record=RAISED_STORED, events=(foreign_raised,))

    assert due is not None
    assert due.kind is RunEventKind.RUN_ALARM_RAISED
    assert due.lane_key == LANE.lane_key
    assert due.subject_key == AlarmSignal.TALLY_UNMOVED.value
    # The stream already agreeing with the record owes nothing, however many
    # ticks the condition goes on firing for.
    assert alarm_event_due(record=RAISED_STORED, events=(foreign_raised, due)) is None

    foreign_cleared = event(
        RunEventKind.RUN_ALARM_CLEARED, subject_key="escalation_ageing"
    )

    owed_clear = alarm_event_due(
        record=QUIET_STORED,
        events=(event(RunEventKind.RUN_ALARM_RAISED), foreign_cleared),
    )

    assert owed_clear is not None
    assert owed_clear.kind is RunEventKind.RUN_ALARM_CLEARED
    assert owed_clear.lane_key == LANE.lane_key
    assert owed_clear.subject_key == AlarmSignal.TALLY_UNMOVED.value


def test_an_event_of_another_kind_keyed_to_this_signal_is_not_this_signal_speaking():
    """The stream's last word is read from this signal's own two kinds only.

    An entry of another kind may carry this signal's subject key, and a reader
    that took the last such entry whatever its kind would hear it as a clear:
    a standing raise would look unanswered and would be posted twice, and a
    record that has since gone quiet would look already cleared.
    """
    raised_here = event(RunEventKind.RUN_ALARM_RAISED)
    another_kind = event(RunEventKind.LANE_PLATEAUED)

    assert (
        alarm_event_due(record=RAISED_STORED, events=(raised_here, another_kind))
        is None
    )

    owed_clear = alarm_event_due(
        record=QUIET_STORED, events=(raised_here, another_kind)
    )

    assert owed_clear is not None
    assert owed_clear.kind is RunEventKind.RUN_ALARM_CLEARED
    assert owed_clear.lane_key == LANE.lane_key
    assert owed_clear.subject_key == AlarmSignal.TALLY_UNMOVED.value


@pytest.mark.parametrize(
    ("record", "posted", "owed"),
    [
        (RAISED_STORED, (), RunEventKind.RUN_ALARM_RAISED),
        (QUIET_STORED, (), None),
        (RAISED_STORED, (RunEventKind.RUN_ALARM_RAISED,), None),
        (
            QUIET_STORED,
            (RunEventKind.RUN_ALARM_RAISED,),
            RunEventKind.RUN_ALARM_CLEARED,
        ),
        (
            QUIET_STORED,
            (RunEventKind.RUN_ALARM_RAISED, RunEventKind.RUN_ALARM_CLEARED),
            None,
        ),
        (
            RAISED_STORED,
            (RunEventKind.RUN_ALARM_RAISED, RunEventKind.RUN_ALARM_CLEARED),
            RunEventKind.RUN_ALARM_RAISED,
        ),
    ],
)
def test_the_stream_owes_a_transition_only_where_it_disagrees(record, posted, owed):
    due = alarm_event_due(record=record, events=tuple(event(kind) for kind in posted))

    assert (None if due is None else due.kind) == owed
