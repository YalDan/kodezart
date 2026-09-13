"""One readable representation of a run-shape alarm record.

The record's address is the complete ``(subject, signal)`` pair: the whole
subject in one canonical spelling, then the signal. A lane key alone would
map two writable surfaces on one issue under ``SURFACE_CONTENDED`` onto one
record, and would not address a scope subject at all.
"""

from collections.abc import Mapping

from pydantic import TypeAdapter

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.types.domain.run_alarm import (
    AlarmSignal,
    AlarmSubject,
    RunAlarm,
)
from kodezart.types.domain.surface import WritableSurface

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
