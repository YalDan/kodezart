"""One site builds a cross-off, and one function puts it on a sub-issue."""

import ast
from pathlib import Path

import pytest

from kodezart.core.protocols import LaneStateTracker
from kodezart.domain.criterion_cross_off import (
    cross_offs_for,
    evaluation_observation,
    require_tickable,
    tick_anchor,
)
from kodezart.domain.criterion_evidence import apply_evidence, parse_criterion_evidence
from kodezart.domain.errors import StaleWriteError
from kodezart.domain.fire_spec import replace_criterion_fields
from kodezart.types.domain.agent import CriterionResult
from kodezart.types.domain.criteria import CriterionId, TrackerCriterion
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import CriterionCrossOff, CrossOffState
from kodezart.types.domain.operation import LifecycleStage
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import make_tracker_issue
from tests.identity_guards import construction_sites

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "kodezart"
OWNER = SOURCE_ROOT / "domain" / "criterion_cross_off.py"
WRITER = SOURCE_ROOT / "services" / "lane_state_writer.py"
GRADED_SHA = "9" * 40
KEY = "lane/first"
CHECK = "the check  this criterion states"


def criterion(*, text: str = CHECK) -> TrackerCriterion:
    return TrackerCriterion(id=CriterionId(KEY), text=text)


def body(*, check: str = CHECK, evidence: str = "—") -> str:
    return f"**Check:** {check}\n**Do:** the build it names\n**Evidence:** {evidence}"


def result(*, passed: bool = True) -> CriterionResult:
    return CriterionResult(
        criterion_id=CriterionId(KEY),
        criterion=CHECK,
        passed=passed,
        reasoning="Observed the selected check.",
    )


def sources() -> dict[Path, str]:
    """The production tree, as the text a static guard parses."""
    return {path: path.read_text() for path in sorted(SOURCE_ROOT.rglob("*.py"))}


@pytest.mark.parametrize("identity", ["CriterionCrossOff", "CriterionEvidence"])
def test_exactly_one_site_constructs_a_cross_off_and_its_evidence(identity):
    """The state and the sha are one value, built in one place.

    A second construction site is a second sha, and a second sha is the
    drift the single-writer rule exists to make impossible. The guard
    counts every form the value could be built by, including a copy or a
    parse, over the whole tree rather than a listed part of it.
    """
    sites = [
        (path, line)
        for path, source in sources().items()
        for line in construction_sites(source, identity=identity)
    ]

    assert [path for path, _ in sites] == [OWNER], sites
    second = f"{OWNER.read_text()}\n{identity}(criterion='second')\n"
    assert len(construction_sites(second, identity=identity)) == 2


def qualified_names(tree: ast.Module) -> dict[int, str]:
    """The dotted name of the definition each node in *tree* sits inside."""
    named: dict[int, str] = {}

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                inner = (*scope, child.name)
            named[id(child)] = ".".join(inner)
            walk(child, inner)

    walk(tree, ())
    return named


def called_name(node: ast.Call) -> str | None:
    """The name a call names, whether it is plain or reached as an attribute."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    return node.func.attr if isinstance(node.func, ast.Attribute) else None


def callers_of(tree: ast.Module, *, name: str) -> list[str]:
    """Every definition in *tree* that calls *name*, by its dotted name."""
    where = qualified_names(tree)
    return sorted(
        {
            where[id(node)]
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and called_name(node) == name
        }
    )


def stage_moves(tree: ast.Module, *, method: str, stage: str) -> list[str]:
    """Every definition in *tree* that moves an issue to the *stage* member."""
    where = qualified_names(tree)
    return sorted(
        {
            where[id(node)]
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and called_name(node) == method
            and any(
                word.arg == "stage" and ast.unparse(word.value).endswith(f".{stage}")
                for word in node.keywords
            )
        }
    )


def test_exactly_one_function_applies_evidence_and_moves_a_criterion_to_done():
    """One function per half of a tick, and nothing else writes either half.

    The Evidence row is written by one function because a tick and the
    refutation that takes it back both say what the last grading of that
    criterion read; the move into the finished state is written by another,
    because only a tick makes it.

    What the guard covers: every ``.py`` file under ``src/kodezart/``,
    parsed, looking for calls named after the two halves as the code itself
    names them — ``apply_evidence.__name__`` and the lifecycle member's own
    name — so renaming either one moves the guard with it rather than
    leaving it scanning a name nobody calls.

    What it does not see, and what review has to read from the code: a call
    reached by reflection (``getattr(module, name)``), a second function
    that composes the Evidence row itself instead of calling the codec, and
    a transition issued through ``restore_workflow_state``, which names a
    backend state rather than a lifecycle stage. The first two are covered
    from the other side by the construction-site guard above, since neither
    can produce a cross-off without building one.
    """
    trees = {path: ast.parse(source) for path, source in sources().items()}
    applying = {
        path: callers_of(tree, name=apply_evidence.__name__)
        for path, tree in trees.items()
    }
    moving = {
        path: stage_moves(
            tree,
            method=LaneStateTracker.set_workflow_state.__name__,
            stage=LifecycleStage.DONE.name,
        )
        for path, tree in trees.items()
    }

    assert {path: found for path, found in applying.items() if found} == {
        WRITER: ["TrackerLaneStateWriter._stamp"]
    }
    assert {path: found for path, found in moving.items() if found} == {
        WRITER: ["TrackerLaneStateWriter._write_one"]
    }


def test_applied_evidence_reads_back_and_changes_no_byte_outside_its_field():
    """The write half of the Evidence codec is the read half's inverse."""
    evidence = CriterionEvidence(graded_sha=GRADED_SHA, test="evaluator session s, 1")
    original = body(evidence="the evidence an earlier run recorded")

    applied = apply_evidence(body=original, evidence=evidence)

    assert parse_criterion_evidence(applied) == evidence
    blanked = {"Evidence": ""}
    assert replace_criterion_fields(applied, replacements=blanked) == (
        replace_criterion_fields(original, replacements=blanked)
    )
    assert GRADED_SHA in applied and len(GRADED_SHA) == 40


def test_a_cross_off_carries_the_attempts_sha_and_the_session_it_was_graded_in():
    observation = evaluation_observation(session_id="session-7", iteration=3)

    crossed = cross_offs_for(
        results=[result(), result(passed=False)],
        graded_sha=GRADED_SHA,
        observation=observation,
        demonstrated=True,
    )

    assert [cross_off.state for cross_off in crossed] == [
        CrossOffState.passed,
        CrossOffState.failed,
    ]
    assert {cross_off.evidence.graded_sha for cross_off in crossed} == {GRADED_SHA}
    assert {cross_off.evidence.test for cross_off in crossed} == {
        "evaluator session session-7, iteration 3"
    }
    assert all(
        isinstance(cross_off, CriterionCrossOff) and cross_off.criterion == KEY
        for cross_off in crossed
    )


@pytest.mark.parametrize(
    "issue",
    [
        pytest.param({"issue_labels": frozenset()}, id="classification-lost"),
        pytest.param(
            {"state_kind": WorkflowStateKind.STARTED, "state_name": "In Progress"},
            id="state-moved",
        ),
        pytest.param({"body": body(check="an amended Check")}, id="check-amended"),
        pytest.param({"body": "**Do:** a body that states no Check"}, id="check-lost"),
    ],
)
def test_a_sub_issue_the_verdict_no_longer_addresses_refuses_the_tick(issue):
    fields = {
        "parent_key": "lane",
        "issue_labels": frozenset({"criterion"}),
        "body": body(),
    }

    with pytest.raises(StaleWriteError) as caught:
        require_tickable(
            issue=make_tracker_issue(KEY, **(fields | issue)), criterion=criterion()
        )

    assert caught.value.target == KEY
    assert caught.value.expected == tick_anchor(criterion())


@pytest.mark.parametrize(
    "state",
    [
        (WorkflowStateKind.UNSTARTED, "Todo"),
        (WorkflowStateKind.COMPLETED, "Done"),
    ],
)
def test_an_unstarted_or_completed_criterion_carrying_its_check_is_tickable(state):
    kind, name = state
    require_tickable(
        issue=make_tracker_issue(
            KEY,
            parent_key="lane",
            issue_labels=frozenset({"criterion"}),
            body=body(),
            state_kind=kind,
            state_name=name,
        ),
        criterion=criterion(),
    )
