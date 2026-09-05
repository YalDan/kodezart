"""Staleness — the recorded base against the base the current blockers imply.

Detection is arithmetic.  Recompute the ``BaseSpec`` from the blockers as
the tracker records them NOW and compare it with the one recorded on the
dispatch.  Any difference — an edge added, an edge removed, a blocker's
deliverable ref replaced, or an input ref's sha advanced — makes the
recorded base stale.  No judgment, no heuristic, no tolerance.

This is what makes changing the graph safe to do: add a blocker and the
base changes, so the lane's recorded base is stale and the arithmetic says
so at the next read, with nobody needing to have noticed the edit.

The consequence is deliberately expensive.  A verdict is about a sha, and a
criterion graded against a branch is graded against that branch ON ITS
BASE; when the base moves, the tree the verdict was about no longer exists.
This module produces the trigger only: the lapse arithmetic itself, and the
surface a lapsed verdict is carried on, belong to the lane that owns them.
"""

from kodezart.types.domain.branch import BaseSpec


def is_base_stale(recorded: BaseSpec, implied: BaseSpec) -> bool:
    """True iff the recorded base is not the base the blockers imply now.

    A pure function of two ``BaseSpec`` values.  Equality is structural and
    ordered, so a reordered input tuple is a different spec — which is why
    the resolver orders the frontier deterministically rather than leaving
    the order to tracker read order.
    """
    return recorded != implied
