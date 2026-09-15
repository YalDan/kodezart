"""Regression and lapse readings over one criterion sub-issue's own state.

A criterion's satisfaction is the sub-issue's state, so both readings here
are over state MOVES rather than over a tally kept somewhere else. The two
are deliberately separate signals: a move back to an unstarted state
abandons a graded result, while a move back to the configured review stage
is the loop carrying the criterion round again, and collapsing them would
report ordinary re-derivation as a regression.
"""

from kodezart.domain.run_shape import (
    read_alarm_value,
    unique_membership,
    unreadable_reading,
)
from kodezart.types.domain.operation import LifecycleStage
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    CriterionStateMove,
    CriterionSubject,
    GraphEvidence,
    PresenceEvidence,
    RunAlarm,
    RunEventsEvidence,
    StateMoveEvidence,
)
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind


def _criterion_move(
    subject: AlarmSubject, move_reading: AlarmReading, signal: AlarmSignal
) -> tuple[CriterionSubject, CriterionStateMove]:
    """Read the one criterion sub-issue's move both signals are answers about.

    Three identities have to be the same criterion: the subject the answer
    is reported against, the key the move was read at, and the key the move
    itself carries. A move read at another sub-issue's key is that
    sub-issue's move whatever the subject claims, and a subject of any
    other kind names no criterion at all. The criterion subject is returned
    beside the move, because everything read after this point is read about
    that one criterion.
    """
    move = read_alarm_value(move_reading, StateMoveEvidence, signal)
    if (
        subject.kind is not AlarmSubjectKind.CRITERION
        or subject.member_id != move.member_id
    ):
        raise unreadable_reading(
            signal, move_reading.source_ref, "subject identifies another criterion"
        )
    if move_reading.source_ref != move.member_id:
        raise unreadable_reading(
            signal, move_reading.source_ref, "the move identifies another criterion"
        )
    return subject, move


def tally_regressed(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Observe a criterion leaving a completed state unaccompanied.

    Two typed readings: the sub-issue's own state move, sourced at its
    key, and the lane's posted events, sourced at the lane the subject
    names. A move that does not leave a completed state is quiet, and so
    is a move into any state that is not an unstarted one: a move to the
    configured review stage is a lapse the criterion can be carried back
    from, and re-deriving it is the loop's ordinary work.

    The move is discharged by an event of the refuted kind keyed to that
    same sub-issue key. An event keyed to nothing is addressed to the lane
    as a whole and discharges no member's move; one keyed to another
    member discharges that member's. No event prose is read here, and the
    order the events were recorded in does not change the answer.
    """
    signal = AlarmSignal.TALLY_REGRESSED
    try:
        move_reading, events_reading = readings
    except ValueError as exc:
        raise unreadable_reading(
            signal, subject.scope_key, "incomplete readings"
        ) from exc
    criterion_subject, move = _criterion_move(subject, move_reading, signal)
    events = read_alarm_value(events_reading, RunEventsEvidence, signal)
    if (
        criterion_subject.lane_key is None
        or events_reading.source_ref != criterion_subject.lane_key
    ):
        raise unreadable_reading(
            signal,
            events_reading.source_ref,
            "the event stream identifies another lane",
        )
    if (
        move.from_kind is not WorkflowStateKind.COMPLETED
        or move.to_kind is not WorkflowStateKind.UNSTARTED
    ):
        return None
    if any(
        event.kind is RunEventKind.CRITERION_REFUTED
        and event.subject_key == move.member_id
        for event in events
    ):
        return None
    return RunAlarm(
        subject=criterion_subject,
        signal=signal,
        readings=readings,
        bound=None,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )


def _subtree_owner(
    *,
    lane_readings: list[AlarmReading],
    member_id: str,
    signal: AlarmSignal,
) -> tuple[str, TrackerIssue, dict[str, bool]]:
    """Resolve the one lane whose recorded subtree holds *member_id*.

    Each lane contributes two readings in order: its complete membership
    snapshot and the completed lookup of whether it holds a live claim,
    both sourced at that lane's own key. Membership is the snapshot's own
    complete read, so a criterion hanging from a deliverable child is
    still the lane's; the direct parent recorded beside it never stands
    in for the lane. Exactly one subtree may hold the criterion.
    """
    holders: dict[str, bool] = {}
    owners: dict[str, TrackerIssue] = {}
    for graph_reading, claim_reading in zip(
        lane_readings[::2], lane_readings[1::2], strict=True
    ):
        snapshot = read_alarm_value(graph_reading, GraphEvidence, signal)
        holds_claim = read_alarm_value(claim_reading, PresenceEvidence, signal)
        if (
            graph_reading.source_ref != snapshot.lane_key
            or claim_reading.source_ref != snapshot.lane_key
        ):
            raise unreadable_reading(
                signal, graph_reading.source_ref, "lane readings identify other lanes"
            )
        if snapshot.lane_key in holders:
            raise unreadable_reading(
                signal, snapshot.lane_key, "one lane is observed more than once"
            )
        holders[snapshot.lane_key] = holds_claim
        members = unique_membership(snapshot.subtree, graph_reading, signal)
        held = members.get(member_id)
        if held is not None:
            owners[snapshot.lane_key] = held
    if len(owners) != 1:
        raise unreadable_reading(
            signal, member_id, "no single lane subtree holds this criterion"
        )
    lane_key, criterion = next(iter(owners.items()))
    return lane_key, criterion, holders


def lapse_undischarged(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Observe a lapsed criterion whose owning lane holds no live claim.

    The first reading is the criterion sub-issue's own state move, sourced
    at its key; the rest are one pair per observed lane — that lane's
    membership snapshot and its completed live-claim lookup. A move that
    is not a lapse, a completed state to the configured review stage, is
    quiet here: a move out to an unstarted state belongs to the regression
    arm, and this signal never restates it.

    The owning lane is the lane in whose subtree the criterion sits, not
    the issue it hangs from. A criterion under a deliverable child is the
    lane's, and resolving ownership to the direct parent would key the
    alarm on a claim that parent never holds. A lapse is quiet while its
    lane still holds a live claim, because that lane is what re-derives
    it; with the claim gone, nothing remains that would.
    """
    signal = AlarmSignal.LAPSE_UNDISCHARGED
    try:
        move_reading, *lane_readings = readings
    except ValueError as exc:
        raise unreadable_reading(
            signal, subject.scope_key, "incomplete readings"
        ) from exc
    if not lane_readings or len(lane_readings) % 2:
        raise unreadable_reading(signal, subject.scope_key, "incomplete lane readings")
    criterion_subject, move = _criterion_move(subject, move_reading, signal)
    lane_key, criterion, holders = _subtree_owner(
        lane_readings=lane_readings, member_id=move.member_id, signal=signal
    )
    if criterion.state_kind is not move.to_kind:
        raise unreadable_reading(
            signal, lane_key, "the membership read disagrees about the criterion"
        )
    if (
        criterion.parent_key is None
        or criterion_subject.issue_id != criterion.parent_key
    ):
        raise unreadable_reading(
            signal, lane_key, "subject identifies another owning issue"
        )
    if (
        criterion_subject.lane_key is not None
        and criterion_subject.lane_key != lane_key
    ):
        raise unreadable_reading(signal, lane_key, "subject identifies another lane")
    if (
        move.from_kind is not WorkflowStateKind.COMPLETED
        or move.to_stage is not LifecycleStage.IN_REVIEW
    ):
        return None
    if holders[lane_key]:
        return None
    return RunAlarm(
        subject=criterion_subject,
        signal=signal,
        readings=readings,
        bound=None,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
