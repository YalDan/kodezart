"""One readable representation of a run-shape alarm record.

The record's address is the complete ``(subject, signal)`` pair: the whole
subject in one canonical spelling, then the signal. A lane key alone would
map two writable surfaces on one issue under ``SURFACE_CONTENDED`` onto one
record, and would not address a scope subject at all.
"""

from collections.abc import Mapping, Sequence

from pydantic import TypeAdapter

from kodezart.domain.comment_markers import (
    compose_comment_marker,
    configured_marker_prefix,
)
from kodezart.domain.errors import DuplicateCommentMarkerError, SurfaceLeaseError
from kodezart.types.domain.run_alarm import (
    AlarmSignal,
    AlarmSubject,
    RunAlarm,
)
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import TrackerComment

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

    The address is composed from what the caller asked for and the body is
    then read under it, so the answer either carries that exact address or
    refuses — the identity check is the marker's own.
    """
    return _record_under(
        body=body,
        marker=run_alarm_marker(
            subject=subject, signal=signal, marker_prefixes=marker_prefixes
        ),
        marker_prefixes=marker_prefixes,
    )


def run_alarm_records(
    *,
    issue_key: str,
    comments: Sequence[TrackerComment],
    marker_prefixes: Mapping[str, str],
) -> tuple[RunAlarm, ...]:
    """Every record this purpose holds on one issue, from one comment listing.

    A tick observing a lane reads a record per signal and per member it
    observes. Composed one address at a time that is a whole comment listing
    per address; composed here it is one listing, and which addresses the
    issue holds is read off the listing rather than guessed before it.

    A comment is a candidate by its marker's PURPOSE alone — the prefix this
    operation configures for these records — and every candidate is then
    read under its own marker, which is where the address it claims is
    checked. Two comments under one marker is the same corruption the single
    read refuses, and it refuses it the same way: one address holds one
    record, and picking either of two would report a record nobody wrote.
    """
    prefix = f"[{configured_marker_prefix(marker_prefixes, purpose=MARKER_PURPOSE)}:"
    found: dict[str, RunAlarm] = {}
    keys: dict[str, str] = {}
    for comment in comments:
        marker = comment.body.partition("\n")[0]
        if not marker.startswith(prefix):
            continue
        if marker in found:
            raise DuplicateCommentMarkerError(
                target=issue_key,
                marker=marker,
                comment_keys=[keys[marker], comment.comment_key],
            )
        found[marker] = _record_under(
            body=comment.body, marker=marker, marker_prefixes=marker_prefixes
        )
        keys[marker] = comment.comment_key
    return tuple(found.values())


def _record_under(
    *, body: str, marker: str, marker_prefixes: Mapping[str, str]
) -> RunAlarm:
    """The one record a body holds under *marker*, or a refusal.

    Three things are checked and each of them says the same sentence about
    this comment: that it is a record of this purpose, written by this
    writer, at this address. The framing is the writer's own; the canonical
    form rejects a payload the writer could not have produced — a repeated
    key, a reordering, an added field — without a second parse; and the
    address is RECOMPOSED from the record's own subject and signal and
    required to be the marker it was found under, so a record moved or
    copied to another address refuses instead of answering for it.
    """
    fence = f"{marker}\n{_FENCE_OPEN}"
    if not body.startswith(fence) or not body.endswith(_FENCE_CLOSE):
        raise ValueError("the alarm record framing is invalid")
    payload = body[len(fence) : -len(_FENCE_CLOSE)]
    alarm = RunAlarm.model_validate_json(payload, strict=True)
    if render_run_alarm(alarm=alarm) != f"{_FENCE_OPEN}{payload}{_FENCE_CLOSE}":
        raise ValueError("the alarm record is not in its canonical form")
    if (
        run_alarm_marker(
            subject=alarm.subject,
            signal=alarm.signal,
            marker_prefixes=marker_prefixes,
        )
        != marker
    ):
        raise ValueError("the alarm record does not carry the address it is under")
    return alarm
