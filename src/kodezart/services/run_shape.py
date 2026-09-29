"""Read-only assembly of recorded run-shape observations."""

from kodezart.config.app import AppConfig
from kodezart.core.protocols import (
    TrackerCriteriaReader,
)
from kodezart.domain.gap import compute_gap
from kodezart.domain.run_shape import (
    BARREN_COMMITS_BOUND,
    BARREN_FILES_BOUND,
    SURFACE_HOLDERS_BOUND,
    barren_tick_with_diff_growth,
    surface_contended,
)
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSubject,
    CountEvidence,
    LaneSubject,
    ReferencesEvidence,
    RunAlarm,
)
from kodezart.types.domain.tracker import TrackerIssue


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
                value=CountEvidence(value=config.run_alarm_max_surface_holders),
            ),
        ),
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )


async def observe_barren_tick(
    *,
    tracker: TrackerCriteriaReader,
    config: AppConfig,
    scope_key: str,
    lane_key: str,
    issue_key: str,
    previous_open: AlarmReading,
    files_changed: AlarmReading,
    commits_ahead: AlarmReading,
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
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
    return observation


async def read_barren_tick(
    *,
    tracker: TrackerCriteriaReader,
    config: AppConfig,
    scope_key: str,
    lane_key: str,
    issue_key: str,
    previous_open: AlarmReading,
    files_changed: AlarmReading,
    commits_ahead: AlarmReading,
    raised_at_sha: str,
    raised_by: str,
) -> tuple[RunAlarm | None, tuple[TrackerIssue, ...]]:
    """Read current criterion closure, then compare already-recorded growth.

    Closure comes from the shared criterion gap arithmetic: Completion closes,
    and canceled or duplicate work closes on state alone. The previous tick
    and both lane-base diff counts must already be recorded projections, with
    their source references supplied here. No repository, author session or
    tracker writer is called.
    """
    subject = LaneSubject(scope_key=scope_key, lane_key=lane_key)
    criteria = tuple(await tracker.read_criteria(issue_key=issue_key))
    open_keys = {criterion.issue_key for criterion in compute_gap(criteria).owed}
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
                value=ReferencesEvidence(value=closed_keys),
            ),
            files_changed,
            commits_ahead,
            AlarmReading(
                source_ref=BARREN_FILES_BOUND,
                value=CountEvidence(
                    value=config.run_alarm_barren_tick_max_files_changed
                ),
            ),
            AlarmReading(
                source_ref=BARREN_COMMITS_BOUND,
                value=CountEvidence(
                    value=config.run_alarm_barren_tick_max_commits_ahead
                ),
            ),
        ),
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )

    return observation, criteria
