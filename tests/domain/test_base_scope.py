"""Base-ref selection for the scope surfaces, and the stale-base refusal.

Every assertion is over the value the scope surfaces are handed, because
that is where the defect lived: no surface ever hardcoded a trunk — each
one used whatever base it was given, and nothing established what that
base should be.
"""

import ast
from pathlib import Path

import pytest

from kodezart.domain.base_scope import changed_inputs, scope_base
from kodezart.domain.errors import StaleBaseError
from kodezart.types.domain.base_spec import (
    BaseInput,
    BaseRefRole,
    BaseSpec,
    trunk_base,
)
from kodezart.types.domain.workflow import ExecutionContext

REPO_ROOT = Path(__file__).resolve().parents[2]

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
    base_ref="kodezart/blocker-a-11111111",
    role=BaseRefRole.deliverable,
    inputs=(BLOCKER_A,),
)
COMBINED = BaseSpec(
    base_ref="kodezart/integration-33333333",
    role=BaseRefRole.integration,
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
    assert COMBINED.role is BaseRefRole.integration


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


#: String surgery that could turn a ref's NAME into a base.
_DISSECTORS = frozenset(
    {"split", "rsplit", "partition", "rpartition", "removeprefix", "removesuffix"},
)
#: Regex entry points, whose subject is an argument rather than a receiver.
_MATCHERS = frozenset({"match", "fullmatch", "search"})
#: What an expression must mention for its dissection to be about a ref.
_REF_WORDS = ("base", "branch", "ref")


def _names_a_ref(segment: str | None) -> bool:
    return segment is not None and any(
        word in segment.lower() for word in _REF_WORDS
    )


def ref_parsing_sites(source: str) -> list[str]:
    """Every place *source* derives a value from the TEXT of a ref.

    Narrow on purpose, in both directions.  A substring scan over a module
    fires on any string handling it happens to contain and misses a base
    obtained by slicing; this reads the syntax tree and asks the question
    the criterion asks — is a ref-named expression being taken apart — so
    unrelated text is not an offender and index surgery is.
    """
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver = ast.get_source_segment(source, node.func.value)
            if node.func.attr in _DISSECTORS and _names_a_ref(receiver):
                offenders.append(f"line {node.lineno}: .{node.func.attr}(")
            if node.func.attr in _MATCHERS and any(
                _names_a_ref(ast.get_source_segment(source, argument))
                for argument in node.args
            ):
                offenders.append(f"line {node.lineno}: regex over a ref")
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            if _names_a_ref(ast.get_source_segment(source, node.value)):
                offenders.append(f"line {node.lineno}: slice of a ref")
    return offenders


def test_the_ref_parsing_detector_fires_on_the_thing_it_is_looking_for() -> None:
    """The guard below is only worth its green if it can go red.

    Three positives, one of which the substring scan this replaced could
    not see at all — a base obtained by slicing — and two negatives that
    the substring scan would have called offenders.
    """
    assert ref_parsing_sites("head = ctx.base_branch.split('/')[0]")
    assert ref_parsing_sites("import re\nre.match(r'kodezart/(.*)', base_ref)")
    assert ref_parsing_sites("head = recorded_base[len('kodezart/') :]")

    assert ref_parsing_sites("lines = commit_message.split('\\n')") == []
    assert ref_parsing_sites("head = summary[:80]") == []


def test_no_scope_surface_parses_a_branch_name_to_obtain_a_base() -> None:
    """KOD-53/AC-26: the name is not the record — nothing derives a base from one.

    The list is the scope surfaces themselves, not only the modules that
    model a base: the changeset digest and the outer review diff are
    where a parsed base would actually be spent, so a check that stopped
    at the domain modules would leave the two consumers unpinned.
    """
    modules = (
        "src/kodezart/domain/base_scope.py",
        "src/kodezart/types/domain/base_spec.py",
        "src/kodezart/chains/ralph_loop.py",
        "src/kodezart/chains/ralph_workflow.py",
    )
    offenders = {
        name: sites
        for name in modules
        if (
            sites := ref_parsing_sites(
                (REPO_ROOT / name).read_text(encoding="utf-8"),
            )
        )
    }
    assert offenders == {}, f"a base is derived from a ref's name: {offenders}"


# ---------------------------------------------------------------------------
# KOD-53/AC-27 — a stale base is not a baseline
# ---------------------------------------------------------------------------


def test_a_live_base_produces_a_verdict() -> None:
    """The paired negative: recomputing the same spec changes nothing."""
    assert scope_base(STACKED, STACKED.model_copy(deep=True)) == STACKED.base_ref


@pytest.mark.parametrize(
    ("label", "implied"),
    [
        (
            "a blocker was added",
            BaseSpec(
                base_ref="kodezart/integration-33333333",
                role=BaseRefRole.integration,
                inputs=(BLOCKER_A, BLOCKER_B),
            ),
        ),
        (
            "the blocker's deliverable ref was replaced",
            BaseSpec(
                base_ref="kodezart/blocker-a-99999999",
                role=BaseRefRole.deliverable,
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
                base_ref="kodezart/blocker-a-11111111",
                role=BaseRefRole.deliverable,
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
    assert excinfo.value.recorded_ref == STACKED.base_ref, label
    assert excinfo.value.implied_ref == implied.base_ref, label


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
