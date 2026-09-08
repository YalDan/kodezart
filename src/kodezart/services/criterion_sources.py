"""Read a criterion's own full source from an unambiguous native child family."""

from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.tracker import TrackerIssue


async def read_audit_criterion(
    *, tracker: TrackerPort, lane_issue_key: str, criterion_key: str
) -> TrackerIssue:
    rows = await tracker.read_criteria(issue_key=lane_issue_key)
    keys = [row.issue_key for row in rows]
    if len(set(keys)) != len(keys) or any(
        row.parent_key != lane_issue_key or "criterion" not in row.issue_labels
        for row in rows
    ):
        raise ValueError("the current criterion family has ambiguous membership")
    selected = [row for row in rows if row.issue_key == criterion_key]
    try:
        (criterion,) = selected
    except ValueError as exc:
        raise ValueError("the lane has no unique requested criterion") from exc
    return criterion
