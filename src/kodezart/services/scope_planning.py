"""Native plan-time barriers, before approval-qualified dispatch selection."""

from kodezart.core.protocols import TrackerPort
from kodezart.domain.dispatch import blocker_keys
from kodezart.domain.errors import ScopePlanRefusalError, ScopeReadError
from kodezart.domain.topology import plan_topology
from kodezart.services.scope_membership import read_scope_members
from kodezart.types.domain.scope import ResolvedScope, ScopePlanSnapshot, ScopeRef
from kodezart.types.domain.tracker import WorkflowStateKind, is_open


async def read_scope_plan(*, ref: ScopeRef, tracker: TrackerPort) -> ScopePlanSnapshot:
    """Read a coherent dependency closure and refuse named invalid stage facts.

    This establishes no approval or readiness. The future walker must apply
    approval, live subtree closure and dispatch ownership to the returned facts.
    """
    tracker.require_scope_plan_reads()
    members = await read_scope_members(tracker=tracker, scope=ref)
    facts = dict(members)
    pending = list(members.values())
    while pending:
        issue = pending.pop()
        for key in blocker_keys(issue):
            if key in facts:
                continue
            dependency = await tracker.read_issue(issue_key=key)
            if dependency.issue_key != key:
                raise ScopeReadError(
                    "dependency read changed the requested identity", ref=ref
                )
            facts[key] = dependency
            pending.append(dependency)
    # Check the native observations again before either a refusal or a plan.
    # A missing or moved dependency is never interpreted as a closed blocker.
    for key, issue in facts.items():
        if key not in members and await tracker.read_issue(issue_key=key) != issue:
            raise ScopeReadError(f"dependency changed during planning: {key}", ref=ref)
    if await read_scope_members(tracker=tracker, scope=ref) != members:
        raise ScopeReadError("scope family changed during planning", ref=ref)
    open_decisions = tuple(
        key
        for key, issue in members.items()
        if "decision" in issue.issue_labels and is_open(issue.state_kind)
    )
    backlog = tuple(
        key
        for key, issue in members.items()
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
    # The existing planner owns cycle detection. No candidate has yet been
    # approved, so the stage check deliberately emits no ready-set claim.
    plan_topology(issues=tuple(facts.values()), candidate_keys=frozenset())
    return ScopePlanSnapshot(
        scope=ResolvedScope(ref=ref, issues=tuple(members.values())),
        dependencies=tuple(issue for key, issue in facts.items() if key not in members),
    )
