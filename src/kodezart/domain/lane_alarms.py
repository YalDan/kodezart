"""Every alarm one lane's observation composes, at that lane's own addresses.

One tick over one lane reads three things — the record at each address, the
lane's own run state, and the lane's event stream — and this module says what
each address should hold afterwards. There is no store behind any of it: a
killed tick loses nothing, because the next one reads the same three facts
and composes the same answers.

What a lane is STANDING at decides which signals are asked, and it is the
walk's own reading of the lane rather than a second opinion about it. A lane
the walk re-derives carries its roster and its gap; a lane owing nothing
carries neither; a lane nothing will re-derive this tick carries neither
either, and is still observed — a criterion whose grading lapsed keeps its
lane's gap open, so the lane holding an undischarged lapse is exactly one
the walk is not re-deriving, and a tick that skipped it could never see one.

Which criterion belongs to which lane is read off a fact rather than
recomputed: a criterion is observed on the lane whose stream accounts for
it, which is by construction a criterion that lane graded, i.e. one in its
subtree. A criterion the stream accounts for whose own record the scope
read does not carry is skipped — its state cannot be read, so nothing is
written and nothing refuses.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from kodezart.domain.run_alarm_table import ALARM_TABLE, alarm_raised
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.domain.stream_signals import ACCOUNT_KINDS
from kodezart.domain.tally_record import next_tally_record
from kodezart.types.domain.run_alarm import (
    AlarmEvidence,
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
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.tracker import TrackerIssue

#: The members this observation folds. Every other member of the signal
#: vocabulary is some other observation's, and a deployment that schedules
#: this tick declares the scans of exactly these.
OBSERVED_ALARMS = frozenset(
    {
        AlarmSignal.TALLY_UNMOVED,
        AlarmSignal.TALLY_REGRESSED,
        AlarmSignal.LAPSE_UNDISCHARGED,
        AlarmSignal.COMPOSITION_SUBSTITUTED,
    }
)

#: The stream's whole vocabulary for an alarm transition: one raise, one clear.
_TRANSITION_KINDS = frozenset(
    {RunEventKind.RUN_ALARM_RAISED, RunEventKind.RUN_ALARM_CLEARED}
)


@dataclass(frozen=True, slots=True)
class Ready:
    """A lane the walk re-derives this tick, with both readings of its subtree."""

    roster: tuple[TrackerIssue, ...]
    gap: tuple[TrackerIssue, ...]


@dataclass(frozen=True, slots=True)
class Waiting:
    """A lane nothing will re-derive this tick: blocked, or unapproved.

    It carries no roster and no gap, and that absence is not the absence a
    finished lane's is: a lane with work left that nothing is going to run
    would look finished if it were read with empty readings, so its tally is
    not composed at all and whatever stands there stands.
    """


@dataclass(frozen=True, slots=True)
class Finished:
    """A lane owing nothing, read with neither roster nor gap.

    A raise standing on a lane that has since finished is cleared rather
    than left, which is why a finished member is observed at all.
    """


type LaneStanding = Ready | Waiting | Finished


def stored_alarm(
    records: Sequence[RunAlarm], *, subject: AlarmSubject, signal: AlarmSignal
) -> RunAlarm | None:
    """The record at one address among every record read off its carrier.

    The address is the whole subject and the signal, as the record's own
    marker is, so two signals over one criterion and one signal over two
    criteria are four addresses and not one.
    """
    for record in records:
        if record.subject == subject and record.signal is signal:
            return record
    return None


def next_alarm_record(
    *, stored: RunAlarm | None, observed: RunAlarm
) -> RunAlarm | None:
    """The record to write at this address, or ``None`` to write nothing.

    One rule: write when what the address SAYS differs from what this tick
    observed, where absence says not raised. A quiet criterion nobody ever
    raised is therefore written nowhere — which is what keeps a healthy walk
    from writing a record per criterion per tick — and a criterion that has
    stopped raising is written its quiet reading once, because a record left
    saying raised is a report of a condition that has ended.

    The tally keeps its own rule, which has a third write this one does not:
    a lane that moved while it still owes work is written a new earlier
    reading so its clock restarts. That write is not a transition and this
    rule could not express it.
    """
    if alarm_raised(stored) == alarm_raised(observed):
        return None
    return observed


def lane_alarm_records(
    *,
    scope_key: str,
    lane_key: str,
    standing: LaneStanding,
    criteria: Sequence[TrackerIssue],
    record: LaneRunState,
    stored: Sequence[RunAlarm],
    events: Sequence[LaneRunEvent],
    max_commits_without_closure: int,
    raised_by: str,
) -> tuple[RunAlarm, ...]:
    """What every address this lane's observation owns should hold after the tick.

    One composition for the whole lane rather than one per signal: the three
    readings are read once and every address is answered from them, so two
    signals about one lane can never disagree about what the board said.

    Everything is read at the lane's own recorded head. That sha is what the
    observation was made at, and it is the same one the tally's clock is
    measured in, so a record and the run state it was composed from can
    always be lined up.

    Whether a node was substituted is read off the stream alone and composed
    at every standing: the openings a lane's evaluations made are facts of
    runs already over, whether or not anything will run the lane again. Its
    raise never clears, because a posted opening is never taken back.
    """
    written: list[RunAlarm] = []
    subject = LaneSubject(scope_key=scope_key, lane_key=lane_key)
    tally = _lane_tally(
        subject=subject,
        standing=standing,
        criteria=criteria,
        record=record,
        stored=stored,
        max_commits_without_closure=max_commits_without_closure,
        raised_by=raised_by,
    )
    if tally is not None:
        written.append(tally)
    substituted = next_alarm_record(
        stored=stored_alarm(
            stored, subject=subject, signal=AlarmSignal.COMPOSITION_SUBSTITUTED
        ),
        observed=_composed(
            signal=AlarmSignal.COMPOSITION_SUBSTITUTED,
            subject=subject,
            readings=(
                _reading(
                    lane_key,
                    RunEventsEvidence(
                        value=tuple(
                            RunEventProjection(
                                kind=event.kind, subject_key=event.subject_key
                            )
                            for event in events
                            if event.kind is RunEventKind.NODE_SESSION_STARTED
                        )
                    ),
                    record.head_sha,
                ),
            ),
            raised_at_sha=record.head_sha,
            raised_by=raised_by,
        ),
    )
    if substituted is not None:
        written.append(substituted)
    written.extend(
        _criterion_alarms(
            scope_key=scope_key,
            lane_key=lane_key,
            standing=standing,
            criteria=criteria,
            record=record,
            stored=stored,
            events=events,
            raised_by=raised_by,
        )
    )
    return tuple(written)


def _lane_tally(
    *,
    subject: LaneSubject,
    standing: LaneStanding,
    criteria: Sequence[TrackerIssue],
    record: LaneRunState,
    stored: Sequence[RunAlarm],
    max_commits_without_closure: int,
    raised_by: str,
) -> RunAlarm | None:
    """The lane's tally address, composed for the standings that can be measured.

    Exhaustive over the standing, so a fourth one cannot be added without
    answering this question for it.
    """
    match standing:
        case Ready(roster=roster, gap=gap):
            pass
        case Finished():
            roster, gap = (), ()
        case Waiting():
            return None
    return next_tally_record(
        subject=subject,
        stored=stored_alarm(stored, subject=subject, signal=AlarmSignal.TALLY_UNMOVED),
        roster=roster,
        gap=gap,
        criteria=criteria,
        record=record,
        max_commits_without_closure=max_commits_without_closure,
        raised_by=raised_by,
    )


def _criterion_alarms(
    *,
    scope_key: str,
    lane_key: str,
    standing: LaneStanding,
    criteria: Sequence[TrackerIssue],
    record: LaneRunState,
    stored: Sequence[RunAlarm],
    events: Sequence[LaneRunEvent],
    raised_by: str,
) -> tuple[RunAlarm, ...]:
    """Every criterion this lane accounted for, at both of its addresses.

    The membership comes off the stream: a criterion this lane said something
    about is one this lane graded. Its current state and its parent come off
    the scope's own criterion reading, which carries every criterion of the
    scope, so a lane nothing is re-deriving has readable criteria too.

    Whether the walk will re-derive the lane is the one thing neither the
    stream nor the record can say, and it is read from the standing: nothing
    on this path takes a claim, so "a lane holding no live claim" is a lane
    this tick's own ready reading does not carry. That is a proxy, and it is
    exactly "not ready on this tick": a ready lane the current walk
    invocation has rested counts as re-derived, because the next invocation
    offers it again, and a ready lane no walk runs at all is not told apart.
    """
    known = {issue.issue_key: issue for issue in criteria}
    projections = tuple(
        RunEventProjection(kind=event.kind, subject_key=event.subject_key)
        for event in events
        if event.kind in ACCOUNT_KINDS and event.subject_key is not None
    )
    rederived = isinstance(standing, Ready)
    written: list[RunAlarm] = []
    for member_id in sorted(
        {projection.subject_key for projection in projections if projection.subject_key}
    ):
        criterion = known.get(member_id)
        if criterion is None or criterion.parent_key is None:
            continue
        subject = CriterionSubject(
            scope_key=scope_key,
            issue_id=criterion.parent_key,
            member_id=member_id,
            lane_key=lane_key,
        )
        account = tuple(
            projection
            for projection in projections
            if projection.subject_key == member_id
        )
        state = _reading(
            member_id, StateEvidence(value=criterion.state_kind), record.head_sha
        )
        stream = _reading(lane_key, RunEventsEvidence(value=account), record.head_sha)
        for signal, readings in (
            (AlarmSignal.TALLY_REGRESSED, (state, stream)),
            (
                AlarmSignal.LAPSE_UNDISCHARGED,
                (
                    state,
                    stream,
                    _reading(
                        lane_key,
                        PresenceEvidence(value=rederived),
                        record.head_sha,
                    ),
                ),
            ),
        ):
            desired = next_alarm_record(
                stored=stored_alarm(stored, subject=subject, signal=signal),
                observed=_composed(
                    signal=signal,
                    subject=subject,
                    readings=readings,
                    raised_at_sha=record.head_sha,
                    raised_by=raised_by,
                ),
            )
            if desired is not None:
                written.append(desired)
    return tuple(written)


def _composed(
    *,
    signal: AlarmSignal,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm:
    """This tick's answer at one address, raised or quiet.

    A quiet answer is a record of the same readings carrying no bound, so the
    address can say "observed, and not raised" in the same terms it says the
    other thing — and replaying either reaches the same answer again.
    """
    raised = ALARM_TABLE[signal].fold(
        subject=subject,
        readings=readings,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
    return raised or RunAlarm(
        subject=subject,
        signal=signal,
        readings=readings,
        bound=None,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )


def _reading(source_ref: str, value: AlarmEvidence, at_sha: str) -> AlarmReading:
    """One reading of this observation, all of them read at the lane's own head."""
    return AlarmReading(source_ref=source_ref, value=value, at_sha=at_sha)


def alarm_event_due(
    *, record: RunAlarm, events: Sequence[LaneRunEvent]
) -> LaneRunEvent | None:
    """The transition event this lane's stream still owes for *record*.

    The owed event is read from the tracker rather than derived from what
    this tick changed, so a tick killed between the record write and the
    event post is repaired by the next one: the record says raised, the
    stream's last word on this signal says nothing, and the difference is the
    event to post. A stream already agreeing with the record owes nothing,
    which is what keeps a condition firing across many ticks to one event.

    Only a lane-subject record is announced at all. The stream's entries are
    keyed to the signal and not to the member, so a criterion-subject record
    has nowhere on it to be keyed and announcing it would be one event per
    criterion where the transition is the lane's.

    Only this signal's own two kinds are read, and only the entries keyed to
    it: another signal clearing on the same lane is not this one clearing.
    """
    if not isinstance(record.subject, LaneSubject):
        raise ValueError("an alarm transition event is keyed to a lane")
    spoken = [
        event
        for event in events
        if event.kind in _TRANSITION_KINDS and event.subject_key == record.signal.value
    ]
    raised = alarm_raised(record)
    last = spoken[-1] if spoken else None
    if last is None:
        # A stream that has never spoken for this signal owes a raise and
        # nothing else: there is no clear to post for an alarm nobody heard.
        if not raised:
            return None
    elif (last.kind is RunEventKind.RUN_ALARM_RAISED) == raised:
        return None
    return LaneRunEvent(
        kind=RunEventKind.RUN_ALARM_RAISED
        if raised
        else RunEventKind.RUN_ALARM_CLEARED,
        lane_key=record.subject.lane_key,
        subject_key=record.signal.value,
    )
