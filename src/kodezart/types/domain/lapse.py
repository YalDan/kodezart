"""The one reading of a recorded grading against the head it is read at.

A grading is a claim about one commit.  The tree moves on without it, and
the moment it does the grading stops speaking for the tree: nothing
re-examined the criterion, so it is neither upheld nor refuted.  That
reading is ``lapsed``, and it is the entire content of comparing a graded
sha with a head sha.

The comparison lives here once.  Every carrier of a graded sha reaches its
state through this function rather than through arithmetic of its own, so
the same two shas are never weighed differently in two places, and a
reader that wants to know what "behind head" means has one body to read.
A second site performing the comparison is a guarded failure of the source
tree rather than a matter left to review.
"""

from enum import StrEnum


class GradedState(StrEnum):
    """Where a recorded grading stands against the head it is read at.

    Two members, and neither is a verdict: ``lapsed`` says the grading no
    longer speaks for the head, not that the criterion failed.  Collapsing
    the pair to a boolean is what lets a lapse be read as a failure, so the
    enum refuses to be one.
    """

    current = "current"
    lapsed = "lapsed"

    def __bool__(self) -> bool:
        raise TypeError("GradedState requires an explicit member comparison")


def graded_state(*, graded_sha: str, head_sha: str) -> GradedState:
    """Where the grading recorded at ``graded_sha`` stands at ``head_sha``.

    Total over the two identities it is handed: the same revision reads
    ``current``, and every other pair reads ``lapsed``.  Nothing here asks
    whether the graded commit is an ancestor of the head — a head that
    moved past the grading and a grading taken on another branch are the
    same fact to every reader, namely that the grading was not taken at the
    revision being read.
    """
    return GradedState.current if graded_sha == head_sha else GradedState.lapsed
