"""Validate the native membership of a complete addressed issue subtree."""

from collections.abc import Sequence

from kodezart.domain.errors import ScopeReadError
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.tracker import TrackerIssue


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
