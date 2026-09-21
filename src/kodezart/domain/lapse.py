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

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import NamedTuple

from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.criterion_lifecycle import (
    PATH_BOUND_CLASSES,
    CriterionCrossOff,
    CrossOffState,
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
) -> GradedState:
    """Whether the grading taken at *graded_sha* still stands at *head_sha*.

    Three arms, in one expression, in this order:

    1. the same sha counts, because nothing has moved at all;
    2. a class outside :data:`PATH_BOUND_CLASSES` lapses on any head move:
       re-deriving it is cheap, so asking which paths moved buys nothing
       that re-deriving it would not answer better;
    3. a path-bound grading lapses when the changed paths of the commit
       record reach at or beneath one of the prefixes it exercised, and
       when there is no record to read.  An absent reading is not a
       reading that nothing moved.

    The base a grading was taken on is not among the inputs.  Whether the
    recorded base still holds is decided before any grading is read — a
    stale recorded base means no verdict may be computed against it at all,
    which is a refusal and not a reading of one grading (see
    :mod:`kodezart.domain.base_staleness` and
    :mod:`kodezart.domain.base_scope`).

    *changeset* is the digest of ``graded_sha..head_sha``, taken from the
    commit record; this function never asks a tree anything.
    """
    return (
        GradedState.counted
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


class HeldStanding(NamedTuple):
    """What an earlier iteration's gradings are worth to the next one.

    Every prior cross-off is in exactly one of the first three fields, in the
    order it arrived: what still stands, what this iteration may grade again,
    and what has lapsed and it may not. ``newly_lapsed`` is a subset of
    ``lapsed``, so a transition can be read without any arithmetic of the
    caller's own.
    """

    carried: tuple[CriterionCrossOff, ...]
    rederive: tuple[CriterionCrossOff, ...]
    lapsed: tuple[CriterionCrossOff, ...]
    newly_lapsed: tuple[CriterionCrossOff, ...]


def held_standing(
    *,
    prior: Sequence[CriterionCrossOff],
    head_sha: str,
    changesets: Mapping[str, ChangesetDigest],
) -> HeldStanding:
    """Partition *prior* by what each grading is still worth at *head_sha*.

    A grading that did not pass is owed again whatever moved, and so is a
    passing cheap one: re-deriving it costs nothing worth reasoning about.
    A passing path-bound grading is asked the rule once, with the changed
    paths of the commit record between its own sha and *head_sha*, and lands
    in ``carried`` when it still stands. When it does not, an expensive one
    goes to ``rederive`` — the loop can grade it again — and one resting on a
    performed observation goes to ``lapsed``, because the loop cannot.

    A grading already lapsed stays lapsed and never reaches ``rederive``: a
    grading that has lapsed does not un-lapse, and putting it back in front of
    the session would grade the very obligation the board now says is owed to
    somebody outside this loop.

    This function compares nothing itself. It asks the rule once per standing
    path-bound grading and reads the answer, which is what keeps one
    expression the only place the two revisions are weighed.
    """
    carried: list[CriterionCrossOff] = []
    rederive: list[CriterionCrossOff] = []
    lapsed: list[CriterionCrossOff] = []
    newly_lapsed: list[CriterionCrossOff] = []
    for cross_off in prior:
        if cross_off.state is CrossOffState.lapsed:
            lapsed.append(cross_off)
            continue
        if (
            cross_off.state is not CrossOffState.passed
            or cross_off.rederivation_class not in PATH_BOUND_CLASSES
        ):
            rederive.append(cross_off)
            continue
        state = graded_state(
            graded_sha=cross_off.evidence.graded_sha,
            head_sha=head_sha,
            rederivation_class=cross_off.rederivation_class,
            exercised_paths=cross_off.exercised_paths,
            changeset=changesets.get(cross_off.evidence.graded_sha),
        )
        if state is GradedState.counted:
            carried.append(cross_off)
        elif cross_off.rederivation_class is RederivationClass.observed:
            lapsed.append(cross_off)
            newly_lapsed.append(cross_off)
        else:
            rederive.append(cross_off)
    return HeldStanding(
        carried=tuple(carried),
        rederive=tuple(rederive),
        lapsed=tuple(lapsed),
        newly_lapsed=tuple(newly_lapsed),
    )
