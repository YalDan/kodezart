"""Every alarm signal, the one function that folds it, and what it scans.

One table, keyed by the signal vocabulary itself, with two columns: the pure
fold that answers the signal, and the tracker scans its readings are
collected through, stated in the vocabulary the boot probe already asks.
Whether the table is total is checked once, at boot, by
:func:`require_alarm_table`; nothing at runtime asks whether a signal has a
fold or a capability, because a deployment that could not answer one never
starts.

The same fold is how a stored record is replayed: whether a record IS an
alarm is decided by the arithmetic the raise was made by, for every signal
alike, never by whether the record carries a bound — most signals raise
with none.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.mandate_graph import (
    rulings_outpace_closures,
    structural_write_uncrosses_milestone,
)
from kodezart.domain.run_shape import (
    barren_tick_with_diff_growth,
    commits_ahead_of_record,
    escalation_ageing,
    record_superseded,
    surface_contended,
    tally_unmoved,
    write_back_missing,
)
from kodezart.domain.stream_signals import (
    composition_substituted,
    lapse_undischarged,
    tally_regressed,
)
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    RunAlarm,
)


class SignalFunction(Protocol):
    """The one shape every fold has: readings in, an alarm or nothing out."""

    def __call__(
        self,
        *,
        subject: AlarmSubject,
        readings: tuple[AlarmReading, ...],
        raised_at_sha: str,
        raised_by: str,
    ) -> RunAlarm | None: ...


@dataclass(frozen=True, slots=True)
class AlarmFold:
    """One row: the fold, and the scans its readings are collected through."""

    fold: SignalFunction
    scans: frozenset[PassSignal]


#: The issue listing: rosters, criterion states and stage markers all come
#: through it, so a signal reading any of them needs the credential to
#: answer it.
_ISSUE_LISTING = frozenset({PassSignal.issues_changed})
#: Comment and record reads only, which no boot probe asks about.
_RECORD_READS: frozenset[PassSignal] = frozenset()

ALARM_TABLE: Mapping[AlarmSignal, AlarmFold] = {
    AlarmSignal.TALLY_UNMOVED: AlarmFold(tally_unmoved, _ISSUE_LISTING),
    AlarmSignal.TALLY_REGRESSED: AlarmFold(tally_regressed, _ISSUE_LISTING),
    AlarmSignal.LAPSE_UNDISCHARGED: AlarmFold(lapse_undischarged, _ISSUE_LISTING),
    AlarmSignal.ESCALATION_AGEING: AlarmFold(escalation_ageing, _RECORD_READS),
    AlarmSignal.WRITE_BACK_MISSING: AlarmFold(write_back_missing, _RECORD_READS),
    AlarmSignal.SURFACE_CONTENDED: AlarmFold(surface_contended, _RECORD_READS),
    AlarmSignal.RECORD_SUPERSEDED: AlarmFold(record_superseded, _RECORD_READS),
    AlarmSignal.COMPOSITION_SUBSTITUTED: AlarmFold(
        composition_substituted, _RECORD_READS
    ),
    AlarmSignal.BARREN_TICK_WITH_DIFF_GROWTH: AlarmFold(
        barren_tick_with_diff_growth, _RECORD_READS
    ),
    AlarmSignal.COMMITS_AHEAD_OF_RECORD: AlarmFold(
        commits_ahead_of_record, _RECORD_READS
    ),
    AlarmSignal.RULINGS_OUTPACE_CLOSURES: AlarmFold(
        rulings_outpace_closures, _ISSUE_LISTING
    ),
    AlarmSignal.STRUCTURAL_WRITE_UNCROSSES_MILESTONE: AlarmFold(
        structural_write_uncrosses_milestone, _ISSUE_LISTING
    ),
}


class AlarmTableError(Exception):
    """A signal of the vocabulary has no fold, so the deployment cannot start."""

    def __init__(self, missing: tuple[AlarmSignal, ...]) -> None:
        super().__init__(
            "alarm signals with no fold: "
            + ", ".join(signal.value for signal in missing)
        )
        self.missing = missing


def require_alarm_table() -> None:
    """Refuse to boot while any signal of the vocabulary has no fold.

    Read at call time rather than at import, so the check is of the table
    the process will actually run with. Every missing member is named at
    once, in the vocabulary's own order.
    """
    missing = tuple(signal for signal in AlarmSignal if signal not in ALARM_TABLE)
    if missing:
        raise AlarmTableError(missing)


def alarm_raised(record: RunAlarm | None) -> bool:
    """Whether *record*'s own readings still replay to an alarm.

    Absence is not raised, and neither is a record kept only so the address
    says the condition has ended or so the next tick has an earlier reading
    to measure from. The replay is the answer because it is the same
    arithmetic the raise was made by.

    The bound is still read, as a consistency check rather than the answer:
    a record whose replay does not reproduce the bound it carries — a
    threshold it never crossed, a bound on a record that replays to nothing
    — was written by something other than this arithmetic, and reading it
    either way would report a threshold nobody measured.

    A record whose signal has no row raises ``KeyError`` rather than being
    answered: the boot refuses a table short of any member, so there is no
    runtime arm for a missing fold, and answering "not raised" would switch
    the signal off.
    """
    if record is None:
        return False
    replayed = ALARM_TABLE[record.signal].fold(
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
