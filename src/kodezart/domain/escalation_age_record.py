"""What the one ageing record at an open question's address should say next.

An open question ages on two terms (KOD-507): the commits its own lane has
recorded since it was raised, and the walker ticks since it was first
observed. A walker tick leaves no tracker fact of its own (KOD-788); what it
does leave is the commits the fired lane records. So a tick is counted by
those commits, across every lane of the question's scope, since an anchor:
the scope's lane heads at the first observation of the question.

The anchor is stored once, as the first reading of the question's own
``ESCALATION_AGEING`` record at its existing ``(subject, signal)`` address,
and every later tick measures from it. A tick over unchanged tracker state
counts the same commits and composes the record already there, so it writes
nothing (KOD-528). Nothing here reads a clock or counts the supervisor's own
ticks, and a killed tick re-enters from the stored anchor alone.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_shape import escalation_ageing, read_alarm_value
from kodezart.types.domain.escalation import (
    EscalationResolution,
    EscalationResolutionState,
)
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    CountEvidence,
    EscalationSubject,
    ReferencesEvidence,
    RunAlarm,
)

_SIGNAL = AlarmSignal.ESCALATION_AGEING

#: How many readings the ageing observation itself carries, after the anchor.
_OBSERVED_READINGS = 6


@dataclass(frozen=True, slots=True)
class ScopePosition:
    """Every lane record of one scope, as the commit shas it has recorded.

    Keyed by lane, each order in trajectory order. A lane with no record is
    absent rather than empty: it has recorded nothing to count.
    """

    orders: Mapping[str, tuple[str, ...]]

    def heads(self) -> tuple[str, ...]:
        """The last recorded sha of every lane that recorded one, sorted."""
        return tuple(sorted(order[-1] for order in self.orders.values() if order))

    def head_of(self, lane_key: str) -> str | None:
        """The last sha *lane_key* recorded, or ``None`` when it recorded none."""
        order = self.orders.get(lane_key, ())
        return order[-1] if order else None


def anchor_reading(*, scope_key: str, position: ScopePosition) -> AlarmReading:
    """The scope's lane heads now, as the reading every later tick measures from."""
    return AlarmReading(
        source_ref=scope_key, value=ReferencesEvidence(value=position.heads())
    )


def anchor_of(record: RunAlarm) -> AlarmReading:
    """The anchor a stored ageing record carries as its first reading."""
    anchor = record.readings[0]
    if not isinstance(anchor.value, ReferencesEvidence):
        raise RunShapeReadError(
            signal=_SIGNAL.value,
            source_ref=anchor.source_ref,
            reason="the stored record carries no anchor of lane heads",
        )
    return anchor


def commits_since(*, anchor: AlarmReading, position: ScopePosition) -> int:
    """The commits every lane of the scope has recorded since *anchor*.

    A lane counts what follows the last anchored head found in its own
    order. A lane whose order holds no anchored head — new since the anchor,
    or rewritten beneath it — counts its whole order, because none of it is
    known to precede the anchor.
    """
    heads = read_alarm_value(anchor, ReferencesEvidence, _SIGNAL)
    anchored = frozenset(heads)
    total = 0
    for order in position.orders.values():
        found = [index for index, sha in enumerate(order) if sha in anchored]
        total += len(order) - (found[-1] + 1) if found else len(order)
    return total


def tick_reading(*, anchor: AlarmReading, position: ScopePosition) -> AlarmReading:
    """The tick-age input: the commits recorded across the scope since *anchor*."""
    return AlarmReading(
        source_ref=anchor.source_ref,
        value=CountEvidence(value=commits_since(anchor=anchor, position=position)),
    )


def is_ageing_raised(record: RunAlarm | None) -> bool:
    """Whether *record*'s own readings still replay to an ageing alarm.

    An anchor-only record is kept so the next tick has an anchor to measure
    from, and it is not raised. A record carrying an observation is replayed,
    and a replay that does not reproduce the bound the record carries was
    written by something other than this arithmetic and refuses.
    """
    if record is None:
        return False
    observed = record.readings[1:]
    if observed and len(observed) != _OBSERVED_READINGS:
        raise RunShapeReadError(
            signal=_SIGNAL.value,
            source_ref=record.raised_at_sha,
            reason="the stored record carries neither an anchor nor an observation",
        )
    replayed = (
        escalation_ageing(
            subject=record.subject,
            readings=observed,
            raised_at_sha=record.raised_at_sha,
            raised_by=record.raised_by,
        )
        if observed
        else None
    )
    if record.bound != (None if replayed is None else replayed.bound):
        raise RunShapeReadError(
            signal=_SIGNAL.value,
            source_ref=record.raised_at_sha,
            reason="the stored record replays to a bound it does not carry",
        )
    return replayed is not None


def next_ageing_record(
    *,
    subject: EscalationSubject,
    stored: RunAlarm | None,
    anchor: AlarmReading,
    observed: RunAlarm | None,
    resolution: EscalationResolution,
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """What the address should hold after this tick, or ``None`` to write nothing.

    A question answered before it was ever observed is never anchored, so a
    board of long-answered questions stays quiet. An unanswered one is
    anchored on its first observation, raised or not. After that the address
    is rewritten only when the answer to "is it raised" changes, so one
    condition across many ticks is one record however the counts grow.
    """
    if observed is not None and observed.subject != subject:
        raise RunShapeReadError(
            signal=_SIGNAL.value,
            source_ref=subject.member_id,
            reason="the observation is about another question",
        )
    if stored is None:
        if resolution.state is EscalationResolutionState.RESOLVED:
            return None
    elif is_ageing_raised(stored) == (observed is not None):
        return None
    if observed is None:
        return RunAlarm(
            subject=subject,
            signal=_SIGNAL,
            readings=(anchor,),
            bound=None,
            raised_at_sha=raised_at_sha,
            raised_by=raised_by,
        )
    return RunAlarm(
        subject=subject,
        signal=_SIGNAL,
        readings=(anchor, *observed.readings),
        bound=observed.bound,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
