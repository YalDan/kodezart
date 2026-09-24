"""Native plan-time barriers, before approval-qualified dispatch selection."""

from kodezart.core.protocols import ScopePlanReader
from kodezart.domain.dispatch import blocker_keys
from kodezart.domain.errors import ScopePlanRefusalError, ScopeReadError
from kodezart.domain.topology import plan_topology
from kodezart.services.scope_membership import (
    read_member_subtrees,
    read_scope_members,
)
from kodezart.types.domain.scope import (
    ResolvedScope,
    ScopeKind,
    ScopePlanSnapshot,
    ScopeRef,
)
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
    """Read the board once and build every scope consumer's facts from that read.

    Three reads and no rereads: the scope family, the subtree under each
    root member for the descendants a container listing does not carry,
    and one planning read per blocker outside both. An issue the family
    or a subtree already holds is never read again; the membership read
    hydrates every member through the same full issue read the planning
    read makes, so a second read of it can only answer the same fields or
    a later moment.

    Measured 2026-09-24 (KOD-1241): this function read a live scope eight
    times over to build one plan and compared each reread with the first
    as whole issues. Two mentions of a member elsewhere on the tracker
    during those reads gave it a related-to relation and a later
    ``updated_at``, and both planning attempts refused on a fact no plan
    uses: the topology reads blocking edges alone. The plan is a snapshot
    of one moment; the walker's own reads and the fire's entry read take
    a later one on their own.
    """
    tracker.require_scope_plan_reads()
    members = await read_scope_members(tracker=tracker, scope=ref)
    if ref.kind is ScopeKind.ISSUE:
        # An issue scope's family is its whole subtree already: the same
        # read would answer the same rows a second time.
        subtree = dict(members)
    else:
        subtree = await read_member_subtrees(
            tracker=tracker, scope=ref, members=members
        )
    facts = dict(members)
    pending = list(members.values())
    while pending:
        issue = pending.pop()
        for key in blocker_keys(issue):
            if key in facts:
                continue
            dependency = subtree.get(key)
            if dependency is None:
                dependency = await tracker.read_planning_issue(issue_key=key)
                if dependency.issue_key != key:
                    raise ScopeReadError(
                        "dependency read changed the requested identity", ref=ref
                    )
            facts[key] = dependency
            pending.append(dependency)
    snapshot = ScopePlanSnapshot(
        scope=ResolvedScope(ref=ref, issues=tuple(members.values())),
        dependencies=tuple(issue for key, issue in facts.items() if key not in members),
    )
    return snapshot, tuple(subtree.values())
