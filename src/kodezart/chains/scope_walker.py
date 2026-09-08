"""Live scope readiness; execution ownership and the walking loop follow it."""

from kodezart.core.protocols import TrackerPort
from kodezart.domain.dispatch import blocker_keys
from kodezart.domain.errors import ScopeReadError
from kodezart.domain.issue_tree import (
    RECORD_KINDS,
    SubtreeClosure,
    index_issue_tree,
)
from kodezart.domain.topology import plan_topology
from kodezart.services.scope_planning import read_scope_plan
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadyLane, ScopeReadySet
from kodezart.types.domain.tracker import TrackerIssue


async def _read_tree(
    *, root: str, tracker: TrackerPort, ref: ScopeRef
) -> dict[str, TrackerIssue]:
    # A consulted descendant tree is an issue scope in its own right. Apply
    # the same named stage barriers there: an outside-filter open decision
    # is not completed merely because record issues carry zero criteria.
    tree = await read_scope_plan(
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=root), tracker=tracker
    )
    return index_issue_tree(
        root=root,
        rows=tree.scope.issues,
        ref=ref,
    )


async def read_scope_ready(*, ref: ScopeRef, tracker: TrackerPort) -> ScopeReadySet:
    """Recompute one ready set without dispatch, writes or merge observations.

    Candidate identity stays native scope membership. The complete
    descendant tree then decides both questions through one arithmetic: what
    a candidate still owes, and whether a blocker is discharged. A scope
    therefore never reports itself at rest while a criterion under one of its
    members is open, including one a container filter cannot reach.
    Repeated reads detect movement; they do not claim a transactional lease.
    """
    tracker.require_issue_classification_reads()
    plan = await read_scope_plan(ref=ref, tracker=tracker)
    members = {issue.issue_key: issue for issue in plan.scope.issues}
    facts = dict(members)
    trees: dict[str, dict[str, TrackerIssue]] = {}
    covered: set[str] = set()
    for key, issue in members.items():
        if issue.parent_key in members:
            continue
        tree = await _read_tree(root=key, tracker=tracker, ref=ref)
        for child_key, child in tree.items():
            if child_key in facts and facts[child_key] != child:
                raise ScopeReadError("overlapping subtree facts changed", ref=ref)
            facts[child_key] = child
        trees[key] = tree
        covered.update(tree)
    if members.keys() - covered:
        raise ScopeReadError("scope parentage is not rooted", ref=ref)
    closure = SubtreeClosure(facts=facts, ref=ref)
    approved: dict[str, bool] = {}
    gaps: dict[str, tuple[TrackerIssue, ...]] = {}
    for key, issue in members.items():
        if "criterion" in issue.issue_labels or issue.issue_labels & RECORD_KINDS:
            continue
        approved[key] = await tracker.execution_approved(issue_key=key)
        if approved[key]:
            gap = closure.gap(key)
            if gap:
                gaps[key] = gap
    blockers = {
        blocker
        for key in gaps
        for blocker in blocker_keys(members[key])
        if blocker in members
    }
    blocking = frozenset(key for key in blockers if not closure.is_closed(key))
    topology = plan_topology(
        issues=(*plan.scope.issues, *plan.dependencies),
        candidate_keys=frozenset(gaps),
        blocking_issue_keys=blocking,
    )
    for key, tree in trees.items():
        if await _read_tree(root=key, tracker=tracker, ref=ref) != tree:
            raise ScopeReadError("subtree family changed during readiness", ref=ref)
    if await read_scope_plan(ref=ref, tracker=tracker) != plan:
        raise ScopeReadError("scope plan changed during readiness", ref=ref)
    for key, was_approved in approved.items():
        if await tracker.execution_approved(issue_key=key) != was_approved:
            raise ScopeReadError("scope approval changed during readiness", ref=ref)
    return ScopeReadySet(
        scope=plan.scope,
        ready=tuple(
            ScopeReadyLane(
                issue=entry.issue,
                effective_priority=entry.effective_priority,
                gap=gaps[entry.issue.issue_key],
            )
            for entry in topology.ready
        ),
        blocked=topology.blocked,
    )
