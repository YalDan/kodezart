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
    """The still-open records of one criterion family, in order.

    The leaf the subtree gap is assembled from, never a reading of
    finishedness on its own: what an issue owes is what its whole subtree
    owes, so no caller may take one family for the answer.
    """
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
    """One arithmetic over a subtree; deliverable workflow fields never decide.

    What an issue still owes and whether it is finished are two readings of
    the same tuple — every still-open criterion record anywhere beneath it.
    ``is_closed`` is the emptiness of ``gap``, so a candidate's brief and a
    blocker's discharge cannot part, and no call site can answer one of the
    two questions with the other.
    """

    def __init__(self, *, facts: Mapping[str, TrackerIssue], ref: ScopeRef) -> None:
        self.facts = facts
        self.ref = ref
        self.children: dict[str, list[TrackerIssue]] = {}
        self.gaps: dict[str, tuple[TrackerIssue, ...]] = {}
        for issue in facts.values():
            if issue.parent_key is not None and issue.parent_key in facts:
                self.children.setdefault(issue.parent_key, []).append(issue)

    def criteria(self, key: str) -> tuple[TrackerIssue, ...]:
        children = self.children.get(key, ())
        criteria = tuple(row for row in children if "criterion" in row.issue_labels)
        if not criteria:
            raise EmptyFireCriteriaError(issue_key=key)
        return criteria

    def gap(self, key: str) -> tuple[TrackerIssue, ...]:
        """Every still-open criterion record under *key*, in subtree order."""
        pending = [(key, False)]
        while pending:
            current, expanded = pending.pop()
            if current in self.gaps:
                continue
            issue = self.facts[current]
            children = self.children.get(current, ())
            if "criterion" in issue.issue_labels:
                if children:
                    raise ScopeReadError("criterion has child issues", ref=self.ref)
                self.gaps[current] = open_criteria((issue,), ref=self.ref)
            elif issue.issue_labels & RECORD_KINDS:
                if children:
                    raise ScopeReadError("record issue has child issues", ref=self.ref)
                self.gaps[current] = ()
            elif expanded:
                self.gaps[current] = tuple(
                    row for child in children for row in self.gaps[child.issue_key]
                )
            else:
                self.criteria(current)
                pending.append((current, True))
                pending.extend((child.issue_key, False) for child in children)
        return self.gaps[key]

    def is_closed(self, key: str) -> bool:
        """Finished is owing nothing: the same read, asked the other way."""
        return not self.gap(key)
