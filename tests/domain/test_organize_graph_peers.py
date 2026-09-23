"""Which issues' graphs a graph change writes, for every change kind and shape.

``graph_peers`` is the rule both the adapter and the double ask before a
graph write: every issue it names is an address the writer must hold.  A
relation edge is stored on both of its ends, so an edge added and an edge
removed each write the other end; a parent change writes the parent the
child leaves and the one it joins.  The conformance suite pins the
refusal at the port; this module pins the rule itself, with each expected
set written out rather than derived.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest

from kodezart.domain.organize_graph import graph_peers
from kodezart.types.domain.organize_graph import GraphProposal
from kodezart.types.domain.tracker import (
    IssuePriority,
    TrackerIssue,
    WorkflowStateKind,
)

CHILD = "FIX-1"
OLD_PARENT = "FIX-2"
NEW_PARENT = "FIX-3"
ADDED = "FIX-4"
REMOVED = "FIX-5"
#: A second issue in one relation list, so every issue a list names is asked
#: for and not only its first.
ADDED_TOO = "FIX-6"
REMOVED_TOO = "FIX-7"
AT = datetime(2026, 9, 1, tzinfo=UTC)


def _issue(*, parent: str | None) -> TrackerIssue:
    return TrackerIssue(
        issue_key=CHILD,
        title=CHILD,
        body="body",
        priority=IssuePriority.NONE,
        state_name="Todo",
        state_kind=WorkflowStateKind.UNSTARTED,
        queue_states=frozenset(),
        team_key=None,
        created_at=AT,
        updated_at=AT,
        url=f"https://tracker.invalid/{CHILD}",
        parent_key=parent,
    )


def _peers(
    changes: Sequence[Mapping[str, object]], *, parent: str | None = OLD_PARENT
) -> frozenset[str]:
    proposal = GraphProposal.model_validate(
        {"kind": "graph", "issue_id": CHILD, "changes": list(changes)}
    )
    return graph_peers(_issue(parent=parent), proposal.changes)


#: (change, the child's parent before it, the issues its write reaches).
PEER_SHAPES: Mapping[str, tuple[Mapping[str, object], str | None, frozenset[str]]] = {
    "parent_move": (
        {"kind": "parent", "parent_id": NEW_PARENT},
        OLD_PARENT,
        frozenset({CHILD, OLD_PARENT, NEW_PARENT}),
    ),
    "parent_clear": (
        {"kind": "parent", "parent_id": None},
        OLD_PARENT,
        frozenset({CHILD, OLD_PARENT}),
    ),
    "parent_first": (
        {"kind": "parent", "parent_id": NEW_PARENT},
        None,
        frozenset({CHILD, NEW_PARENT}),
    ),
    "parent_clear_of_none": (
        {"kind": "parent", "parent_id": None},
        None,
        frozenset({CHILD}),
    ),
    "blocked_by_add": (
        {"kind": "blocked_by", "add": [ADDED]},
        OLD_PARENT,
        frozenset({CHILD, ADDED}),
    ),
    "blocked_by_remove": (
        {"kind": "blocked_by", "remove": [REMOVED]},
        OLD_PARENT,
        frozenset({CHILD, REMOVED}),
    ),
    "blocked_by_add_and_remove": (
        {"kind": "blocked_by", "add": [ADDED], "remove": [REMOVED]},
        OLD_PARENT,
        frozenset({CHILD, ADDED, REMOVED}),
    ),
    "blocked_by_add_two": (
        {"kind": "blocked_by", "add": [ADDED, ADDED_TOO]},
        OLD_PARENT,
        frozenset({CHILD, ADDED, ADDED_TOO}),
    ),
    "blocked_by_remove_two": (
        {"kind": "blocked_by", "remove": [REMOVED, REMOVED_TOO]},
        OLD_PARENT,
        frozenset({CHILD, REMOVED, REMOVED_TOO}),
    ),
    "related_to_add": (
        {"kind": "related_to", "add": [ADDED]},
        OLD_PARENT,
        frozenset({CHILD, ADDED}),
    ),
    "related_to_remove": (
        {"kind": "related_to", "remove": [REMOVED]},
        OLD_PARENT,
        frozenset({CHILD, REMOVED}),
    ),
    "related_to_add_and_remove": (
        {"kind": "related_to", "add": [ADDED], "remove": [REMOVED]},
        OLD_PARENT,
        frozenset({CHILD, ADDED, REMOVED}),
    ),
    "related_to_add_two": (
        {"kind": "related_to", "add": [ADDED, ADDED_TOO]},
        OLD_PARENT,
        frozenset({CHILD, ADDED, ADDED_TOO}),
    ),
    "related_to_remove_two": (
        {"kind": "related_to", "remove": [REMOVED, REMOVED_TOO]},
        OLD_PARENT,
        frozenset({CHILD, REMOVED, REMOVED_TOO}),
    ),
    "priority": (
        {"kind": "priority", "priority": IssuePriority.HIGH.value},
        OLD_PARENT,
        frozenset({CHILD}),
    ),
    "milestone": (
        {"kind": "milestone", "milestone_id": "milestone-1"},
        OLD_PARENT,
        frozenset({CHILD}),
    ),
}


def test_every_graph_change_kind_has_a_shape() -> None:
    """The table covers every kind a graph proposal can carry."""
    kinds = {str(change["kind"]) for change, _, _ in PEER_SHAPES.values()}
    assert kinds == {"parent", "blocked_by", "related_to", "priority", "milestone"}


@pytest.mark.parametrize("shape", sorted(PEER_SHAPES))
def test_a_graph_change_reaches_exactly_the_issues_it_writes(shape: str) -> None:
    change, parent, reached = PEER_SHAPES[shape]

    assert _peers([change], parent=parent) == reached


def test_changes_together_reach_every_issue_each_one_reaches() -> None:
    """One proposal of several changes asks for the union of their peers."""
    assert _peers(
        [
            {"kind": "parent", "parent_id": None},
            {"kind": "blocked_by", "remove": [REMOVED]},
            {"kind": "related_to", "add": [ADDED]},
        ]
    ) == frozenset({CHILD, OLD_PARENT, REMOVED, ADDED})
