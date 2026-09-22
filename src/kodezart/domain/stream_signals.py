"""Signals read off a lane's own event stream.

Two of them read a criterion's own state and its lane's account of it
together, because neither fact answers on its own. A criterion out of its
finished state says nothing about who moved it or why; a lane's stream says
what the lane did and nothing about where the criterion stands now. Both
readings come from the lane that graded the criterion: the state from the
criterion's own record, the account from the stream of the lane whose
subtree it sits in.

The account is the LAST of the three things a lane can say about a
criterion it graded — it crossed it off, it refuted it, or it found the
grading lapsed — so a criterion graded twice is read by what the lane said
the second time. A criterion its lane never accounted for is quiet
everywhere here: nothing in this module infers an account from a state.

The third reads the stream alone: the session openings the harness observed
each node invocation make, counted against what that invocation declared.
"""

from collections.abc import Sequence

from kodezart.domain.gap import open_state_kind
from kodezart.domain.run_shape import (
    read_alarm_value,
    unreadable_reading,
)
from kodezart.types.domain.node_session import NodeInvocation, NodeSessionKey
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    CriterionSubject,
    LaneSubject,
    PresenceEvidence,
    RunAlarm,
    RunEventProjection,
    RunEventsEvidence,
    StateEvidence,
)
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.tracker import WorkflowStateKind

#: Everything a lane says about a criterion it graded, and nothing else. A
#: kind outside this set is another subject's business even when it is keyed
#: to this criterion, so the account is read from these three alone.
ACCOUNT_KINDS = (
    RunEventKind.ISSUE_CROSSED_OFF,
    RunEventKind.CRITERION_REFUTED,
    RunEventKind.CRITERION_LAPSED,
)


def _account(projections: Sequence[RunEventProjection]) -> RunEventKind | None:
    """The last thing the lane said about this criterion, or nothing.

    The last and not the first: a criterion crossed off and later refuted is
    refuted, and one refuted and then crossed off again is crossed off. The
    projections arrive in the order the backend recorded them, so the last
    of them is the lane's current word.
    """
    accounts = [
        projection.kind
        for projection in projections
        if projection.kind in ACCOUNT_KINDS
    ]
    return accounts[-1] if accounts else None


def _criterion_readings(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, AlarmReading],
    signal: AlarmSignal,
) -> tuple[CriterionSubject, WorkflowStateKind, tuple[RunEventProjection, ...]]:
    """The pair every criterion signal here reads, validated once.

    Both signals read the same two things about the same criterion, so both
    read them through here: a second copy of these checks would be a second
    opinion about which addresses a criterion observation may be composed
    from.

    The addresses are the point of the validation. The state is sourced at
    the criterion, because a state read at the lane would be the lane's own
    state; the account is sourced at the LANE, because a criterion is
    observed on the lane whose stream accounts for it, which is by
    construction the lane in whose subtree it sits. A projection keyed to
    another member is another criterion's account, and its presence among
    these readings means the collector composed the wrong address.
    """
    state_reading, events_reading = readings
    if not isinstance(subject, CriterionSubject) or subject.lane_key is None:
        raise unreadable_reading(
            signal, subject.scope_key, "a criterion signal names no observing lane"
        )
    if state_reading.source_ref != subject.member_id:
        raise unreadable_reading(
            signal, state_reading.source_ref, "the state reading names another record"
        )
    if events_reading.source_ref != subject.lane_key:
        raise unreadable_reading(
            signal, events_reading.source_ref, "the account reading names another lane"
        )
    state = read_alarm_value(state_reading, StateEvidence, signal)
    projections = read_alarm_value(events_reading, RunEventsEvidence, signal)
    for projection in projections:
        if (
            projection.kind not in ACCOUNT_KINDS
            or projection.subject_key != subject.member_id
        ):
            raise unreadable_reading(
                signal,
                events_reading.source_ref,
                "an account reading carries another subject's event",
            )
    return subject, state, projections


def tally_regressed(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """A criterion moved out of its finished state that its lane never took back.

    Two readings: the criterion's current workflow kind, and its lane's
    account of it. A criterion the lane crossed off and that now stands
    unstarted moved out of the state that grading left it in, and the lane's
    stream carries no refutation and no lapse for it — so whatever moved it
    moved it silently, which is the regression.

    Every other reading is quiet, and each for its own reason. A refutation
    is the lane reporting the same move itself, which is an account and not
    a silent one. A lapse is the grading no longer standing rather than the
    criterion being taken back against it — a lapse is not a regression. A
    criterion the lane never accounted for was never this signal's to read.
    And any state other than unstarted is not the move: a criterion handed
    on for grading has left its finished state without being taken back to
    owed, and the account for that move is the next grading's to give.

    No bound: the reading is an identity and a kind, not a count, so there
    is no threshold to configure and nothing to measure against one.
    """
    signal = AlarmSignal.TALLY_REGRESSED
    try:
        state_reading, events_reading = readings
    except ValueError as exc:
        raise unreadable_reading(
            signal, subject.scope_key, "incomplete criterion readings"
        ) from exc
    criterion, state, projections = _criterion_readings(
        subject=subject, readings=(state_reading, events_reading), signal=signal
    )
    if (
        _account(projections) is not RunEventKind.ISSUE_CROSSED_OFF
        or state is not WorkflowStateKind.UNSTARTED
    ):
        return None
    return RunAlarm(
        subject=criterion,
        signal=signal,
        readings=readings,
        bound=None,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )


def lapse_undischarged(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """A lapsed criterion on a lane nothing is going to re-derive it from.

    Three readings: the criterion's current workflow kind, its lane's
    account of it, and whether that lane is one the walk re-derives on this
    tick. A lapse is the grading that finished a criterion no longer
    standing, so the criterion is owed again and the lane it belongs to is
    the one that will grade it — unless nothing will run that lane, in which
    case the lapse is owed by nobody and that is what this reports.

    The presence reading is sourced at the LANE and never at the criterion's
    parent issue: the lane that announced the lapse is the one whose subtree
    the criterion sits in, and a descendant criterion's parent may be a
    deliverable child that fires nothing. Keyed to the parent, the signal
    would ask about a carrier that was never going to grade anything.

    Quiet in three ways. A criterion whose account is anything but a lapse
    is not lapsed. A criterion the lapse left closed — moved on since, by
    the next grading — owes nothing. And a lane the walk re-derives will
    grade the criterion again, so the lapse discharges itself.
    """
    signal = AlarmSignal.LAPSE_UNDISCHARGED
    try:
        state_reading, events_reading, presence_reading = readings
    except ValueError as exc:
        raise unreadable_reading(
            signal, subject.scope_key, "incomplete criterion readings"
        ) from exc
    criterion, state, projections = _criterion_readings(
        subject=subject, readings=(state_reading, events_reading), signal=signal
    )
    if presence_reading.source_ref != criterion.lane_key:
        raise unreadable_reading(
            signal,
            presence_reading.source_ref,
            "the re-derivation reading names another lane",
        )
    rederived = read_alarm_value(presence_reading, PresenceEvidence, signal)
    if (
        _account(projections) is not RunEventKind.CRITERION_LAPSED
        or not open_state_kind(state, supersession_ref=None)
        or rederived
    ):
        return None
    return RunAlarm(
        subject=criterion,
        signal=signal,
        readings=readings,
        bound=None,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )


def composition_substituted(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """A node invocation that opened more sessions than it declared.

    One reading: the lane's own stream, projected to its node-session
    openings. Each opening is keyed to the whole invocation it belongs to
    and the session it opened, so the count per invocation and the count
    that invocation declared are both read off the same reading — the
    record replays from itself and no literal is compared against. A node
    declared as a fan-out of n that opened n is quiet; one declared as a
    single session that opened two was substituted by something else.

    No bound: the declared count is a fact of the invocation, not a
    configured threshold, so there is no configuration field to name.
    """
    signal = AlarmSignal.COMPOSITION_SUBSTITUTED
    try:
        (stream,) = readings
    except ValueError as exc:
        raise unreadable_reading(
            signal, subject.scope_key, "incomplete session readings"
        ) from exc
    if not isinstance(subject, LaneSubject):
        raise unreadable_reading(
            signal, subject.scope_key, "a substitution is observed on a lane"
        )
    if stream.source_ref != subject.lane_key:
        raise unreadable_reading(
            signal, stream.source_ref, "the session reading names another lane"
        )
    opened: dict[NodeInvocation, set[str]] = {}
    for projection in read_alarm_value(stream, RunEventsEvidence, signal):
        if (
            projection.kind is not RunEventKind.NODE_SESSION_STARTED
            or projection.subject_key is None
        ):
            raise unreadable_reading(
                signal, stream.source_ref, "a session reading carries another event"
            )
        try:
            key = NodeSessionKey.model_validate_json(projection.subject_key)
        except ValueError as exc:
            raise unreadable_reading(
                signal, stream.source_ref, "a session opening names no invocation"
            ) from exc
        opened.setdefault(key.invocation, set()).add(key.session_id)
    if not any(
        len(sessions) > invocation.declared_sessions
        for invocation, sessions in opened.items()
    ):
        return None
    return RunAlarm(
        subject=subject,
        signal=signal,
        readings=readings,
        bound=None,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
