"""Whether a grading taken at one commit still stands at another (KOD-696).

One function answers it, for every record that carries a graded sha.  Two
readers weighing the same two shas is how a lapse comes to mean one thing
on a compliance mark and another on a lane check: one of them eventually
grows an ancestry test, a prefix match or a null case, and nothing red
says so.

The reading is arithmetic over values the caller already holds — the two
shas, the re-derivation class, the prefixes that grading exercised, and
the changed-path set of the commit record between them.  It issues no
command, reads no tree and asks no session, so the same inputs read the
same way in a fixture as in a fire.
"""

from collections.abc import Sequence
from enum import StrEnum

from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.criterion_lifecycle import (
    PATH_BOUND_CLASSES,
    ExercisedPath,
    RederivationClass,
)


class GradedState(StrEnum):
    """What an earlier grading is still worth at the sha it is read against.

    Never a boolean.  ``lapsed`` is not a fail and ``counted`` is not a
    fresh pass: both are readings of a verdict some earlier grading already
    reached, and collapsing them to a truth value makes the one case this
    module exists for — a grading nothing has re-derived — indistinguishable
    from the grading itself.
    """

    counted = "counted"
    lapsed = "lapsed"

    def __bool__(self) -> bool:
        """Refuse the truth-value reading rather than answer one.

        ``if graded_state(...)`` would compile away into a silent pass for
        both members, because a non-empty string is true.  A caller names
        the member it means.
        """
        raise TypeError(
            "a graded-sha reading is not a truth value; compare it with a "
            f"{type(self).__name__} member"
        )


def graded_state(
    *,
    graded_sha: str,
    head_sha: str,
    rederivation_class: RederivationClass = RederivationClass.cheap,
    exercised_paths: Sequence[ExercisedPath] = (),
    changeset: ChangesetDigest | None = None,
    base_stale: bool = False,
) -> GradedState:
    """Whether the grading taken at *graded_sha* still stands at *head_sha*.

    Four arms, in one expression, in this order:

    1. a stale recorded base lapses the grading whatever it exercised: the
       tree it was about was cut from a base that is gone, so no path
       reading can rescue it;
    2. the same sha counts, because nothing has moved at all;
    3. a class outside :data:`PATH_BOUND_CLASSES` lapses on any head move:
       re-deriving it is cheap, so asking which paths moved buys nothing
       that re-deriving it would not answer better;
    4. a path-bound grading lapses when the changed paths of the commit
       record reach at or beneath one of the prefixes it exercised, and
       when there is no record to read.  An absent reading is not a
       reading that nothing moved.

    *changeset* is the digest of ``graded_sha..head_sha``, taken from the
    commit record; this function never asks a tree anything.
    """
    return (
        GradedState.lapsed
        if base_stale
        else GradedState.counted
        if graded_sha == head_sha
        else GradedState.lapsed
        if rederivation_class not in PATH_BOUND_CLASSES
        else GradedState.lapsed
        if changeset is None
        or any(
            _beneath(path, prefix)
            for path in changeset.file_paths
            for prefix in exercised_paths
        )
        else GradedState.counted
    )


def _beneath(path: str, prefix: str) -> bool:
    """Whether *path* is *prefix* itself or sits under it, by whole segments.

    A prefix matching a partial segment is not a hit: ``src/kodezart/dom``
    names no part of ``src/kodezart/domain/lapse.py``, and matching it
    would lapse gradings over a directory nobody touched.
    """
    stem = prefix.rstrip("/")
    return path == stem or path.startswith(f"{stem}/")
