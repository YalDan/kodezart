"""Live scope readiness; execution ownership and the walking loop follow it."""

from collections.abc import Container, Sequence
from dataclasses import dataclass

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
        unapproved=tuple(key for key, value in approved.items() if not value),
        criteria=tuple(
            issue for issue in facts.values() if "criterion" in issue.issue_labels
        ),
    )


@dataclass(frozen=True, slots=True)
class UnreachableCriterion:
    """One criterion a lane owes whose own issue the scope's filter misses.

    Unreachability is not ownership: the criterion sits inside the lane's
    subtree, so it is the lane's work and the lane is fired for it.  What
    the filter decides is only whether the scope can ADDRESS that issue in
    its own right, which is what ``reason`` records.
    """

    issue_key: str
    lane_key: str
    reason: str


def _filter_reason(issue: TrackerIssue, *, kind: ScopeKind) -> str:
    """Why *issue* is out of reach, in the terms the filter is itself stated in."""
    match kind:
        case ScopeKind.PROJECT | ScopeKind.INITIATIVE:
            return (
                issue.project_id
                if issue.project_id is not None
                else "the issue belongs to no project"
            )
        case ScopeKind.MILESTONE:
            return (
                issue.milestone_key
                if issue.milestone_key is not None
                else "the issue belongs to no milestone"
            )
        case ScopeKind.ISSUE:
            return "the issue is outside the addressed subtree"


def unreachable_criteria(
    *,
    ref: ScopeRef,
    members: Container[str],
    lanes: Sequence[ScopeReadyLane],
) -> tuple[UnreachableCriterion, ...]:
    """The lanes' open criteria whose own issues the scope family never carried.

    A scope family carries every criterion child of every member its filter
    resolved, so a criterion reached only through a member's SUBTREE is one
    the scope cannot address on its own.  Declared here rather than left to
    be inferred from a silence: a reader that sees neither the criterion nor
    a statement about it cannot tell an unreachable obligation from none.
    Each is named once, under the first lane that owes it.
    """
    named: dict[str, UnreachableCriterion] = {}
    for lane in lanes:
        for criterion in lane.gap:
            if criterion.issue_key in members or criterion.issue_key in named:
                continue
            named[criterion.issue_key] = UnreachableCriterion(
                issue_key=criterion.issue_key,
                lane_key=lane.issue.issue_key,
                reason=_filter_reason(criterion, kind=ref.kind),
            )
    return tuple(named.values())
