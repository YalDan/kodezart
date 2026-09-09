"""Read one native scope family without losing direct criterion children."""

from collections.abc import Mapping

from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import ScopeReadError
from kodezart.services.scope_resolution import resolve_scope
from kodezart.types.domain.scope import ScopeKind, ScopeRef
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


async def read_member_subtrees(
    *, tracker: TrackerPort, scope: ScopeRef, members: Mapping[str, TrackerIssue]
) -> dict[str, TrackerIssue]:
    """Every issue beneath the scope's members, read as issue scopes of their own.

    A container filter carries the members it was asked for. What each of
    them owes lives in its complete subtree, so a descendant no filter
    reaches is read through its root rather than taken for absent. Each
    member whose parent is itself a member is already inside that ancestor's
    subtree; a member no subtree contains refuses instead of narrowing the
    answer.
    """
    subtree: dict[str, TrackerIssue] = {}
    for key, issue in members.items():
        if issue.parent_key in members:
            continue
        rooted = await read_scope_members(
            tracker=tracker, scope=ScopeRef(kind=ScopeKind.ISSUE, key=key)
        )
        for rooted_key, rooted_issue in rooted.items():
            established = subtree.get(rooted_key, members.get(rooted_key))
            if established is not None and established != rooted_issue:
                raise ScopeReadError(
                    f"subtree fact contradicts the scope family: {rooted_key}",
                    ref=scope,
                )
            subtree[rooted_key] = rooted_issue
    absent = members.keys() - subtree.keys()
    if absent:
        raise ScopeReadError(
            "scope member is outside every member subtree: "
            + ", ".join(sorted(absent)),
            ref=scope,
        )
    return subtree
