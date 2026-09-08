"""Read-only assembly of recorded run-shape observations."""

from pydantic import ValidationError

from kodezart.core.config import AppConfig
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_shape import (
    ESCALATION_COMMITS_BOUND,
    ESCALATION_TICKS_BOUND,
    escalation_ageing,
)
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    RunAlarm,
)
from kodezart.types.domain.run_state import LaneEscalation


async def observe_escalation_ageing(
    *,
    tracker: TrackerPort,
    config: AppConfig,
    scope_key: str,
    lane_key: str,
    escalation: AlarmReading,
    commits: AlarmReading,
    ticks_since_raise: AlarmReading,
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Read the current decision for an already-read occurrence and counters.

    The caller supplies tracker projections with explicit source references:
    the LaneEscalation JSON, recorded commit SHAs in order, and the recorded
    ticks since this occurrence was raised. This function never collects
    commits from a repository or manufactures walker ticks. It returns an
    observation; persistence belongs to a supervisor holding its own lease.
    """
    try:
        record = LaneEscalation.model_validate_json(escalation.value, strict=True)
    except ValidationError as exc:
        raise RunShapeReadError(
            signal=AlarmSignal.ESCALATION_AGEING.value,
            source_ref=escalation.source_ref,
            reason="invalid recorded escalation",
        ) from exc
    subject = AlarmSubject(
        kind=AlarmSubjectKind.ESCALATION,
        scope_key=scope_key,
        lane_key=lane_key,
        issue_id=record.issue_id,
        member_id=record.escalation_key,
    )
    resolution = await tracker.read_escalation_resolution(
        issue_key=record.issue_id,
        lane_key=lane_key,
        escalation_key=record.escalation_key,
    )
    return escalation_ageing(
        subject=subject,
        readings=(
            escalation,
            AlarmReading(
                source_ref=escalation.source_ref,
                value=resolution.model_dump_json(by_alias=True),
            ),
            commits,
            ticks_since_raise,
            AlarmReading(
                source_ref=ESCALATION_COMMITS_BOUND,
                value=str(config.run_alarm_escalation_age_max_commits),
            ),
            AlarmReading(
                source_ref=ESCALATION_TICKS_BOUND,
                value=str(config.run_alarm_escalation_age_max_ticks),
            ),
        ),
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
