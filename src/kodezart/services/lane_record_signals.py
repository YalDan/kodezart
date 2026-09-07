"""Supervisor observations collected from one addressed lane record read."""

import json

from pydantic import TypeAdapter

from kodezart.core.protocols import TrackerPort
from kodezart.domain.run_shape import commits_ahead_of_record
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSubject,
    AlarmSubjectKind,
    RunAlarm,
)
from kodezart.types.domain.run_state import LaneCommit

_COMMIT_ROWS = TypeAdapter(tuple[LaneCommit, ...])


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
    subject = AlarmSubject(
        kind=AlarmSubjectKind.LANE, scope_key=scope_key, lane_key=lane_key
    )
    comment, record = await LaneRecordReader(tracker=tracker, operation=operation).read(
        issue_key=issue_key, lane_key=lane_key, record_ref=record_ref
    )
    values = (
        json.dumps(record.lane_key),
        json.dumps(record.head_sha),
        str(record.commits_ahead),
        _COMMIT_ROWS.dump_json(tuple(record.commits), by_alias=True).decode("utf-8"),
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
