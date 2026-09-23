"""One readable representation of a run-shape alarm record.

The record's address is the complete ``(subject, signal)`` pair: the whole
subject in one canonical spelling, then the signal. A lane key alone would
map two writable surfaces on one issue under ``SURFACE_CONTENDED`` onto one
record, and would not address a scope subject at all.
"""

from collections.abc import Mapping, Sequence

from pydantic import TypeAdapter

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import SurfaceLeaseError
from kodezart.domain.run_event_stream import LaneRunEvent
from kodezart.types.domain.run_alarm import (
    AlarmSignal,
    AlarmSubject,
    EscalationSubject,
    LaneSubject,
    RunAlarm,
)
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface

_SUBJECT: TypeAdapter[AlarmSubject] = TypeAdapter(AlarmSubject)
_SURFACE = TypeAdapter(WritableSurface)


def alarm_subject_key(subject: AlarmSubject) -> str:
    """One canonical spelling of the complete typed subject at its address boundary."""
    return _SUBJECT.dump_json(subject).decode("utf-8")


def surface_alarm_member_id(surface: WritableSurface) -> str:
    """Canonical source reference; evidence retains the typed surface itself."""
    return _SURFACE.dump_json(surface).decode("utf-8")


MARKER_PURPOSE = "run_alarm"

_FENCE_OPEN = "```json\n"
_FENCE_CLOSE = "\n```"


def run_alarm_marker(
    *,
    subject: AlarmSubject,
    signal: AlarmSignal,
    marker_prefixes: Mapping[str, str],
) -> str:
    """Compose the record's marker from the whole subject and the signal."""
    return compose_comment_marker(
        prefixes=marker_prefixes,
        purpose=MARKER_PURPOSE,
        lane=alarm_subject_key(subject),
        occurrence_key=signal.value,
    )


def run_alarm_surface(*, issue_key: str, marker: str) -> WritableSurface:
    """The one leased address a record at *marker* on *issue_key* occupies.

    One expression, so the writer that takes the lease and the refusal that
    names the missing holder cannot describe two different addresses.
    """
    return WritableSurface(
        kind=SurfaceKind.MARKER_COMMENT,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=issue_key),
        marker=marker,
    )


def require_alarm_holder(*, issue_key: str, marker: str, holder: str | None) -> None:
    """Refuse a leased alarm write that names no holder, before any read.

    The record is kept under a live lease, so the holder is part of the
    call rather than a lease the write may or may not find: an absent or
    blank one is refused here and never reaches the comment upsert, whose
    own holder is optional for the writes that have no lease at all.
    """
    if holder and holder.strip():
        return
    raise SurfaceLeaseError(
        "a leased record write names no holder",
        surface=run_alarm_surface(issue_key=issue_key, marker=marker),
        current_holder=None,
    )


def render_run_alarm(*, alarm: RunAlarm) -> str:
    """The record's content beneath its marker: one explicit JSON object.

    The code block is the sole representation of the alarm in this comment;
    the readings keep the order the signal produced them in.
    """
    return (
        f"{_FENCE_OPEN}{alarm.model_dump_json(by_alias=True, indent=2)}{_FENCE_CLOSE}"
    )


def parse_run_alarm(
    *,
    body: str,
    subject: AlarmSubject,
    signal: AlarmSignal,
    marker_prefixes: Mapping[str, str],
) -> RunAlarm:
    """Read the record stored at this address, refusing anything else.

    A body whose framing, canonical form or carried identity does not match
    the address it was read under is a damaged record, not an alarm with
    repairable fields: reporting a subject other than the one asked for
    would answer a question nobody put.
    """
    marker = run_alarm_marker(
        subject=subject, signal=signal, marker_prefixes=marker_prefixes
    )
    prefix = f"{marker}\n{_FENCE_OPEN}"
    if not body.startswith(prefix) or not body.endswith(_FENCE_CLOSE):
        raise ValueError("the alarm record framing is invalid")
    payload = body[len(prefix) : -len(_FENCE_CLOSE)]
    alarm = RunAlarm.model_validate_json(payload, strict=True)
    if alarm.subject != subject or alarm.signal != signal:
        raise ValueError("the alarm record does not carry the address it is under")
    # Canonical form rejects a payload the writer could not have produced —
    # a repeated key, a reordering, an added field — without a second parse.
    if render_run_alarm(alarm=alarm) != f"{_FENCE_OPEN}{payload}{_FENCE_CLOSE}":
        raise ValueError("the alarm record is not in its canonical form")
    return alarm


#: The stream's whole vocabulary for one alarm: one raise, one clear.
_TRANSITION_KINDS = frozenset(
    {RunEventKind.RUN_ALARM_RAISED, RunEventKind.RUN_ALARM_CLEARED}
)


def alarm_event_key(record: RunAlarm) -> str:
    """The key a record's transition events carry on its lane's stream.

    A lane subject has one record per signal on its lane, so the signal is
    the key, and the tally key streams already carry reads the same. An
    escalation subject has one record per open question, and a lane holds
    several, so the question's own occurrence is part of the key: a raise
    for one question and a clear for another are two streams, not one.
    """
    subject = record.subject
    if isinstance(subject, LaneSubject):
        return record.signal.value
    if isinstance(subject, EscalationSubject) and subject.lane_key is not None:
        return f"{record.signal.value}:{subject.member_id}"
    raise ValueError("an alarm event is keyed to a lane or to a lane's question")


def alarm_event_lane(record: RunAlarm) -> str:
    """The lane whose stream carries *record*'s transition events."""
    subject = record.subject
    if isinstance(subject, LaneSubject | EscalationSubject) and subject.lane_key:
        return subject.lane_key
    raise ValueError("an alarm event is keyed to a lane")


def alarm_event_due(
    *, record: RunAlarm, raised: bool, events: Sequence[LaneRunEvent]
) -> LaneRunEvent | None:
    """The transition event this lane's stream still owes for *record*.

    The owed event is read from the tracker rather than derived from what
    this tick changed, so a tick killed between the record write and the
    event post is repaired by the next one: the record says raised, the
    stream's last word on this record says nothing, and the difference is the
    event to post. A stream already agreeing with the record owes nothing,
    which is what keeps a condition firing across many ticks to one event.

    *raised* is the record's own replayed answer, read by the arm that wrote
    it. Only the two transition kinds are read, and only the entries keyed
    to this record: another signal or another question clearing on the same
    lane is not this one clearing.
    """
    key = alarm_event_key(record)
    lane_key = alarm_event_lane(record)
    spoken = [
        event
        for event in events
        if event.kind in _TRANSITION_KINDS and event.subject_key == key
    ]
    last = spoken[-1] if spoken else None
    if last is None:
        # A stream that has never spoken for this record owes a raise and
        # nothing else: there is no clear to post for an alarm nobody heard.
        if not raised:
            return None
    elif (last.kind is RunEventKind.RUN_ALARM_RAISED) == raised:
        return None
    return LaneRunEvent(
        kind=RunEventKind.RUN_ALARM_RAISED
        if raised
        else RunEventKind.RUN_ALARM_CLEARED,
        lane_key=lane_key,
        subject_key=key,
    )
