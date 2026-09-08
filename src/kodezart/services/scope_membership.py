"""Read one native scope family without losing direct criterion children."""

from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import ScopeReadError
from kodezart.services.scope_resolution import resolve_scope
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.tracker import TrackerIssue


async def read_scope_members(
    *, tracker: TrackerPort, scope: ScopeRef
) -> dict[str, TrackerIssue]:
    members: dict[str, TrackerIssue] = {}
    scoped = (await resolve_scope(ref=scope, tracker=tracker)).issues
    for issue in scoped:
        if issue.issue_key in members:
            raise ScopeReadError("duplicate scope member", ref=scope)
        members[issue.issue_key] = issue
    for issue in scoped:
        child_keys: set[str] = set()
        for criterion in await tracker.read_criteria(issue_key=issue.issue_key):
            if criterion.issue_key in child_keys:
                raise ScopeReadError("duplicate scope criterion member", ref=scope)
            child_keys.add(criterion.issue_key)
            previous = members.get(criterion.issue_key)
            if criterion.parent_key != issue.issue_key or (
                previous is not None and previous != criterion
            ):
                raise ScopeReadError("scope criterion membership changed", ref=scope)
            members[criterion.issue_key] = criterion
    return members
