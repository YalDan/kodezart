"""Read one native scope family without losing direct criterion children."""

from collections.abc import Mapping

from kodezart.core.protocols import ScopeMemberReader
from kodezart.domain.errors import ScopeReadError
from kodezart.services.scope_resolution import resolve_scope
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import TrackerIssue


async def read_scope_members(
    *, tracker: ScopeMemberReader, scope: ScopeRef
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


async def read_subtree_criteria(
    *, tracker: ScopeMemberReader, subject: str
) -> dict[str, TrackerIssue]:
    """Every criterion sub-issue under *subject*, keyed and at head.

    The one subtree reading. The extent every reading of a fire's criteria
    is taken over: what the entry captures is this roster entire, what the
    fire owes is a selection from it by state, what a lane delivers on is
    all of it, and what an answer may address at the write is read here
    too. Two definitions of the extent could answer two different rosters
    for one subject, and the barrier that compares their selections would
    refuse a lane nothing is wrong with.
    """
    return subtree_criteria(
        await read_scope_members(
            tracker=tracker, scope=ScopeRef(kind=ScopeKind.ISSUE, key=subject)
        )
    )


def subtree_criteria(members: Mapping[str, TrackerIssue]) -> dict[str, TrackerIssue]:
    """Every criterion sub-issue of one subtree reading already taken, keyed.

    The filter :func:`read_subtree_criteria` applies, stated once, so a
    reader that needs the whole membership too (the native writer's
    authority read) takes its criteria from that same map instead of
    reading the subtree a second time (KOD-1249).
    """
    return {
        key: issue
        for key, issue in members.items()
        if "criterion" in issue.issue_labels
    }


async def read_member_subtrees(
    *, tracker: ScopeMemberReader, scope: ScopeRef, members: Mapping[str, TrackerIssue]
) -> dict[str, TrackerIssue]:
    """Every issue beneath the scope's members, read as issue scopes of their own.

    A container filter carries the members it was asked for. What each of
    them owes lives in its complete subtree, so a descendant no filter
    reaches is read through its root rather than taken for absent. Each
    member whose parent is itself a member is already inside that ancestor's
    subtree; a member no subtree contains refuses instead of narrowing the
    answer.

    An issue the family and a subtree both hold is the family's copy. Until
    2026-09-24 the two copies were compared whole and a difference refused
    (KOD-1241): a mention of a member elsewhere on the tracker between the
    two reads gives it a new related-to relation and a later ``updated_at``,
    and that refused a live scope twice over a fact the plan never uses.
    """
    subtree: dict[str, TrackerIssue] = {}
    for key, issue in members.items():
        if issue.parent_key in members:
            continue
        rooted = await read_scope_members(
            tracker=tracker, scope=ScopeRef(kind=ScopeKind.ISSUE, key=key)
        )
        for rooted_key, rooted_issue in rooted.items():
            subtree[rooted_key] = members.get(rooted_key, rooted_issue)
    absent = members.keys() - subtree.keys()
    if absent:
        raise ScopeReadError(
            "scope member is outside every member subtree: "
            + ", ".join(sorted(absent)),
            ref=scope,
        )
    return subtree
