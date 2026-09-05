"""Base-ref selection for the scope surfaces, and the stale-base refusal.

Every assertion is over the value the scope surfaces are handed, because
that is where the defect lived: no surface ever hardcoded a trunk — each
one used whatever base it was given, and nothing established what that
base should be.
"""

import pytest

from kodezart.domain.base_scope import changed_inputs, scope_base
from kodezart.domain.errors import StaleBaseError
from kodezart.types.domain.branch import (
    BaseInput,
    BaseSpec,
    WorkRefRole,
    trunk_base,
)
from kodezart.types.domain.workflow import ExecutionContext

BLOCKER_A = BaseInput(
    blocker_issue_id="KOD-101",
    branch="kodezart/blocker-a-11111111",
    sha="a" * 40,
)
BLOCKER_B = BaseInput(
    blocker_issue_id="KOD-102",
    branch="kodezart/blocker-b-22222222",
    sha="b" * 40,
)

STACKED = BaseSpec(
    base_branch="kodezart/blocker-a-11111111",
    base_role=WorkRefRole.DELIVERABLE,
    inputs=(BLOCKER_A,),
)
COMBINED = BaseSpec(
    base_branch="kodezart/integration-33333333",
    base_role=WorkRefRole.INTEGRATION,
    inputs=(BLOCKER_A, BLOCKER_B),
)


def _context(spec: BaseSpec) -> ExecutionContext:
    return ExecutionContext(
        prompt="do the thing",
        repo_path="/tmp/fake",
        cache_key="k",
        base_spec=spec,
        permission_mode="bypassPermissions",
        allowed_tools=["Bash"],
    )


# ---------------------------------------------------------------------------
# KOD-53/AC-22 — base-ref selection, stacked and non-stacked
# ---------------------------------------------------------------------------


def test_a_trunk_fired_lane_computes_against_trunk() -> None:
    """KOD-53/AC-22: the non-stacked case is not a special case, it is the trunk arm."""
    assert scope_base(trunk_base("main"), None) == "main"
    assert _context(trunk_base("main")).base_branch == "main"


def test_a_stacked_lane_computes_against_its_recorded_base() -> None:
    assert scope_base(STACKED, None) == "kodezart/blocker-a-11111111"
    assert _context(STACKED).base_branch == "kodezart/blocker-a-11111111"


def test_a_combined_base_is_read_exactly_like_a_single_one() -> None:
    """The role is carried so it can be REPORTED, never so it can branch."""
    assert scope_base(COMBINED, None) == "kodezart/integration-33333333"
    assert _context(COMBINED).base_branch == "kodezart/integration-33333333"
    assert COMBINED.base_role is WorkRefRole.INTEGRATION


def test_the_context_holds_no_base_of_its_own() -> None:
    """KOD-53/AC-26: one place a base enters a run, so two surfaces cannot disagree.

    ``base_branch`` is a property over the recorded base, not a field, so
    there is no constructor argument that could set it to something the
    recorded base does not say.
    """
    assert "base_branch" not in ExecutionContext.model_fields
    with pytest.raises(ValueError, match="baseSpec"):
        ExecutionContext.model_validate(
            {
                "prompt": "p",
                "repo_path": "/tmp/fake",
                "cache_key": "k",
                "base_branch": "main",
                "permission_mode": "bypassPermissions",
                "allowed_tools": [],
            },
        )


# ---------------------------------------------------------------------------
# KOD-53/AC-27 — a stale base is not a baseline
# ---------------------------------------------------------------------------


def test_a_live_base_produces_a_verdict() -> None:
    """The paired negative: recomputing the same spec changes nothing."""
    assert scope_base(STACKED, STACKED.model_copy(deep=True)) == STACKED.base_branch


@pytest.mark.parametrize(
    ("label", "implied"),
    [
        (
            "a blocker was added",
            BaseSpec(
                base_branch="kodezart/integration-33333333",
                base_role=WorkRefRole.INTEGRATION,
                inputs=(BLOCKER_A, BLOCKER_B),
            ),
        ),
        (
            "the blocker's deliverable ref was replaced",
            BaseSpec(
                base_branch="kodezart/blocker-a-99999999",
                base_role=WorkRefRole.DELIVERABLE,
                inputs=(
                    BLOCKER_A.model_copy(
                        update={"branch": "kodezart/blocker-a-99999999"},
                    ),
                ),
            ),
        ),
        (
            "an input ref advanced",
            BaseSpec(
                base_branch="kodezart/blocker-a-11111111",
                base_role=WorkRefRole.DELIVERABLE,
                inputs=(BLOCKER_A.model_copy(update={"sha": "c" * 40}),),
            ),
        ),
        ("the last blocker was removed", trunk_base("main")),
    ],
    ids=["added", "replaced", "advanced", "removed"],
)
def test_a_moved_base_refuses_instead_of_grading(
    label: str,
    implied: BaseSpec,
) -> None:
    """No scope verdict at all — never one graded against the stale ref."""
    with pytest.raises(StaleBaseError) as excinfo:
        scope_base(STACKED, implied)
    assert excinfo.value.recorded_ref == STACKED.base_branch, label
    assert excinfo.value.implied_ref == implied.base_branch, label


def test_the_refusal_names_what_moved() -> None:
    advanced = BLOCKER_A.model_copy(update={"sha": "c" * 40})
    implied = STACKED.model_copy(update={"inputs": (advanced,)})
    with pytest.raises(StaleBaseError) as excinfo:
        scope_base(STACKED, implied)
    named = excinfo.value.changed_inputs
    assert any(BLOCKER_A.sha in item for item in named)
    assert any(advanced.sha in item for item in named)


def test_changed_inputs_is_empty_for_two_equal_specs() -> None:
    assert changed_inputs(COMBINED, COMBINED.model_copy(deep=True)) == []
