"""Ready-set arithmetic over tracker facts, without dispatch or tracker writes."""

from collections.abc import Iterator, Mapping, Sequence

from kodezart.domain.dispatch import blocker_keys, live_blocker
from kodezart.domain.errors import ScopeCycleError
from kodezart.types.domain.topology import BlockedIssue, ReadyIssue, TopologyPlan
from kodezart.types.domain.tracker import TrackerIssue, is_open, priority_rank


def _acyclic_order(graph: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    """Visit prerequisites first using an explicit stack, even for deep graphs.

    This traversal order is internal to priority propagation. It is never
    returned as a future dispatch schedule.
    """
    visited: set[str] = set()
    ordered: list[str] = []
    for root in graph:
        if root in visited:
            continue
        path = [root]
        active = {root}
        stack: list[Iterator[str]] = [iter(graph[root])]
        while stack:
            child = next(stack[-1], None)
            if child is None:
                completed = path.pop()
                active.remove(completed)
                visited.add(completed)
                ordered.append(completed)
                stack.pop()
            elif child in active:
                raise ScopeCycleError(issue_keys=path[path.index(child) :])
            elif child not in visited:
                path.append(child)
                active.add(child)
                stack.append(iter(graph[child]))
    return tuple(ordered)


def plan_topology(
    *,
    issues: Sequence[TrackerIssue],
    candidate_keys: frozenset[str],
) -> TopologyPlan:
    """Rank candidates with no open blocker by effective priority, then age.

    ``issues`` is the complete dependency snapshot, including facts about any
    referenced issue outside the selected scope. Missing facts are refused:
    an unread blocker cannot be treated as closed. ``candidate_keys`` names
    issues already admitted by the caller's scope approval and gap checks;
    this pure function neither reads approval provenance nor substitutes a
    parent's state for the criterion-subtree admission check.

    Priority flows from everything an issue transitively blocks back to that
    issue, including through candidates that are themselves blocked. Closed
    blockers do not prevent readiness. Only ``blockedBy`` relations count;
    parentage and reverse/related edges never manufacture a dependency.

    Exact priority/age ties retain snapshot order. No future lane order or
    concurrency limit is computed: the walker takes one fire and re-reads.
    """
    by_key: dict[str, TrackerIssue] = {}
    for issue in issues:
        if issue.issue_key in by_key:
            raise ValueError(f"duplicate issue in topology snapshot: {issue.issue_key}")
        by_key[issue.issue_key] = issue
    unknown_candidates = candidate_keys - by_key.keys()
    if unknown_candidates:
        names = ", ".join(sorted(unknown_candidates))
        raise ValueError(f"candidate issues absent from topology snapshot: {names}")
    graph = {
        key: tuple(dict.fromkeys(blocker_keys(issue))) for key, issue in by_key.items()
    }
    missing = {key for blockers in graph.values() for key in blockers} - by_key.keys()
    if missing:
        raise ValueError(
            f"blocker facts absent from topology snapshot: {', '.join(sorted(missing))}"
        )
    ordered = _acyclic_order(graph)
    effective = {key: issue.priority for key, issue in by_key.items()}
    for key in reversed(ordered):
        for blocker in graph[key]:
            effective[blocker] = min(
                effective[blocker],
                effective[key],
                key=priority_rank,
            )
    ready: list[ReadyIssue] = []
    blocked: list[BlockedIssue] = []
    for key, issue in by_key.items():
        if key not in candidate_keys:
            continue
        if live_blocker(issue, blockers=by_key) is None:
            ready.append(ReadyIssue(issue=issue, effective_priority=effective[key]))
        else:
            blocked.append(
                BlockedIssue(
                    issue_key=key,
                    blocker_keys=tuple(
                        blocker
                        for blocker in graph[key]
                        if is_open(by_key[blocker].state_kind)
                    ),
                ),
            )
    ready.sort(
        key=lambda entry: (
            priority_rank(entry.effective_priority),
            entry.issue.created_at,
        ),
    )
    return TopologyPlan(ready=tuple(ready), blocked=tuple(blocked))
