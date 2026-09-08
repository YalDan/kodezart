"""Live scope readiness; execution ownership and the walking loop follow it."""

from collections.abc import Mapping, Sequence

from kodezart.core.protocols import TrackerPort
from kodezart.domain.dispatch import blocker_keys
from kodezart.domain.errors import (
    EmptyFireCriteriaError,
    ScopeReadError,
    ScopeSupersessionReadError,
)
from kodezart.domain.gap import compute_gap
from kodezart.domain.issue_tree import index_issue_tree
from kodezart.domain.topology import plan_topology
from kodezart.services.scope_planning import read_scope_plan
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadyLane, ScopeReadySet
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind

_RECORD_KINDS = frozenset({"tracker", "decision"})


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


def _gap(
    *, criteria: Sequence[TrackerIssue], ref: ScopeRef
) -> tuple[TrackerIssue, ...]:
    unresolved = tuple(
        issue.issue_key
        for issue in criteria
        if issue.state_kind in {WorkflowStateKind.CANCELED, WorkflowStateKind.DUPLICATE}
    )
    if unresolved:
        raise ScopeSupersessionReadError(ref=ref, criterion_keys=unresolved)
    # No criterion in this input needs a supersession reference. Do not turn
    # an absent native reference reader into a guessed cancellation policy.
    return compute_gap(criteria=criteria, supersession_refs={})


class _SubtreeClosure:
    """All children decide closure; deliverable workflow fields never do."""

    def __init__(self, *, facts: Mapping[str, TrackerIssue], ref: ScopeRef) -> None:
        self.facts = facts
        self.ref = ref
        self.children: dict[str, list[TrackerIssue]] = {}
        self.closed: dict[str, bool] = {}
        for issue in facts.values():
            if issue.parent_key is not None and issue.parent_key in facts:
                self.children.setdefault(issue.parent_key, []).append(issue)

    def criteria(self, key: str) -> tuple[TrackerIssue, ...]:
        children = self.children.get(key, ())
        criteria = tuple(row for row in children if "criterion" in row.issue_labels)
        if not criteria:
            raise EmptyFireCriteriaError(issue_key=key)
        return criteria

    def is_closed(self, key: str) -> bool:
        pending = [(key, False)]
        while pending:
            current, visited = pending.pop()
            if current in self.closed:
                continue
            issue = self.facts[current]
            children = self.children.get(current, ())
            if "criterion" in issue.issue_labels:
                if children:
                    raise ScopeReadError("criterion has child issues", ref=self.ref)
                self.closed[current] = not _gap(criteria=(issue,), ref=self.ref)
            elif issue.issue_labels & _RECORD_KINDS:
                if children:
                    raise ScopeReadError("record issue has child issues", ref=self.ref)
                self.closed[current] = True
            elif visited:
                self.closed[current] = all(
                    self.closed[child.issue_key] for child in children
                )
            else:
                self.criteria(current)
                pending.append((current, True))
                pending.extend((child.issue_key, False) for child in children)
        return self.closed[key]


async def read_scope_ready(*, ref: ScopeRef, tracker: TrackerPort) -> ScopeReadySet:
    """Recompute one ready set without dispatch, writes or merge observations.

    Candidate identity stays native scope membership. Complete descendants
    outside a container filter are read only to decide a blocker's closure.
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
    closure = _SubtreeClosure(facts=facts, ref=ref)
    approved: dict[str, bool] = {}
    gaps: dict[str, tuple[TrackerIssue, ...]] = {}
    for key, issue in members.items():
        if "criterion" in issue.issue_labels or issue.issue_labels & _RECORD_KINDS:
            continue
        approved[key] = await tracker.execution_approved(issue_key=key)
        if approved[key]:
            gap = _gap(criteria=closure.criteria(key), ref=ref)
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
