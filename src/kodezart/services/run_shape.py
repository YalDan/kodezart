"""Read-only assembly of recorded run-shape observations."""

from collections.abc import Mapping

from pydantic import TypeAdapter, ValidationError

from kodezart.core.config import AppConfig
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.gap import compute_gap
from kodezart.domain.run_shape import (
    BARREN_COMMITS_BOUND,
    BARREN_FILES_BOUND,
    ESCALATION_COMMITS_BOUND,
    ESCALATION_TICKS_BOUND,
    SURFACE_HOLDERS_BOUND,
    barren_tick_with_diff_growth,
    escalation_ageing,
    surface_contended,
)
from kodezart.types.domain.escalation import EscalationResolution
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    AlarmSubject,
    AlarmSubjectKind,
    RunAlarm,
)
from kodezart.types.domain.run_state import LaneEscalation
from kodezart.types.domain.tracker import TrackerIssue

_REFERENCE_JSON = TypeAdapter(tuple[str, ...])


def observe_surface_contention(
    *,
    config: AppConfig,
    subject: AlarmSubject,
    surface: AlarmReading,
    holder_history: AlarmReading,
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Supply the configured limit to explicitly provided provenance readings.

    This assembly does not read provenance. Its caller must provide both the
    full address and the ordered run-holder identities from a trustworthy
    source. The universal provenance producer is a separate prerequisite.
    """
    return surface_contended(
        subject=subject,
        readings=(
            surface,
            holder_history,
            AlarmReading(
                source_ref=SURFACE_HOLDERS_BOUND,
                value=str(config.run_alarm_max_surface_holders),
            ),
        ),
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )


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
    """Read current resolution and return the existing pure age observation."""
    observation, _ = await read_escalation_ageing(
        tracker=tracker,
        config=config,
        scope_key=scope_key,
        lane_key=lane_key,
        escalation=escalation,
        commits=commits,
        ticks_since_raise=ticks_since_raise,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
    return observation


async def read_escalation_ageing(
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
) -> tuple[RunAlarm | None, EscalationResolution]:
    """Retain the exact resolution that produced this age observation.

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
    observation = escalation_ageing(
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

    return observation, resolution


async def observe_barren_tick(
    *,
    tracker: TrackerPort,
    config: AppConfig,
    scope_key: str,
    lane_key: str,
    issue_key: str,
    previous_open: AlarmReading,
    files_changed: AlarmReading,
    commits_ahead: AlarmReading,
    supersession_refs: Mapping[str, str],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Read current closure and return the existing barren growth observation."""
    observation, _ = await read_barren_tick(
        tracker=tracker,
        config=config,
        scope_key=scope_key,
        lane_key=lane_key,
        issue_key=issue_key,
        previous_open=previous_open,
        files_changed=files_changed,
        commits_ahead=commits_ahead,
        supersession_refs=supersession_refs,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
    return observation


async def read_barren_tick(
    *,
    tracker: TrackerPort,
    config: AppConfig,
    scope_key: str,
    lane_key: str,
    issue_key: str,
    previous_open: AlarmReading,
    files_changed: AlarmReading,
    commits_ahead: AlarmReading,
    supersession_refs: Mapping[str, str],
    raised_at_sha: str,
    raised_by: str,
) -> tuple[RunAlarm | None, tuple[TrackerIssue, ...]]:
    """Read current criterion closure, then compare already-recorded growth.

    Closure comes from the shared criterion gap arithmetic: Completion closes,
    and canceled/duplicate work closes only with an established supersession
    reference supplied by its owning reader. Missing references never imply
    closure. The previous tick and both lane-base diff counts must already
    be recorded projections, with their source references supplied here.
    No repository, author session or tracker writer is called.
    """
    subject = AlarmSubject(
        kind=AlarmSubjectKind.LANE, scope_key=scope_key, lane_key=lane_key
    )
    criteria = tuple(await tracker.read_criteria(issue_key=issue_key))
    open_keys = {
        criterion.issue_key
        for criterion in compute_gap(criteria, supersession_refs=supersession_refs)
    }
    closed_keys = tuple(
        criterion.issue_key
        for criterion in criteria
        if criterion.issue_key not in open_keys
    )
    observation = barren_tick_with_diff_growth(
        subject=subject,
        readings=(
            previous_open,
            AlarmReading(
                source_ref=issue_key,
                value=_REFERENCE_JSON.dump_json(closed_keys).decode("utf-8"),
            ),
            files_changed,
            commits_ahead,
            AlarmReading(
                source_ref=BARREN_FILES_BOUND,
                value=str(config.run_alarm_barren_tick_max_files_changed),
            ),
            AlarmReading(
                source_ref=BARREN_COMMITS_BOUND,
                value=str(config.run_alarm_barren_tick_max_commits_ahead),
            ),
        ),
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )

    return observation, criteria
