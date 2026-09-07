"""Run-shape predicates over recorded values, with no side effects."""

from typing import Annotated

from pydantic import Field, NonNegativeInt, TypeAdapter, ValidationError

from kodezart.domain.errors import RunShapeReadError
from kodezart.types.domain.escalation import (
    EscalationResolution,
    EscalationResolutionState,
)
from kodezart.types.domain.run_alarm import (
    AlarmBound,
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    RunAlarm,
)
from kodezart.types.domain.run_state import LaneEscalation

ESCALATION_COMMITS_BOUND = "run_alarm_escalation_age_max_commits"
ESCALATION_TICKS_BOUND = "run_alarm_escalation_age_max_ticks"

_ESCALATION = TypeAdapter(LaneEscalation)
_RESOLUTION = TypeAdapter(EscalationResolution)
_COMMITS = TypeAdapter(tuple[Annotated[str, Field(min_length=1, pattern=r"\S")], ...])
_COUNT = TypeAdapter(NonNegativeInt)


def _unreadable(source_ref: str, reason: str) -> RunShapeReadError:
    return RunShapeReadError(
        signal=AlarmSignal.ESCALATION_AGEING.value,
        source_ref=source_ref,
        reason=reason,
    )


def _decode[T](reading: AlarmReading, adapter: TypeAdapter[T]) -> T:
    try:
        return adapter.validate_json(reading.value, strict=True)
    except ValidationError as exc:
        raise _unreadable(reading.source_ref, "invalid recorded value") from exc


def escalation_ageing(
    *,
    subject: AlarmSubject,
    readings: tuple[AlarmReading, ...],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Measure an unanswered occurrence on its lane's two recorded counters.

    Readings are ordered: the escalation value, its resolution value, the
    recorded commit SHA sequence, ticks since raise, and the two configured
    count limits (commits then ticks). Values use JSON; their original bytes
    and source references survive in the alarm. The resolution names the
    same escalation source, and bound sources are AppConfig field names.

    A counter must strictly exceed its limit to fire its own arm.
    Either arm raises the one subject/signal alarm; if both fire, the commit
    bound is reported first. Both readings remain available for replay.
    Missing or ambiguous history refuses observation instead of clearing it.
    """
    try:
        escalation, resolution, commits, ticks, max_commits, max_ticks = readings
    except ValueError as exc:
        raise _unreadable(
            subject.member_id or subject.scope_key, "incomplete readings"
        ) from exc

    record = _decode(escalation, _ESCALATION)
    answer = _decode(resolution, _RESOLUTION)
    if (
        subject.kind is not AlarmSubjectKind.ESCALATION
        or subject.member_id != record.escalation_key
        or subject.issue_id != record.issue_id
        or subject.lane_key is None
    ):
        raise _unreadable(
            escalation.source_ref, "subject does not identify this escalation"
        )
    if resolution.source_ref != escalation.source_ref:
        raise _unreadable(
            resolution.source_ref, "resolution identifies another escalation"
        )
    if (max_commits.source_ref, max_ticks.source_ref) != (
        ESCALATION_COMMITS_BOUND,
        ESCALATION_TICKS_BOUND,
    ):
        raise _unreadable(
            subject.member_id, "age bounds do not name their AppConfig fields"
        )

    commit_order = _decode(commits, _COMMITS)
    tick_age = _decode(ticks, _COUNT)
    commit_limit = _decode(max_commits, _COUNT)
    tick_limit = _decode(max_ticks, _COUNT)
    if len(set(commit_order)) != len(commit_order):
        raise _unreadable(commits.source_ref, "recorded commit order repeats a SHA")
    if record.raised_at_sha not in commit_order:
        raise _unreadable(
            commits.source_ref, "recorded commit order omits the raise SHA"
        )
    commit_age = len(commit_order) - commit_order.index(record.raised_at_sha) - 1

    if answer.state is EscalationResolutionState.RESOLVED:
        return None
    for reading, configured, observed in (
        (max_commits, commit_limit, commit_age),
        (max_ticks, tick_limit, tick_age),
    ):
        if observed > configured:
            return RunAlarm(
                subject=subject,
                signal=AlarmSignal.ESCALATION_AGEING,
                readings=readings,
                bound=AlarmBound(
                    config_field=reading.source_ref,
                    configured_value=configured,
                    observed_value=observed,
                ),
                raised_at_sha=raised_at_sha,
                raised_by=raised_by,
            )
    return None
