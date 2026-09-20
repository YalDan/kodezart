"""What the one alarm record at a lane's tally address should say next.

The record is the whole memory of this signal. There is no store and no
process state behind it: a tick reads the record at the address, composes
what the address should hold given what the tracker says now, and writes
only when the two differ. A killed tick therefore loses nothing — the next
one reads the same two facts and reaches the same answer.

Whether a record is an alarm is decided by replaying its own readings, never
by asking whether it carries a bound: the scope arm of the same signal
raises with no bound at all, so a bound's presence answers a different
question. A record whose readings replay to something other than what it
claims was written by another arm at this address, and refuses.
"""

from collections.abc import Sequence

from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.fire_plateau import closed_previous_work, observe_tick
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.domain.run_shape import (
    COMMITS_WITHOUT_CLOSURE_BOUND,
    read_alarm_value,
    tally_unmoved,
)
from kodezart.types.domain.run_alarm import (
    AlarmEvidence,
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
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.tracker import TrackerIssue

#: The stream's whole vocabulary for this signal: one raise, one clear.
_TRANSITION_KINDS = frozenset(
    {RunEventKind.RUN_ALARM_RAISED, RunEventKind.RUN_ALARM_CLEARED}
)


def is_raised(record: RunAlarm | None) -> bool:
    """Whether *record*'s own readings still replay to an alarm.

    Absence is not raised, and neither is a record kept only so the next tick
    has an earlier reading to measure from. The replay is the answer because
    it is the same arithmetic the raise was made by, and asking whether the
    record carries a bound would answer a different question: the scope arm
    of this same signal raises with none.

    The bound is still read, as a consistency check rather than the answer. A
    record whose replay does not reproduce the bound it carries — a threshold
    it never crossed, a bound on a record that replays to nothing — was
    written by something other than this arithmetic, and reading it either
    way would report a threshold nobody measured.
    """
    if record is None:
        return False
    replayed = tally_unmoved(
        subject=record.subject,
        readings=record.readings,
        raised_at_sha=record.raised_at_sha,
        raised_by=record.raised_by,
    )
    if record.bound != (None if replayed is None else replayed.bound):
        raise RunShapeReadError(
            signal=record.signal.value,
            source_ref=record.raised_at_sha,
            reason="the stored record replays to a bound it does not carry",
        )
    return replayed is not None


def anchor_of(record: RunAlarm) -> LaneTally:
    """The reading the next tick measures from, read off *record*.

    A record written on a tick that saw the lane close something anchors at
    that tick's own reading: the bound counts what the lane has recorded
    SINCE it last moved, so movement restarts the measurement. A record that
    saw no movement keeps the anchor it already carries, which is what makes
    a stall measurable across any number of ticks.
    """
    is_raised(record)
    anchor_reading, latest_reading, closed_reading, _ = record.readings
    signal = record.signal
    if read_alarm_value(closed_reading, ReferencesEvidence, signal):
        return read_alarm_value(latest_reading, TallyEvidence, signal)
    return read_alarm_value(anchor_reading, TallyEvidence, signal)


def lane_start(roster: Sequence[TrackerIssue]) -> LaneTally:
    """The reading a lane with no record stands at: everything owed, nothing done.

    A lane nothing was ever written about has recorded no commit, so the
    bound is measured from zero and the first tick that finds it already past
    the bound says so. Standing the start in is not a guess about the past: a
    lane with no record has no recorded past to read.
    """
    return LaneTally(
        open=tuple(sorted(issue.issue_key for issue in roster)), commits=()
    )


def next_tally_record(
    *,
    subject: LaneSubject,
    stored: RunAlarm | None,
    roster: Sequence[TrackerIssue],
    gap: Sequence[TrackerIssue],
    criteria: Sequence[TrackerIssue],
    record: LaneRunState,
    max_commits_without_closure: int,
    raised_by: str,
) -> RunAlarm | None:
    """What the address should hold after this tick, or ``None`` to write nothing.

    Three writes exist and no others: a raise, a clear, and a new earlier
    reading for a lane that moved while it still owes work. The last one is
    what keeps a busy lane from being measured against a reading it left
    behind, and it is the only write a run that never stalls makes.

    A tick over unchanged state composes the record that is already there and
    writes nothing, so replay costs its reads and no write.
    """
    stored_raised = is_raised(stored)
    anchor = anchor_of(stored) if stored is not None else lane_start(roster)
    closed = tuple(
        sorted(
            closed_previous_work(
                observe_tick(
                    previously_open=anchor.open,
                    subtree_criteria=criteria,
                    open_criteria=gap,
                )
            )
        )
    )
    latest = LaneTally(
        open=tuple(sorted(issue.issue_key for issue in gap)),
        commits=tuple(commit.sha for commit in record.commits),
    )
    readings = (
        _reading(subject.lane_key, TallyEvidence(value=anchor), record.head_sha),
        _reading(subject.lane_key, TallyEvidence(value=latest), record.head_sha),
        _reading(subject.lane_key, ReferencesEvidence(value=closed), record.head_sha),
        _reading(
            COMMITS_WITHOUT_CLOSURE_BOUND,
            CountEvidence(value=max_commits_without_closure),
            record.head_sha,
        ),
    )
    raise_now = tally_unmoved(
        subject=subject,
        readings=readings,
        raised_at_sha=record.head_sha,
        raised_by=raised_by,
    )
    desired = raise_now or RunAlarm(
        subject=subject,
        signal=AlarmSignal.TALLY_UNMOVED,
        readings=readings,
        bound=None,
        raised_at_sha=record.head_sha,
        raised_by=raised_by,
    )
    if (raise_now is not None) != stored_raised:
        return desired
    if closed and latest.open:
        return desired
    return None


def _reading(source_ref: str, value: AlarmEvidence, at_sha: str) -> AlarmReading:
    """One reading of this arm, all four of which are read at the record's head."""
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

    Only this signal's own two kinds are read, and only the entries keyed to
    it: another signal clearing on the same lane is not this one clearing.
    """
    if not isinstance(record.subject, LaneSubject):
        raise ValueError("a tally alarm event is keyed to a lane")
    spoken = [
        event
        for event in events
        if event.kind in _TRANSITION_KINDS and event.subject_key == record.signal.value
    ]
    raised = is_raised(record)
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
