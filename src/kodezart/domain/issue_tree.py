"""Native membership and closure of a complete addressed issue subtree."""

from collections.abc import Mapping, Sequence

from kodezart.domain.errors import (
    EmptyFireCriteriaError,
    ScopeReadError,
    ScopeSupersessionReadError,
)
from kodezart.domain.gap import compute_gap
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind

RECORD_KINDS = frozenset({"tracker", "decision"})


def index_issue_tree(
    *, root: str, rows: Sequence[TrackerIssue], ref: ScopeRef
) -> dict[str, TrackerIssue]:
    facts: dict[str, TrackerIssue] = {}
    children: dict[str, list[str]] = {}
    for issue in rows:
        key = issue.issue_key
        if key in facts:
            raise ScopeReadError("duplicate subtree issue", ref=ref)
        facts[key] = issue
        if key != root:
            if issue.parent_key is None:
                raise ScopeReadError("subtree member has no parent", ref=ref)
            children.setdefault(issue.parent_key, []).append(key)
    if root not in facts:
        raise ScopeReadError("subtree root is missing", ref=ref)
    visited: set[str] = set()
    pending = [root]
    while pending:
        key = pending.pop()
        if key in visited:
            raise ScopeReadError("subtree parent cycle", ref=ref)
        visited.add(key)
        pending.extend(children.get(key, ()))
    if visited != facts.keys():
        raise ScopeReadError("subtree has disconnected or cyclic parentage", ref=ref)
    if facts[root].parent_key in facts:
        raise ScopeReadError("subtree root has an internal parent", ref=ref)
    return facts


def open_criteria(
    criteria: Sequence[TrackerIssue], *, ref: ScopeRef
) -> tuple[TrackerIssue, ...]:
    """The still-open criterion records of one deliverable, in order."""
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


class SubtreeClosure:
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
                self.closed[current] = not open_criteria((issue,), ref=self.ref)
            elif issue.issue_labels & RECORD_KINDS:
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
