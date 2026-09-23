"""The one rule: whether a grading taken at one commit still stands (KOD-696).

One arm per fixture, each stating its own inputs, so the arm that breaks
names itself.  The rule is pure arithmetic, so every fixture here is the
whole apparatus: no tracker, no workspace, no session.
"""

import ast
import inspect

import pytest

from kodezart.domain import lapse
from kodezart.domain.lapse import GradedState, graded_state, held_standing
from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import (
    CriterionCrossOff,
    CrossOffState,
    RederivationClass,
)
from kodezart.types.domain.criterion_ref import CriterionRef

GRADED = "a" * 40
HEAD = "b" * 40


def digest(*paths: str) -> ChangesetDigest:
    """A commit record's changed-path reading, as the rule takes it."""
    return ChangesetDigest(
        file_paths=list(paths), commit_subjects=["moved something"], commit_count=1
    )


def test_a_grading_at_the_head_it_is_read_against_counts():
    assert graded_state(graded_sha=GRADED, head_sha=GRADED) is GradedState.counted


def test_a_cheap_grading_lapses_as_soon_as_the_head_moves():
    """Nothing is asked about paths: re-deriving it is what it costs."""
    assert (
        graded_state(
            graded_sha=GRADED,
            head_sha=HEAD,
            rederivation_class=RederivationClass.cheap,
            exercised_paths=("src/kodezart/domain/",),
            changeset=digest("docs/architecture.md"),
        )
        is GradedState.lapsed
    )


def test_a_grading_that_declares_no_class_is_cheap_and_cannot_carry():
    """The default is what a verdict naming no class earns: no exemption."""
    assert (
        graded_state(
            graded_sha=GRADED, head_sha=HEAD, changeset=digest("docs/architecture.md")
        )
        is GradedState.lapsed
    )


@pytest.mark.parametrize(
    "rederivation_class", sorted(RederivationClass, key=lambda member: member.value)
)
def test_a_path_bound_grading_carries_when_nothing_it_exercised_moved(
    rederivation_class,
):
    """Only the two path-bound classes carry; the cheap one lapses regardless."""
    state = graded_state(
        graded_sha=GRADED,
        head_sha=HEAD,
        rederivation_class=rederivation_class,
        exercised_paths=("src/kodezart/domain/lapse.py",),
        changeset=digest("docs/architecture.md", "src/kodezart/chains/ralph_loop.py"),
    )
    expected = (
        GradedState.lapsed
        if rederivation_class is RederivationClass.cheap
        else GradedState.counted
    )
    assert state is expected


@pytest.mark.parametrize(
    "moved",
    [
        "src/kodezart/domain",
        "src/kodezart/domain/lapse.py",
        "src/kodezart/domain/nested/deeper.py",
    ],
)
def test_a_path_at_or_beneath_an_exercised_prefix_lapses_the_grading(moved):
    """Two prefixes are declared, and the one that moved is the second.

    ``one of its prefixes`` is a quantifier over everything the grading
    exercised, not a reading of the first thing it happens to name. The
    first prefix here is a directory nothing in the record touched, so a
    rule consulting only the first would carry a grading whose other
    prefix moved.
    """
    assert (
        graded_state(
            graded_sha=GRADED,
            head_sha=HEAD,
            rederivation_class=RederivationClass.expensive,
            exercised_paths=("src/kodezart/adapters", "src/kodezart/domain"),
            changeset=digest("docs/architecture.md", moved),
        )
        is GradedState.lapsed
    )


def test_a_prefix_matching_a_partial_segment_is_not_a_hit():
    """``dom`` names no part of ``domain``, and a partial match would lapse it."""
    assert (
        graded_state(
            graded_sha=GRADED,
            head_sha=HEAD,
            rederivation_class=RederivationClass.expensive,
            exercised_paths=("src/kodezart/dom",),
            changeset=digest("src/kodezart/domain/lapse.py"),
        )
        is GradedState.counted
    )


def test_a_trailing_separator_on_a_prefix_reads_the_same_as_one_without():
    moved = digest("src/kodezart/domain/lapse.py")
    assert graded_state(
        graded_sha=GRADED,
        head_sha=HEAD,
        rederivation_class=RederivationClass.expensive,
        exercised_paths=("src/kodezart/domain/",),
        changeset=moved,
    ) is graded_state(
        graded_sha=GRADED,
        head_sha=HEAD,
        rederivation_class=RederivationClass.expensive,
        exercised_paths=("src/kodezart/domain",),
        changeset=moved,
    )


def test_a_path_bound_grading_with_no_changed_path_reading_lapses():
    """An absent reading is not a reading that nothing moved."""
    assert (
        graded_state(
            graded_sha=GRADED,
            head_sha=HEAD,
            rederivation_class=RederivationClass.observed,
            exercised_paths=("src/kodezart/domain/",),
            changeset=None,
        )
        is GradedState.lapsed
    )


def test_an_empty_commit_record_carries_a_path_bound_grading():
    """A record that read no changed path is a reading, and it says nothing moved."""
    assert (
        graded_state(
            graded_sha=GRADED,
            head_sha=HEAD,
            rederivation_class=RederivationClass.observed,
            exercised_paths=("src/kodezart/domain/",),
            changeset=ChangesetDigest(
                file_paths=[], commit_subjects=[], commit_count=0
            ),
        )
        is GradedState.counted
    )


@pytest.mark.parametrize("member", sorted(GradedState, key=lambda one: one.value))
def test_the_reading_refuses_to_answer_as_a_truth_value(member):
    """``if graded_state(...)`` would pass silently for both members."""
    with pytest.raises(TypeError, match="not a truth value"):
        bool(member)


def test_a_reading_used_as_a_condition_refuses_rather_than_passing():
    with pytest.raises(TypeError, match="not a truth value"):
        if graded_state(graded_sha=GRADED, head_sha=HEAD):  # pragma: no branch
            pass


def test_the_lapse_module_imports_only_value_types_and_does_no_io():
    """The rule may reach value types and nothing else: no port, no adapter, no tree."""
    tree = ast.parse(inspect.getsource(lapse))
    modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert modules == {
        "collections.abc",
        "enum",
        "typing",
        "kodezart.types.domain.consolidation",
        "kodezart.types.domain.criterion_lifecycle",
    }
    assert not any(
        module is not None
        and (
            module.startswith("kodezart.adapters")
            or module == "kodezart.core.protocols"
        )
        for module in modules
    )
    assert not any(
        isinstance(node, (ast.Import, ast.AsyncFunctionDef, ast.Await))
        for node in ast.walk(tree)
    )
    forbidden = {"open", "print", "input", "__import__", "eval", "exec", "compile"}
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & forbidden


# ---------------------------------------------------------------------------
# The partition: what the next iteration is dispatched from (KOD-695).
# ---------------------------------------------------------------------------

EXERCISED = "src/kodezart/domain/"


def standing(
    key: str,
    *,
    state: CrossOffState = CrossOffState.passed,
    rederivation_class: RederivationClass = RederivationClass.cheap,
    exercised_paths: tuple[str, ...] = (),
    graded_sha: str = GRADED,
) -> CriterionCrossOff:
    """One cross-off an earlier iteration left standing."""
    return CriterionCrossOff(
        criterion=CriterionRef(key),
        state=state,
        evidence=CriterionEvidence(graded_sha=graded_sha, test=f"a pointer for {key}"),
        rederivation_class=rederivation_class,
        exercised_paths=exercised_paths,
    )


def keys_of(partition) -> dict[str, tuple[str, ...]]:
    """The partition as the criterion keys in each of its four fields."""
    return {
        field: tuple(str(cross_off.criterion) for cross_off in group)
        for field, group in partition._asdict().items()
    }


def test_every_prior_grading_lands_in_exactly_one_of_the_first_three_fields():
    """The partition is total and disjoint, and it keeps the order it was given."""
    prior = [
        standing("alpha"),
        standing("beta", state=CrossOffState.failed),
        standing(
            "gamma",
            rederivation_class=RederivationClass.expensive,
            exercised_paths=(EXERCISED,),
        ),
        standing("delta", state=CrossOffState.lapsed),
        standing(
            "epsilon",
            rederivation_class=RederivationClass.observed,
            exercised_paths=(EXERCISED,),
        ),
    ]
    partition = held_standing(
        base_stale=False,
        prior=prior,
        head_sha=HEAD,
        changesets={GRADED: digest(f"{EXERCISED}lapse.py")},
    )
    placed = keys_of(partition)
    landed = placed["carried"] + placed["rederive"] + placed["lapsed"]
    assert sorted(landed) == sorted(str(one.criterion) for one in prior)
    assert len(landed) == len(set(landed)) == len(prior)
    # Order is the order it arrived in, inside each field.
    assert placed["rederive"] == ("alpha", "beta", "gamma")
    assert placed["lapsed"] == ("delta", "epsilon")
    assert placed["carried"] == ()


def test_a_grading_whose_paths_did_not_move_is_carried_and_not_re_derived():
    partition = held_standing(
        base_stale=False,
        prior=[
            standing(
                "alpha",
                rederivation_class=RederivationClass.expensive,
                exercised_paths=(EXERCISED,),
            )
        ],
        head_sha=HEAD,
        changesets={GRADED: digest("docs/architecture.md")},
    )
    assert keys_of(partition) == {
        "carried": ("alpha",),
        "rederive": (),
        "lapsed": (),
        "newly_lapsed": (),
    }


def test_an_expensive_grading_whose_paths_moved_is_re_derived_not_lapsed():
    """The loop can grade it again, so it goes back to the session."""
    partition = held_standing(
        base_stale=False,
        prior=[
            standing(
                "alpha",
                rederivation_class=RederivationClass.expensive,
                exercised_paths=(EXERCISED,),
            )
        ],
        head_sha=HEAD,
        changesets={GRADED: digest(f"{EXERCISED}lapse.py")},
    )
    assert keys_of(partition) == {
        "carried": (),
        "rederive": ("alpha",),
        "lapsed": (),
        "newly_lapsed": (),
    }


def test_an_observed_grading_whose_paths_moved_lapses_and_is_newly_lapsed():
    """The loop cannot re-derive a performed observation, so it is not offered one."""
    partition = held_standing(
        base_stale=False,
        prior=[
            standing(
                "alpha",
                rederivation_class=RederivationClass.observed,
                exercised_paths=(EXERCISED,),
            )
        ],
        head_sha=HEAD,
        changesets={GRADED: digest(f"{EXERCISED}lapse.py")},
    )
    assert keys_of(partition) == {
        "carried": (),
        "rederive": (),
        "lapsed": ("alpha",),
        "newly_lapsed": ("alpha",),
    }


@pytest.mark.parametrize("base_stale", [False, True])
def test_held_standing_hands_the_base_reading_to_the_rule(base_stale):
    """The partition carries the caller's base reading to the rule unchanged.

    Graded at the head it is read at, a passing path-bound grading stands on
    every other arm, so only the base reading can move it: carried on a live
    base, re-derived (expensive) or lapsed (observed) on a stale one.
    """
    # Required and keyword-only, so no caller can drop the reading silently.
    parameter = inspect.signature(held_standing).parameters["base_stale"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    partition = held_standing(
        prior=[
            standing(
                "expensive",
                rederivation_class=RederivationClass.expensive,
                exercised_paths=(EXERCISED,),
                graded_sha=HEAD,
            ),
            standing(
                "observed",
                rederivation_class=RederivationClass.observed,
                exercised_paths=(EXERCISED,),
                graded_sha=HEAD,
            ),
        ],
        head_sha=HEAD,
        changesets={},
        base_stale=base_stale,
    )
    assert keys_of(partition) == (
        {
            "carried": (),
            "rederive": ("expensive",),
            "lapsed": ("observed",),
            "newly_lapsed": ("observed",),
        }
        if base_stale
        else {
            "carried": ("expensive", "observed"),
            "rederive": (),
            "lapsed": (),
            "newly_lapsed": (),
        }
    )


def test_a_grading_that_already_lapsed_stays_lapsed_and_is_not_newly_lapsed():
    """A grading that has lapsed does not un-lapse, and asks nothing twice."""
    partition = held_standing(
        base_stale=False,
        prior=[
            standing(
                "alpha",
                state=CrossOffState.lapsed,
                rederivation_class=RederivationClass.observed,
                exercised_paths=(EXERCISED,),
            )
        ],
        head_sha=GRADED,
        changesets={},
    )
    assert keys_of(partition) == {
        "carried": (),
        "rederive": (),
        "lapsed": ("alpha",),
        "newly_lapsed": (),
    }


def test_the_partition_asks_the_rule_once_per_standing_path_bound_grading():
    """A cheap grading needs no reading, so none is taken for it.

    Counted by the digests the partition actually consults: a reading it
    took for a cheap grading would be a read of a digest the caller did
    not even have to fetch.
    """
    consulted: list[str] = []

    class Counting(dict):
        def get(self, key, default=None):
            consulted.append(key)
            return super().get(key, default)

    prior = [
        standing("cheap-one"),
        standing(
            "bound-one",
            rederivation_class=RederivationClass.expensive,
            exercised_paths=(EXERCISED,),
        ),
        standing(
            "bound-two",
            rederivation_class=RederivationClass.observed,
            exercised_paths=(EXERCISED,),
            graded_sha="c" * 40,
        ),
        standing("failed-one", state=CrossOffState.failed),
    ]
    held_standing(
        base_stale=False,
        prior=prior,
        head_sha=HEAD,
        changesets=Counting({GRADED: digest("docs/architecture.md")}),
    )
    assert consulted == [GRADED, "c" * 40]


def test_a_grading_with_no_digest_for_its_own_sha_lapses():
    """An absent reading is not a reading that nothing moved, here too."""
    partition = held_standing(
        base_stale=False,
        prior=[
            standing(
                "alpha",
                rederivation_class=RederivationClass.observed,
                exercised_paths=(EXERCISED,),
            )
        ],
        head_sha=HEAD,
        changesets={},
    )
    assert keys_of(partition)["lapsed"] == ("alpha",)
