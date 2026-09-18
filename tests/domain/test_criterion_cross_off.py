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
from tests.identity_guards import model_value_sites

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


def source_tree() -> dict[str, str]:
    """The same tree, keyed the way the value guard's report names a module."""
    return {
        path.relative_to(SOURCE_ROOT).as_posix(): source
        for path, source in sources().items()
    }


#: The one function that mints a cross-off and the evidence inside it, and
#: the one that reads such an evidence value back out of a body.
BUILD_SITE = "domain/criterion_cross_off.py::cross_offs_for"
PARSE_SITE = "domain/criterion_evidence.py::parse_criterion_evidence"


@pytest.mark.parametrize(
    "identity,expected",
    [
        pytest.param(
            "CriterionCrossOff",
            {"build": (BUILD_SITE,), "parse": ()},
            id="the-cross-off",
        ),
        pytest.param(
            "CriterionEvidence",
            {"build": (BUILD_SITE,), "parse": (PARSE_SITE,)},
            id="its-evidence",
        ),
    ],
)
def test_exactly_one_site_constructs_a_cross_off_and_its_evidence(identity, expected):
    """The state and the sha are one value, built in one place.

    A second construction site is a second sha, and a second sha is the
    drift the single-writer rule exists to make impossible.

    What the guard covers: every module that imports or declares the value,
    reaches its name through a module it imports, or imports something whose
    own annotation carries it; inside such a module the class call, a
    subclass of it, the constructing and parsing methods however they are
    reached, an adapter or partial built around the class, ``type(x)(...)``,
    ``x.__class__(...)`` and a copy whose receiver its own function states no
    other type for. The cross-off is built and never parsed: the board
    carries the evidence row, not the verdict that produced it.

    What it does not see, and what review has to read from the code: a class
    or a method reached by runtime reflection, and a value rebuilt field by
    field into another model that renders the same bytes.
    """
    assert model_value_sites(source_tree(), identity=identity) == expected


@pytest.mark.parametrize(
    "form",
    [
        "CriterionEvidence(graded_sha=sha, test='second')",
        "CriterionEvidence.model_validate({'gradedSha': sha})",
        "CriterionEvidence.model_validate_json('{}')",
        "CriterionEvidence.model_construct(graded_sha=sha)",
        "evidence.model_copy(update={'graded_sha': sha})",
        "TypeAdapter(CriterionEvidence).validate_python({})",
        "partial(CriterionEvidence.model_validate)",
        "type(evidence)(graded_sha=sha, test='second')",
        "evidence.__class__(graded_sha=sha, test='second')",
    ],
)
def test_a_second_evidence_site_in_any_construction_form_is_reported(form):
    """A copy that re-mints the sha is the second sha, in the shape it takes."""
    tree = source_tree()
    tree["services/second_writer.py"] = (
        "from kodezart.types.domain.criterion_evidence import CriterionEvidence\n"
        "\n"
        "def _restamp(evidence, sha):\n"
        f"    return {form}\n"
    )

    sites = model_value_sites(tree, identity="CriterionEvidence")

    assert "services/second_writer.py::_restamp" in sites["build"] + sites["parse"]


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


def stage_names(tree: ast.Module, *, stage: str) -> set[str]:
    """Every name in *tree* that resolves to the *stage* member.

    The member reached as an attribute is the spelling the code uses; a
    name assigned from it is the same value under another word, and a guard
    reading only the attribute would be answered by binding it first.
    Grown to a fixed point, because an alias can precede its source.
    """
    names: set[str] = set()

    def resolves(node: ast.expr) -> bool:
        return ast.unparse(node).endswith(f".{stage}") or (
            isinstance(node, ast.Name) and node.id in names
        )

    changed = True
    while changed:
        previous = set(names)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and resolves(node.value):
                names.update(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and isinstance(node.target, ast.Name)
                and resolves(node.value)
            ):
                names.add(node.target.id)
        changed = names != previous
    return names


def stage_moves(tree: ast.Module, *, method: str, stage: str) -> list[str]:
    """Every definition in *tree* that moves an issue to the *stage* member."""
    where = qualified_names(tree)
    named = stage_names(tree, stage=stage)

    def is_stage(node: ast.expr) -> bool:
        return ast.unparse(node).endswith(f".{stage}") or (
            isinstance(node, ast.Name) and node.id in named
        )

    return sorted(
        {
            where[id(node)]
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and called_name(node) == method
            and any(
                word.arg == "stage" and is_stage(word.value) for word in node.keywords
            )
        }
    )


#: The stage as the source spells it, and one function moving an issue to it.
DONE = f"LifecycleStage.{LifecycleStage.DONE.name}"
MOVE = (
    "async def _move(tracker, key):\n"
    "{bound}"
    f"    await tracker.{LaneStateTracker.set_workflow_state.__name__}("
    "issue_key=key, stage={alias})\n"
)


@pytest.mark.parametrize(
    "module",
    [
        pytest.param(
            MOVE.format(bound=f"    done = {DONE}\n", alias="done"),
            id="a-name-bound-inside-the-function",
        ),
        pytest.param(
            f"FINISHED = {DONE}\n" + MOVE.format(bound="", alias="FINISHED"),
            id="a-module-level-name",
        ),
        pytest.param(
            f"FINISHED: LifecycleStage = {DONE}\n"
            + MOVE.format(bound="", alias="FINISHED"),
            id="an-annotated-module-level-name",
        ),
        pytest.param(
            MOVE.format(bound="", alias="LATER") + f"\nTHEN = {DONE}\nLATER = THEN\n",
            id="a-name-bound-after-its-use",
        ),
    ],
)
def test_a_move_to_done_made_under_an_alias_is_reported(module):
    """The guard reads the stage through the names a module binds it to.

    Without this the guard is answered by binding the member to a word
    first: a second writer that moves a criterion into the finished state
    under any of these forms would be invisible to the single-writer
    assertion below.
    """
    moves = stage_moves(
        ast.parse(module),
        method=LaneStateTracker.set_workflow_state.__name__,
        stage=LifecycleStage.DONE.name,
    )

    assert moves == ["_move"]


def test_exactly_one_function_applies_evidence_and_moves_a_criterion_to_done():
    """One function per half of a tick, and nothing else writes either half.

    The Evidence row is written by one function because a tick and the
    refutation that takes it back both say what the last grading of that
    criterion read (KOD-690); the move into the finished state is written by
    another, because only a tick makes it.

    What the guard covers: every ``.py`` file under ``src/kodezart/``,
    parsed, looking for calls named after the two halves as the code itself
    names them — ``apply_evidence.__name__`` and the lifecycle member's own
    name — so renaming either one moves the guard with it rather than
    leaving it scanning a name nobody calls. The stage is resolved through
    the names a module binds it to as well as through the attribute, so a
    move made under a local or module-level alias is seen.

    What it does not see, and what review has to read from the code: a call
    reached by reflection (``getattr(module, name)``), a second function
    that composes the Evidence row itself instead of calling the codec, a
    transition issued through ``restore_workflow_state``, which names a
    backend state rather than a lifecycle stage, and a stage reached
    through a function call or as an attribute of an object rather than
    bound to a name. The first two are covered from the other side by the
    construction-site guard above, since neither can produce a cross-off
    without building one.
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
        pytest.param(
            {"body": f"{body()}\n**Evidence:** what an earlier run recorded"},
            id="evidence-duplicated",
        ),
        pytest.param(
            {"body": f"{body()}\n**Do:** a second build it names"},
            id="do-duplicated",
        ),
        pytest.param(
            {"body": f"{body()}\n**Class:** one\n**Class:** another"},
            id="class-duplicated",
        ),
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
@pytest.mark.parametrize(
    "written",
    [
        pytest.param(body(), id="with-an-evidence-row"),
        pytest.param(
            f"**Check:** {CHECK}\n**Do:** the build it names",
            id="without-an-evidence-row",
        ),
    ],
)
def test_an_unstarted_or_completed_criterion_carrying_its_check_is_tickable(
    state, written
):
    """A sub-issue nothing has recorded Evidence on yet is tickable too.

    The tick appends the row it finds absent, so the condition the write
    depends on is that no template field is written twice, not that the
    Evidence row is already there.
    """
    kind, name = state
    require_tickable(
        issue=make_tracker_issue(
            KEY,
            parent_key="lane",
            issue_labels=frozenset({"criterion"}),
            body=written,
            state_kind=kind,
            state_name=name,
        ),
        criterion=criterion(),
    )
