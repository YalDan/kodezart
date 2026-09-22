"""Native membership and closure of a complete addressed issue subtree."""

from collections.abc import Mapping, Sequence

from kodezart.domain.errors import EmptyFireCriteriaError, ScopeReadError
from kodezart.domain.gap import compute_gap, in_gap
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.tracker import TrackerIssue

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
    _ = ref
    return compute_gap(criteria)


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
        self.rosters: dict[str, tuple[TrackerIssue, ...]] = {}
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
        self._walk(key)
        return self.gaps[key]

    def roster(self, key: str) -> tuple[TrackerIssue, ...]:
        """Every criterion record under *key*, open or closed, in subtree order.

        The same walk and the same refusals as ``gap``, read for the whole
        set rather than its open part: a reading of what the subtree owes now
        says nothing about what it owed before, so a consumer comparing two
        ticks needs the identities that could have closed between them.
        """
        self._walk(key)
        return self.rosters[key]

    def _walk(self, key: str) -> None:
        """Assemble both readings of *key*'s subtree in one traversal.

        One walk, because the two answers are two readings of one set and a
        second traversal could see a different shape of it. Whatever refuses
        the open reading refuses the whole one: a criterion with children, a
        record issue with children and a container owing no criteria are not
        subtrees this arithmetic can be asked about at all.
        """
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
                self.rosters[current] = (issue,)
                self.gaps[current] = open_criteria((issue,), ref=self.ref)
            elif issue.issue_labels & RECORD_KINDS:
                if children:
                    raise ScopeReadError("record issue has child issues", ref=self.ref)
                self.rosters[current] = ()
                self.gaps[current] = ()
            elif expanded:
                self.rosters[current] = tuple(
                    row for child in children for row in self.rosters[child.issue_key]
                )
                self.gaps[current] = tuple(
                    row for child in children for row in self.gaps[child.issue_key]
                )
            else:
                self.criteria(current)
                pending.append((current, True))
                pending.extend((child.issue_key, False) for child in children)

    def is_closed(self, key: str) -> bool:
        """Finished is owing nothing: the same read, asked the other way."""
        return not self.gap(key)

    def open_criterion_keys(self) -> tuple[str, ...]:
        """Every still-open criterion anywhere in the subtree, by key, in order.

        What the whole tree still owes, for a caller that REPORTS the remaining
        work rather than fires anything for it. The reading is the gap
        arithmetic's own membership predicate asked of each criterion record the
        tree carries, so what a scope still owes and what a lane still owes are
        two askings of one question and a reporter needs no second opinion about
        what a workflow state means (KOD-356).

        Every criterion the tree carries, and not only those beneath a
        candidate: an obligation under a member nobody approved is one the scope
        has not discharged either. A criterion the board Canceled or closed as a
        Duplicate counts for nothing here, which is the same answer the reading
        gives a lane that carries one (KOD-794).
        """
        return tuple(
            key
            for key, issue in self.facts.items()
            if "criterion" in issue.issue_labels and in_gap(issue)
        )
