"""Native plan-time barriers, before approval-qualified dispatch selection."""

from kodezart.core.protocols import ScopePlanReader
from kodezart.domain.dispatch import blocker_keys
from kodezart.domain.errors import ScopePlanRefusalError, ScopeReadError
from kodezart.domain.topology import plan_topology
from kodezart.services.scope_membership import (
    read_member_subtrees,
    read_scope_members,
)
from kodezart.types.domain.scope import ResolvedScope, ScopePlanSnapshot, ScopeRef
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind, is_open


async def read_scope_plan(
    *, ref: ScopeRef, tracker: ScopePlanReader
) -> ScopePlanSnapshot:
    """Read a coherent dependency closure and refuse named invalid stage facts.

    The stage barrier is measured over the same thing the exit condition is:
    every member's complete subtree. An offending key under a deliverable
    child is therefore named here, before the walk, rather than reached only
    once a fire has already begun owing it.

    This establishes no approval or readiness. The future walker must apply
    approval, live subtree closure and dispatch ownership to the returned facts.
    """
    snapshot, subtree = await _read_scope_facts(ref=ref, tracker=tracker)
    facts = {
        issue.issue_key: issue
        for issue in (*snapshot.scope.issues, *snapshot.dependencies)
    }
    open_decisions = tuple(
        issue.issue_key
        for issue in subtree
        if "decision" in issue.issue_labels and is_open(issue.state_kind)
    )
    backlog = tuple(
        issue.issue_key
        for issue in subtree
        if "criterion" in issue.issue_labels
        and issue.state_kind is WorkflowStateKind.BACKLOG
    )
    crossing = tuple(
        (issue.issue_key, target)
        for issue in facts.values()
        for target in blocker_keys(issue)
        if (
            "criterion" in issue.issue_labels
            or "criterion" in facts[target].issue_labels
        )
        and (issue.parent_key is None or issue.parent_key != facts[target].parent_key)
    )
    if open_decisions or backlog or crossing:
        raise ScopePlanRefusalError(
            ref=ref,
            open_decisions=open_decisions,
            backlog_criteria=backlog,
            cross_subtree_edges=crossing,
        )
    # Preserve stage refusal precedence over the existing structural check.
    plan_topology(issues=tuple(facts.values()), candidate_keys=frozenset())
    return snapshot


async def read_scope_facts(
    *, ref: ScopeRef, tracker: ScopePlanReader
) -> ScopePlanSnapshot:
    """Read coherent membership and dependencies without future-stage admission."""
    snapshot, _subtree = await _read_scope_facts(ref=ref, tracker=tracker)
    plan_topology(
        issues=(*snapshot.scope.issues, *snapshot.dependencies),
        candidate_keys=frozenset(),
    )
    return snapshot


async def _read_scope_facts(
    *, ref: ScopeRef, tracker: ScopePlanReader
) -> tuple[ScopePlanSnapshot, tuple[TrackerIssue, ...]]:
    """Share the same native observations and rereads across scope consumers."""
    tracker.require_scope_plan_reads()
    members = await read_scope_members(tracker=tracker, scope=ref)
    for key, issue in members.items():
        if await tracker.read_planning_issue(issue_key=key) != issue:
            raise ScopeReadError(f"scope fact changed during planning: {key}", ref=ref)
    subtree = await read_member_subtrees(tracker=tracker, scope=ref, members=members)
    for key, issue in subtree.items():
        if key in members:
            continue
        if await tracker.read_planning_issue(issue_key=key) != issue:
            raise ScopeReadError(
                f"subtree fact changed during planning: {key}", ref=ref
            )
    facts = dict(members)
    pending = list(members.values())
    while pending:
        issue = pending.pop()
        for key in blocker_keys(issue):
            if key in facts:
                continue
            dependency = await tracker.read_planning_issue(issue_key=key)
            if dependency.issue_key != key:
                raise ScopeReadError(
                    "dependency read changed the requested identity", ref=ref
                )
            facts[key] = dependency
            pending.append(dependency)
    # Check the native observations again before either a refusal or a plan.
    # A missing or moved dependency is never interpreted as a closed blocker.
    for key, issue in facts.items():
        if await tracker.read_planning_issue(issue_key=key) != issue:
            raise ScopeReadError(f"dependency changed during planning: {key}", ref=ref)
    if await read_scope_members(tracker=tracker, scope=ref) != members:
        raise ScopeReadError("scope family changed during planning", ref=ref)
    if (
        await read_member_subtrees(tracker=tracker, scope=ref, members=members)
        != subtree
    ):
        raise ScopeReadError("member subtrees changed during planning", ref=ref)
    snapshot = ScopePlanSnapshot(
        scope=ResolvedScope(ref=ref, issues=tuple(members.values())),
        dependencies=tuple(issue for key, issue in facts.items() if key not in members),
    )
    return snapshot, tuple(subtree.values())
