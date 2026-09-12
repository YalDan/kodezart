"""Deterministic native identity and graph checks, without semantic planning."""

from collections.abc import Sequence
from hashlib import sha256

from kodezart.domain.errors import OrganizeWriteRefusalError, ScopeCycleError
from kodezart.domain.topology import plan_topology
from kodezart.types.domain.organize_graph import (
    BlockedByChange,
    GraphChange,
    IssueGraphSnapshot,
    MilestoneChange,
    ParentChange,
    PriorityChange,
    RelatedToChange,
)
from kodezart.types.domain.tracker import IssueRelation, IssueRelationKind, TrackerIssue


def graph_snapshot(issue: TrackerIssue) -> IssueGraphSnapshot:
    return IssueGraphSnapshot(
        issue_key=issue.issue_key,
        body_digest=sha256(issue.body.encode()).hexdigest(),
        title=issue.title,
        state_kind=issue.state_kind,
        issue_labels=tuple(sorted(issue.issue_labels)),
        parent_key=issue.parent_key,
        priority=issue.priority,
        milestone_key=issue.milestone_key,
        project_id=issue.project_id,
        relations=tuple(
            sorted(issue.relations, key=lambda edge: (edge.kind.value, edge.issue_key))
        ),
    )


def graph_peers(issue: TrackerIssue, changes: Sequence[GraphChange]) -> frozenset[str]:
    keys = {issue.issue_key}
    for change in changes:
        if isinstance(change, ParentChange):
            keys.update(
                key for key in (issue.parent_key, change.parent_id) if key is not None
            )
        elif isinstance(change, (BlockedByChange, RelatedToChange)):
            keys.update((*change.add, *change.remove))
    return frozenset(keys)


def changed_issue(issue: TrackerIssue, changes: Sequence[GraphChange]) -> TrackerIssue:
    """Apply only named fields to a validated candidate; preserve all other facts."""
    fields = issue.model_dump()
    relations = list(issue.relations)
    for change in changes:
        if isinstance(change, ParentChange):
            fields["parent_key"] = change.parent_id
        elif isinstance(change, PriorityChange):
            fields["priority"] = change.priority
        elif isinstance(change, MilestoneChange):
            fields["milestone_key"] = change.milestone_id
        else:
            kind = (
                IssueRelationKind.BLOCKED_BY
                if isinstance(change, BlockedByChange)
                else IssueRelationKind.RELATED
            )
            relations = [
                edge
                for edge in relations
                if not (edge.kind is kind and edge.issue_key in change.remove)
            ]
            held = {edge.issue_key for edge in relations if edge.kind is kind}
            relations.extend(
                IssueRelation(kind=kind, issue_key=key)
                for key in change.add
                if key not in held
            )
    fields["relations"] = relations
    return TrackerIssue.model_validate(fields)


def validate_graph_change(
    *,
    issue_key: str,
    changes: tuple[GraphChange, ...],
    issues: Sequence[TrackerIssue],
    member_keys: frozenset[str],
) -> tuple[TrackerIssue, frozenset[str]]:
    """Refuse absent identities, unowned peers and cycles before a native write."""
    by_key = {issue.issue_key: issue for issue in issues}

    def refuse(reason: str) -> OrganizeWriteRefusalError:
        return OrganizeWriteRefusalError(issue_key=issue_key, reason=reason)

    if len(by_key) != len(issues) or issue_key not in by_key:
        raise refuse("graph snapshot has duplicate or missing native identities")
    if not changes or len({change.kind for change in changes}) != len(changes):
        raise refuse("graph changes must name distinct fields")
    source = by_key[issue_key]
    peers = graph_peers(source, changes)
    if not peers <= member_keys or not peers <= by_key.keys():
        raise refuse("an affected graph peer is outside the current admitted scope")
    candidate = changed_issue(source, changes)
    for change in changes:
        if isinstance(change, (BlockedByChange, RelatedToChange)) and issue_key in (
            *change.add,
            *change.remove,
        ):
            raise refuse("an issue cannot relate to itself")
    by_key[issue_key] = candidate
    for node in by_key.values():
        seen = {node.issue_key}
        parent = node.parent_key
        while parent is not None:
            if parent in seen:
                raise refuse("proposed parentage creates a cycle")
            if parent not in by_key:
                raise refuse("parent ancestry is absent from the graph snapshot")
            seen.add(parent)
            parent = by_key[parent].parent_key
    try:
        plan_topology(issues=tuple(by_key.values()), candidate_keys=frozenset())
    except (ScopeCycleError, ValueError) as exc:
        raise refuse("proposed dependency graph is cyclic or incomplete") from exc
    return candidate, peers


def changed_peers(
    *, issue_key: str, changes: Sequence[GraphChange], issues: Sequence[TrackerIssue]
) -> tuple[TrackerIssue, ...]:
    """Expected inverse relation facts, retaining every unrelated peer edge."""
    by_key = {issue.issue_key: issue for issue in issues}
    touched: set[str] = set()
    for change in changes:
        if not isinstance(change, (BlockedByChange, RelatedToChange)):
            continue
        inverse = (
            IssueRelationKind.BLOCKS
            if isinstance(change, BlockedByChange)
            else IssueRelationKind.RELATED
        )
        for key in (*change.remove, *change.add):
            peer = by_key[key]
            relations = [
                edge
                for edge in peer.relations
                if not (edge.kind is inverse and edge.issue_key == issue_key)
            ]
            if key in change.add:
                relations.append(IssueRelation(kind=inverse, issue_key=issue_key))
            by_key[key] = TrackerIssue.model_validate(
                {**peer.model_dump(), "relations": relations}
            )
            touched.add(key)
    return tuple(by_key[key] for key in sorted(touched))
