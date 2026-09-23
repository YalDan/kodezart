"""Live scope readiness; execution ownership and the walking loop follow it."""

from collections.abc import Container

from kodezart.core.protocols import ScopePlanReader, ScopeReadyReader
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
from kodezart.types.domain.scope_ready import (
    ScopeReadyLane,
    ScopeReadySet,
    UnreachableCriterion,
    UnreachableReason,
)
from kodezart.types.domain.tracker import TrackerIssue


async def _read_tree(
    *, root: str, tracker: ScopePlanReader, ref: ScopeRef
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


def _filter_reason(issue: TrackerIssue, *, ref: ScopeRef) -> UnreachableCriterion:
    """Why *issue* is out of reach, in the terms its scope's filter is stated in.

    A project or initiative scope filters on the project an issue belongs to
    and a milestone scope on the milestone, so each kind reads the field its
    own filter reads and never the other: the two are independent, and an
    issue of the addressed project can still sit under another milestone.
    """
    match ref.kind:
        case ScopeKind.PROJECT | ScopeKind.INITIATIVE:
            return UnreachableCriterion(
                issue_key=issue.issue_key,
                reason=(
                    UnreachableReason.NO_PROJECT
                    if issue.project_id is None
                    else UnreachableReason.OTHER_PROJECT
                ),
                container=issue.project_id,
            )
        case ScopeKind.MILESTONE:
            return UnreachableCriterion(
                issue_key=issue.issue_key,
                reason=(
                    UnreachableReason.NO_MILESTONE
                    if issue.milestone_key is None
                    else UnreachableReason.OTHER_MILESTONE
                ),
                container=issue.milestone_key,
            )
        case ScopeKind.ISSUE:
            # An issue scope's members ARE its subtree: the same read produced
            # both, so a criterion missing from the members is an inconsistent
            # read and not a filter's answer.
            raise ScopeReadError(
                "an issue scope left an open criterion outside its own subtree",
                ref=ref,
            )


def _unreachable_criteria(
    *, closure: SubtreeClosure, members: Container[str]
) -> tuple[UnreachableCriterion, ...]:
    """The open criteria of *closure* whose own issues *members* never carried.

    The same open reading ``unresolved`` is, asked of membership: a scope
    family carries every criterion child of every member its filter resolved,
    so an open criterion that is not a member sits under a deliverable the
    filter never reached. In the closure's own order, and declared rather than
    left to be inferred from a silence — a reader that sees neither the
    criterion nor a statement about it cannot tell an unreachable obligation
    from none.
    """
    return tuple(
        _filter_reason(criterion, ref=closure.ref)
        for criterion in closure.scope_gap().owed
        if criterion.issue_key not in members
    )


async def read_scope_ready(
    *, ref: ScopeRef, tracker: ScopeReadyReader
) -> ScopeReadySet:
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
    closed: dict[str, TrackerIssue] = {}
    for key, issue in members.items():
        if "criterion" in issue.issue_labels or issue.issue_labels & RECORD_KINDS:
            continue
        approved[key] = await tracker.execution_approved(issue_key=key)
        if approved[key]:
            gap = closure.gap(key)
            if gap:
                gaps[key] = gap
            else:
                # The same arithmetic, read the other way. A member owing
                # nothing is not a candidate for the topology — there is no
                # iteration to order — but it is a member a delivery may
                # still be owed for, and the reading that drops it is the
                # reason nothing could ever notice.
                closed[key] = issue
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
    scope_gap = closure.scope_gap()
    return ScopeReadySet(
        scope=plan.scope,
        ready=tuple(
            ScopeReadyLane(
                issue=entry.issue,
                effective_priority=entry.effective_priority,
                gap=gaps[entry.issue.issue_key],
                criteria=closure.roster(entry.issue.issue_key),
            )
            for entry in topology.ready
        ),
        blocked=topology.blocked,
        unapproved=tuple(key for key, value in approved.items() if not value),
        criteria=tuple(
            issue for issue in facts.values() if "criterion" in issue.issue_labels
        ),
        closed=tuple(closed.values()),
        # What the scope still owes, from the closure that computed the gaps.
        # A reporter asking a criterion's state kind again would be a second
        # reading of the same question, answerable differently.
        unresolved=tuple(issue.issue_key for issue in scope_gap.owed),
        excluded=scope_gap.excluded,
        # The same unresolved reading, asked of the filter's own membership.
        unreachable=_unreachable_criteria(closure=closure, members=members),
    )
