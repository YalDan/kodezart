"""One readable representation of a run-shape alarm record.

The record's address is the complete ``(subject, signal)`` pair: the whole
subject in one canonical spelling, then the signal. A lane key alone would
map two writable surfaces on one issue under ``SURFACE_CONTENDED`` onto one
record, and would not address a scope subject at all.
"""

from collections.abc import Mapping

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.fenced_record import parse_fenced_record, render_fenced_record
from kodezart.types.domain.run_alarm import (
    AlarmSignal,
    AlarmSubject,
    RunAlarm,
    alarm_subject_key,
)

MARKER_PURPOSE = "run_alarm"


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
    """The alarm's content beneath its marker, readings in recorded order."""
    return render_fenced_record(alarm)


def parse_run_alarm(
    *,
    body: str,
    subject: AlarmSubject,
    signal: AlarmSignal,
    marker_prefixes: Mapping[str, str],
) -> RunAlarm:
    """Read the record stored at this address, refusing anything else.

    A body carrying an identity other than the address it was read under is
    a damaged record, not an alarm with repairable fields: answering with
    some other subject's alarm would answer a question nobody put.
    """
    alarm = parse_fenced_record(
        body=body,
        marker=run_alarm_marker(
            subject=subject, signal=signal, marker_prefixes=marker_prefixes
        ),
        model=RunAlarm,
    )
    if alarm.subject != subject or alarm.signal != signal:
        raise ValueError("the alarm record does not carry the address it is under")
    return alarm
