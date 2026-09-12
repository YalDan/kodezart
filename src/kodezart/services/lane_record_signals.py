"""Supervisor observations collected from one addressed lane record read."""

from kodezart.core.protocols import TrackerPort
from kodezart.domain.run_shape import commits_ahead_of_record
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    CommitsEvidence,
    CountEvidence,
    LaneSubject,
    RunAlarm,
    TextEvidence,
)


async def observe_commits_ahead_of_record(
    *,
    tracker: TrackerPort,
    operation: OperationConfig,
    scope_key: str,
    lane_key: str,
    issue_key: str,
    record_ref: str | None = None,
    raised_at_sha: str,
    raised_by: str,
) -> RunAlarm | None:
    """Read and compare the record's own declared count and enumerated rows.

    All four projections carry the native comment identity and the head
    from that same read. Missing, malformed, duplicated or unreadable records
    retain the reader's typed refusal; none becomes a clean empty lane.
    This observation never resolves the head against a repository. A wholly
    stale record whose count and rows agree remains invisible to this signal.
    """
    subject = LaneSubject(scope_key=scope_key, lane_key=lane_key)
    comment, record = await LaneRecordReader(tracker=tracker, operation=operation).read(
        issue_key=issue_key, lane_key=lane_key, record_ref=record_ref
    )
    values = (
        TextEvidence(value=record.lane_key),
        TextEvidence(value=record.head_sha),
        CountEvidence(value=record.commits_ahead),
        CommitsEvidence(value=tuple(record.commits)),
    )
    return commits_ahead_of_record(
        subject=subject,
        readings=tuple(
            AlarmReading(
                source_ref=comment.comment_key, value=value, at_sha=record.head_sha
            )
            for value in values
        ),
        raised_at_sha=raised_at_sha,
        raised_by=raised_by,
    )
