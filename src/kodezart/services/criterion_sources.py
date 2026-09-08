"""Read a criterion's own full source from an unambiguous native child family."""

from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import CriterionResolutionError
from kodezart.types.domain.tracker import TrackerIssue


async def resolve_criterion(
    *, tracker: TrackerPort, issue_key: str, criterion_key: str
) -> TrackerIssue:
    """Resolve one native key against a fresh, complete criterion-family read.

    Membership, identity and full source are one observation. Callers apply their
    own state policy; no address, body anchor or criterion set is persisted here.
    """

    def refuse(reason: str) -> CriterionResolutionError:
        return CriterionResolutionError(
            issue_key=issue_key, criterion_key=criterion_key, reason=reason
        )

    try:
        rows = tuple(await tracker.read_criteria(issue_key=issue_key))
    except Exception as exc:
        raise refuse(f"the current criterion family is unreadable: {exc}") from exc
    selected = [row for row in rows if row.issue_key == criterion_key]
    if len(selected) != 1:
        raise refuse(f"the key resolves to {len(selected)} current sub-issues")
    keys = [row.issue_key for row in rows]
    if len(set(keys)) != len(keys) or any(
        row.parent_key != issue_key or "criterion" not in row.issue_labels
        for row in rows
    ):
        raise refuse("the current criterion family has ambiguous membership")
    return selected[0]
