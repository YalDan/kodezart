"""Observe barren growth from the addressed lane's recorded counters."""

from collections.abc import Mapping

from kodezart.core.config import AppConfig
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import RunShapeReadError
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.run_shape import read_barren_tick
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_alarm import AlarmReading, AlarmSignal, RunAlarm


async def observe_recorded_barren_tick(
    *,
    tracker: TrackerPort,
    operation: OperationConfig,
    config: AppConfig,
    scope_key: str,
    lane_key: str,
    issue_key: str,
    record_ref: str | None = None,
    previous_open: AlarmReading,
    supersession_refs: Mapping[str, str],
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Read native growth and current closure without consulting a repository.

    The prior tick's open references and established supersession references
    remain supplied facts. Both counters come from one addressed lane comment,
    including its native identity and recorded head. Record and criterion
    changes during the observation refuse both alarming and quiet results.
    Commit-row agreement belongs to its separate signal; no history or count
    is reconstructed here. Scheduling and leased publication remain external.
    """
    supersessions = dict(supersession_refs)
    reader = LaneRecordReader(tracker=tracker, operation=operation)
    comment, record = await reader.read(
        issue_key=issue_key, lane_key=lane_key, record_ref=record_ref
    )
    result, criteria = await read_barren_tick(
        tracker=tracker,
        config=config,
        scope_key=scope_key,
        lane_key=lane_key,
        issue_key=issue_key,
        previous_open=previous_open,
        files_changed=AlarmReading(
            source_ref=comment.comment_key,
            value=str(record.files_changed),
            at_sha=record.head_sha,
        ),
        commits_ahead=AlarmReading(
            source_ref=comment.comment_key,
            value=str(record.commits_ahead),
            at_sha=record.head_sha,
        ),
        supersession_refs=supersessions,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
    current_comment, _ = await reader.read(
        issue_key=issue_key, lane_key=lane_key, record_ref=comment.comment_key
    )
    current_criteria = tuple(await tracker.read_criteria(issue_key=issue_key))
    if current_comment.body != comment.body or current_criteria != criteria:
        raise RunShapeReadError(
            signal=AlarmSignal.BARREN_TICK_WITH_DIFF_GROWTH.value,
            source_ref=comment.comment_key,
            reason="the lane record or criterion family changed during observation",
        )
    return result
