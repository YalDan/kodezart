"""Collect escalation age from the recorded occurrence and its lane history."""

import json

from kodezart.core.config import AppConfig
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import RunShapeReadError
from kodezart.services.escalation_records import EscalationRecordReader
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.run_shape import read_escalation_ageing
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_alarm import AlarmReading, AlarmSignal, RunAlarm


async def observe_recorded_escalation_ageing(
    *,
    tracker: TrackerPort,
    operation: OperationConfig,
    config: AppConfig,
    scope_key: str,
    lane_key: str,
    issue_key: str,
    escalation_key: str,
    escalation_ref: str | None = None,
    lane_record_ref: str | None = None,
    ticks_since_raise: AlarmReading,
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Use actual native sources, keeping the recorded tick input explicit.

    There is no repository lookup, wall clock, writer or implicit latest run.
    The complete commit order belongs to the addressed lane record. Both
    source records are checked again after the existing resolution consumer;
    drift refuses rather than clearing or publishing an old observation.
    """
    escalation_reader = EscalationRecordReader(tracker=tracker, operation=operation)
    lane_reader = LaneRecordReader(tracker=tracker, operation=operation)
    address = {
        "issue_key": issue_key,
        "lane_key": lane_key,
        "escalation_key": escalation_key,
    }
    escalation_comment, escalation = await escalation_reader.read(
        **address, record_ref=escalation_ref
    )
    lane_comment, lane = await lane_reader.read(
        issue_key=issue_key, lane_key=lane_key, record_ref=lane_record_ref
    )
    commit_order = tuple(row.sha for row in lane.commits)
    if (
        len(commit_order) != lane.commits_ahead
        or not commit_order
        or commit_order[-1] != lane.head_sha
    ):
        raise RunShapeReadError(
            signal=AlarmSignal.ESCALATION_AGEING.value,
            source_ref=lane_comment.comment_key,
            reason="the recorded commit series does not reach its declared head",
        )
    result, resolution = await read_escalation_ageing(
        tracker=tracker,
        config=config,
        scope_key=scope_key,
        lane_key=lane_key,
        escalation=AlarmReading(
            source_ref=escalation_comment.comment_key,
            value=escalation_comment.body.partition("\n")[2],
            at_sha=escalation.raised_at_sha,
        ),
        commits=AlarmReading(
            source_ref=lane_comment.comment_key,
            value=json.dumps(commit_order),
            at_sha=lane.head_sha,
        ),
        ticks_since_raise=ticks_since_raise,
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
    current_escalation, _ = await escalation_reader.read(
        **address, record_ref=escalation_comment.comment_key
    )
    current_lane, _ = await lane_reader.read(
        issue_key=issue_key, lane_key=lane_key, record_ref=lane_comment.comment_key
    )
    current_resolution = await tracker.read_escalation_resolution(**address)
    if (
        current_resolution != resolution
        or current_escalation.body != escalation_comment.body
        or current_lane.body != lane_comment.body
    ):
        raise RunShapeReadError(
            signal=AlarmSignal.ESCALATION_AGEING.value,
            source_ref=escalation_comment.comment_key,
            reason=(
                "the escalation, lane history or resolution changed during observation"
            ),
        )
    return result
