"""One site builds a cross-off, and one function puts it on a sub-issue."""

import ast
from pathlib import Path

import pytest

from kodezart.core.protocols import LaneStateTracker
from kodezart.domain.criterion_cross_off import (
    CARRIED_REASON,
    LAPSE_POINTER,
    LAPSE_REASON,
    base_answers,
    base_reasons,
    cross_offs_for,
    declared_class,
    evaluation_observation,
    iteration_output,
    lapse_observation,
    passed_ids,
    require_tickable,
    tick_anchor,
    undemonstrated_reasons,
)
from kodezart.domain.criterion_evidence import apply_evidence, parse_criterion_evidence
from kodezart.domain.errors import StaleWriteError
from kodezart.domain.fire_spec import criterion_ref, replace_criterion_fields
from kodezart.domain.lapse import GradedState
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    BaseCheckOutput,
    BaseCheckResult,
    CriterionResult,
)
from kodezart.types.domain.criteria import CriterionId, TrackerCriterion
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import (
    PATH_BOUND_CLASSES,
    CriterionCrossOff,
    CrossOffState,
    RederivationClass,
    UndemonstratedReason,
)
from kodezart.types.domain.operation import LifecycleStage
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import make_tracker_issue
from tests.identity_guards import model_value_sites

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "kodezart"
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
    """Every definition in *tree* that calls *name*, by its dotted name.

    A member bound to a word and called under that word is the same call:
    ``mint = tracker.create_criterion_if_absent`` followed by ``await
    mint(...)`` is an ordinary second call site, not reflection, and a walk
    that compared the called name alone would be answered by binding the
    member first.  The word a class holds its collaborator under is the same
    binding written on ``self``, which is how this tree's own services hold
    theirs, so an alias is matched by the whole spelling of the call's target
    rather than by a bare name: ``self._mint(...)`` counts exactly as
    ``mint(...)`` does.  The aliases are resolved to a fixed point before the
    calls are counted, by the same resolution the stage guard below uses.

    Resolved within the one tree it is handed: a binding in one module called
    under its alias in another is not matched, and neither is a member handed
    on by any route ``stage_names`` does not record.  The criterion mint's
    one-caller count does not rest on this walk; it counts every naming of
    the member over the whole package instead (KOD-621).
    """
    where = qualified_names(tree)
    aliases = stage_names(tree, stage=name)
    return sorted(
        {
            where[id(node)]
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (called_name(node) == name or ast.unparse(node.func) in aliases)
        }
    )


def stage_names(tree: ast.Module, *, stage: str) -> set[str]:
    """Every spelling in *tree* that resolves to the *stage* member.

    The member reached as an attribute is the spelling the code uses; a
    name assigned from it is the same value under another word, and a guard
    reading only the attribute would be answered by binding it first.  A
    binding is recorded under the whole spelling of its target, so a
    collaborator held on an instance — ``self._mint = tracker.mint`` in an
    ``__init__``, which is how this tree's classes ordinarily hold theirs —
    is recorded as ``self._mint`` and is the member wherever that spelling is
    read.  Grown to a fixed point, because an alias can precede its source and
    an alias of an alias is the same value again.

    What it records is an assignment whose whole value is spelled as the
    member or as a recorded spelling.  What it does not record: the same
    value handed on by any other route — a walrus, a tuple or list unpacking,
    a parameter default, a ``partial``, a conditional whose last arm is not
    the member, an argument, a return, or a binding in another module.
    """
    names: set[str] = set()

    def resolves(node: ast.expr) -> bool:
        spelling = ast.unparse(node)
        return spelling.endswith(f".{stage}") or spelling in names

    def spelled(target: ast.expr) -> str | None:
        return (
            ast.unparse(target)
            if isinstance(target, ast.Name | ast.Attribute)
            else None
        )

    changed = True
    while changed:
        previous = set(names)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and resolves(node.value):
                names.update(
                    spelling
                    for target in node.targets
                    if (spelling := spelled(target)) is not None
                )
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and resolves(node.value)
                and (spelling := spelled(node.target)) is not None
            ):
                names.add(spelling)
        changed = names != previous
    return names


def stage_moves(tree: ast.Module, *, method: str, stage: str) -> list[str]:
    """Every definition in *tree* that moves an issue to the *stage* member."""
    where = qualified_names(tree)
    named = stage_names(tree, stage=stage)

    def is_stage(node: ast.expr) -> bool:
        spelling = ast.unparse(node)
        return spelling.endswith(f".{stage}") or spelling in named

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

    The Evidence row a body carries is applied by one function because a
    tick and the refutation that takes it back both say what the last
    grading of that criterion read (KOD-690), and both reach that row
    through it — the refutation asks it before its first write, because the
    edit is the precondition of making it (KOD-712). The move into the
    finished state is written by another, because only a tick makes it.

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
    through a function call, as an attribute of an object, or arriving as a
    parameter of the function that makes the move, rather than bound to a
    name. The first two are covered from the other side by the
    construction-site guard above, since neither can produce a cross-off
    without building one; the forwarded parameter is covered from the other
    side by the adoption register in ``tests/chains/test_write_back_adoption.py``,
    which reaches every production write of the port.
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
        WRITER: ["TrackerLaneStateWriter._evidence_body"]
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

    results = [result(), result(passed=False)]

    crossed = cross_offs_for(
        results=results,
        graded_sha=GRADED_SHA,
        observation=observation,
        reasons={},
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


#: The second criterion this module needs to tell one reading from another.
OTHER = "lane/second"
#: What a base reading reports as the command it ran for a criterion.
BASE_COMMAND = "ran the check this criterion names, at the base"


def base_result(*, key: str = KEY, satisfied: bool) -> BaseCheckResult:
    return BaseCheckResult(
        criterion_id=CriterionId(key), command=BASE_COMMAND, satisfied_at_base=satisfied
    )


def resolved(
    *, passed: bool, at_base: dict[CriterionId, bool], graded_tree_stood: bool
) -> CriterionCrossOff:
    """One result's cross-off, through the folds the evaluator step uses.

    In the evaluator step's order: the workspace reading first, whose
    withholding grades the result failed, then the base reading over the
    passes that are left — so a criterion names the first reading that came
    back empty for it, and only that one.
    """
    results = [result(passed=passed)]
    reasons = undemonstrated_reasons(
        results=results, workspace_stood=graded_tree_stood, surviving_checks=()
    )
    passing = passed_ids([one for one in results if one.criterion_id not in reasons])
    crossed = cross_offs_for(
        results=results,
        graded_sha=GRADED_SHA,
        observation=evaluation_observation(session_id="session-7", iteration=1),
        reasons={**reasons, **base_reasons(passing=passing, at_base=at_base)},
    )
    return crossed[0]


def state_of(
    *, passed: bool, at_base: dict[CriterionId, bool], graded_tree_stood: bool
) -> CrossOffState:
    """What one result is worth, through the fold the evaluator step uses."""
    return resolved(
        passed=passed, at_base=at_base, graded_tree_stood=graded_tree_stood
    ).state


@pytest.mark.parametrize(
    "passed,at_base,graded_tree_stood,expected",
    [
        pytest.param(
            True,
            {CriterionId(KEY): False},
            True,
            CrossOffState.passed,
            id="pass-failing-at-base",
        ),
        pytest.param(
            True,
            {CriterionId(KEY): True},
            True,
            CrossOffState.undemonstrated,
            id="pass-passing-at-base",
        ),
        pytest.param(
            True, {}, True, CrossOffState.undemonstrated, id="pass-unread-at-base"
        ),
        pytest.param(
            False,
            {CriterionId(KEY): True},
            True,
            CrossOffState.failed,
            id="fail-passing-at-base",
        ),
        pytest.param(
            False,
            {CriterionId(KEY): False},
            True,
            CrossOffState.failed,
            id="fail-failing-at-base",
        ),
        pytest.param(False, {}, True, CrossOffState.failed, id="fail-unread-at-base"),
        pytest.param(
            True,
            {CriterionId(KEY): False},
            False,
            CrossOffState.undemonstrated,
            id="tree-fell-over-under-a-pass",
        ),
        pytest.param(
            False,
            {CriterionId(KEY): False},
            False,
            CrossOffState.undemonstrated,
            id="tree-fell-over-under-a-fail",
        ),
    ],
)
def test_a_pass_stands_only_when_the_base_reading_found_the_check_failing_there(
    passed, at_base, graded_tree_stood, expected
):
    """The whole table, each expected member written out rather than derived.

    A pass is this branch's only where the base reading found that same check
    failing at the base: a check that already passed there is satisfied by
    every implementation including the empty one, so the head's pass is a
    reading of the base and not of the work. No reading at all fails closed
    for the same reason — the branch's contribution is what was not read.

    A fail stands on the graded tree alone, whatever the base says: a
    criterion this fire finished and has now broken is taken back, and a base
    reading cannot make a break into a non-break. And a grading read from a
    tree the sha does not name stands for nothing either way.
    """
    assert (
        state_of(passed=passed, at_base=at_base, graded_tree_stood=graded_tree_stood)
        is expected
    )


@pytest.mark.parametrize(
    "passed,at_base,graded_tree_stood,reason",
    [
        pytest.param(
            True,
            {CriterionId(KEY): False},
            True,
            None,
            id="pass-failing-at-base",
        ),
        pytest.param(
            True,
            {CriterionId(KEY): True},
            True,
            UndemonstratedReason.satisfied_at_base,
            id="pass-passing-at-base",
        ),
        pytest.param(
            True,
            {},
            True,
            UndemonstratedReason.base_reading_unsettled,
            id="pass-unread-at-base",
        ),
        pytest.param(
            False, {CriterionId(KEY): True}, True, None, id="fail-passing-at-base"
        ),
        pytest.param(False, {}, True, None, id="fail-unread-at-base"),
        pytest.param(
            True,
            {CriterionId(KEY): True},
            False,
            UndemonstratedReason.workspace_not_the_graded_sha,
            id="tree-fell-over-under-a-pass",
        ),
        pytest.param(
            False,
            {},
            False,
            UndemonstratedReason.workspace_not_the_graded_sha,
            id="tree-fell-over-under-a-fail",
        ),
    ],
)
def test_an_undemonstrated_resolution_names_the_reading_that_came_back_empty(
    passed, at_base, graded_tree_stood, reason
):
    """The same table, read for the reason the cross-off carries.

    A pass whose check already passed at the base names that base reading:
    the check was run there and found passing. A pass with no settled answer
    at the base names the base reading as unsettled instead — nothing was
    read there, so reporting it as satisfied at the base would misreport
    which reading came back empty. A tree the sha does not name is the first
    reading to fail, so it is the one named, whatever the base would have
    said. A verdict that stands names nothing.
    """
    assert (
        resolved(
            passed=passed, at_base=at_base, graded_tree_stood=graded_tree_stood
        ).undemonstrated_reason
        is reason
    )


def test_the_base_reasons_tell_a_check_passing_at_base_from_one_never_read_there():
    """Present-and-true, present-and-false and absent are three answers.

    Only the passes sent to the base are read; an id the reading answered
    that was not sent decides nothing, exactly as ``base_answers`` carries it.
    """
    satisfied, standing, unread = (
        CriterionId(KEY),
        CriterionId(OTHER),
        CriterionId("lane/third"),
    )

    assert base_reasons(
        passing=(satisfied, standing, unread),
        at_base={
            satisfied: True,
            standing: False,
            CriterionId("lane/never-sent"): True,
        },
    ) == {
        satisfied: UndemonstratedReason.satisfied_at_base,
        unread: UndemonstratedReason.base_reading_unsettled,
    }


def test_an_id_answered_twice_at_base_has_no_answer():
    """A reading contradicting itself settled nothing about that id.

    Neither answer stands, so the id has no reading and its head pass fails
    closed. The id answered once beside it still has its answer: what is
    dropped is the contradiction and not the whole reading.
    """
    answers = base_answers(
        BaseCheckOutput(
            base_check_results=[
                base_result(satisfied=True),
                base_result(satisfied=False),
                base_result(key=OTHER, satisfied=True),
            ]
        )
    )

    assert answers == {CriterionId(OTHER): True}


def test_an_id_nobody_dispatched_decides_nothing():
    """An id the reading invented is carried and matches no result.

    Carried rather than dropped: it matches no result the fold reads, so it
    decides nothing, and reconciling it here would be a second reconciliation
    of the dispatched set beside the one the grading already makes.
    """
    output = BaseCheckOutput(
        base_check_results=[
            base_result(satisfied=False),
            base_result(key="lane/never-dispatched", satisfied=True),
        ]
    )

    assert base_answers(output) == {
        CriterionId(KEY): False,
        CriterionId("lane/never-dispatched"): True,
    }
    assert (
        state_of(passed=True, at_base=base_answers(output), graded_tree_stood=True)
        is CrossOffState.passed
    )


def test_the_cross_offs_carry_what_the_fold_decided():
    """One evidence value for the attempt, one state per criterion.

    The sha and the session pointer are the attempt's, so every cross-off
    carries the same evidence; which readings stand is per criterion, so the
    states differ inside one call.
    """
    results = [
        result(),
        CriterionResult(
            criterion_id=CriterionId(OTHER),
            criterion=CHECK,
            passed=True,
            reasoning="Observed the selected check.",
        ),
    ]

    crossed = cross_offs_for(
        results=results,
        graded_sha=GRADED_SHA,
        observation=evaluation_observation(session_id="session-7", iteration=3),
        reasons={CriterionId(KEY): UndemonstratedReason.satisfied_at_base},
    )

    assert [cross_off.state for cross_off in crossed] == [
        CrossOffState.undemonstrated,
        CrossOffState.passed,
    ]
    assert [cross_off.undemonstrated_reason for cross_off in crossed] == [
        UndemonstratedReason.satisfied_at_base,
        None,
    ]
    assert len({cross_off.evidence for cross_off in crossed}) == 1
    assert [cross_off.criterion for cross_off in crossed] == [KEY, OTHER]


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


# ---------------------------------------------------------------------------
# A standing grading: counted unchanged, or lapsed beside the sha it holds.
# ---------------------------------------------------------------------------

STANDING_SHA = "1" * 40


def standing_cross_off(
    *,
    key: str = KEY,
    state: CrossOffState = CrossOffState.passed,
    rederivation_class: RederivationClass = RederivationClass.expensive,
    exercised_paths: tuple[str, ...] = ("src/kodezart/domain/",),
) -> CriterionCrossOff:
    """The cross-off an earlier attempt finished this criterion with."""
    return CriterionCrossOff(
        criterion=criterion_ref(CriterionId(key)),
        state=state,
        evidence=CriterionEvidence(
            graded_sha=STANDING_SHA,
            test=evaluation_observation(session_id="first-session", iteration=1),
        ),
        rederivation_class=rederivation_class,
        exercised_paths=exercised_paths,
    )


def for_reading(reading) -> tuple[CriterionCrossOff, ...]:
    """This attempt's cross-offs, given what it read about the standing one."""
    return cross_offs_for(
        results=[result()],
        graded_sha=GRADED_SHA,
        observation=evaluation_observation(session_id="second-session", iteration=2),
        reasons={},
        standing=[standing_cross_off()],
        reading=reading,
    )


def test_a_grading_the_reading_counts_is_returned_exactly_as_it_stands():
    """Arithmetic may not restate a verdict it did not reach.

    The reading says the earlier grading still stands, so what this attempt
    carries forward is that grading itself — its state, its sha and the
    pointer back to the session that produced it — and not a fresh row
    composed to look like one.
    """
    standing = standing_cross_off()
    assert for_reading({standing.criterion: GradedState.counted}) == (standing,)


def test_a_grading_the_reading_lapses_keeps_its_sha_and_says_it_lapsed():
    """The row records the lapse beside the sha the grading was taken at.

    The pointer is read twice over, because the two halves are different
    claims. That the builder routes through the one pointer function is the
    equality below, and it says nothing about what that function returns: a
    function handing its argument straight back would satisfy it. So the text
    is also read against the constant and against the grading it came from —
    a row saying it lapsed, still traceable to the session whose verdict did.
    """
    standing = standing_cross_off()
    (lapsed,) = for_reading({standing.criterion: GradedState.lapsed})
    assert lapsed.state is CrossOffState.lapsed
    assert lapsed.evidence.graded_sha == STANDING_SHA
    assert lapsed.evidence.test == lapse_observation(observation=standing.evidence.test)
    assert lapsed.evidence.test.endswith(LAPSE_POINTER)
    assert standing.evidence.test in lapsed.evidence.test
    assert lapsed.rederivation_class is standing.rederivation_class
    assert lapsed.exercised_paths == standing.exercised_paths


def test_the_words_a_lapse_leaves_a_reader_are_the_words_they_say():
    """The lapse prose is written out once, because nothing derives it (KOD-698).

    Every other reading of these two constants takes the constant itself, so
    a pointer reading that the grading is fine and a reason saying nothing at
    all satisfy all of them while the row tells a reader the opposite of what
    happened. What the criterion asks for is a gap a reader can SEE, so the
    words are spelled out here. A twin is the only reading that can red when
    fixed prose drifts, and these two are fixed texts rather than composed
    ones, so the twin costs one line each to keep.
    """
    assert LAPSE_POINTER == "that grading lapsed"
    assert LAPSE_REASON == (
        "lapsed: what this grading exercised moved after the sha it was "
        "graded at, so the verdict it reached is owed again rather than failed"
    )


def test_a_criterion_with_no_reading_is_built_from_this_attempts_own_grade():
    """The absent-reading arm is what every attempt without a standing one does."""
    assert for_reading({}) == cross_offs_for(
        results=[result()],
        graded_sha=GRADED_SHA,
        observation=evaluation_observation(session_id="second-session", iteration=2),
        reasons={},
    )


def test_a_reading_naming_a_criterion_nothing_stands_for_refuses():
    """The reading and the standing gradings are one partition, or neither."""
    with pytest.raises(ValueError, match="nothing is standing for"):
        cross_offs_for(
            results=[result()],
            graded_sha=GRADED_SHA,
            observation=evaluation_observation(session_id="s", iteration=2),
            reasons={},
            standing=[],
            reading={criterion_ref(CriterionId(KEY)): GradedState.counted},
        )


def test_a_lapsed_grading_round_trips_through_the_one_evidence_codec():
    """The row is one fenced record of a sha and a pointer, read back as itself."""
    (lapsed,) = for_reading({criterion_ref(CriterionId(KEY)): GradedState.lapsed})
    written = apply_evidence(body=body(), evidence=lapsed.evidence)
    assert parse_criterion_evidence(written) == lapsed.evidence


@pytest.mark.parametrize(
    "rederivation_class", sorted(PATH_BOUND_CLASSES, key=lambda one: one.value)
)
@pytest.mark.parametrize(
    "paths", [pytest.param((), id="none"), pytest.param((" ",), id="blank")]
)
def test_a_path_bound_class_naming_no_prefix_earns_neither(rederivation_class, paths):
    """The exemption and the prefixes that end it are declared together or not at all.

    The cross-off model refuses that pair outright, so a declaration read
    straight through would raise inside the write and take the fire with it.
    """
    assert declared_class(
        rederivation_class=rederivation_class, exercised_paths=paths
    ) == (RederivationClass.cheap, ())


@pytest.mark.parametrize(
    "rederivation_class", sorted(RederivationClass, key=lambda one: one.value)
)
def test_a_declaration_naming_its_prefixes_is_read_as_it_was_declared(
    rederivation_class,
):
    assert declared_class(
        rederivation_class=rederivation_class,
        exercised_paths=["src/kodezart/domain/", "docs/architecture.md"],
    ) == (rederivation_class, ("src/kodezart/domain/", "docs/architecture.md"))


def test_a_normalised_declaration_builds_a_cross_off_the_model_accepts():
    """The whole point: the pair the model raises on never reaches it."""
    normalised, paths = declared_class(
        rederivation_class=RederivationClass.observed, exercised_paths=()
    )
    built = CriterionCrossOff(
        criterion=criterion_ref(CriterionId(KEY)),
        state=CrossOffState.passed,
        evidence=CriterionEvidence(graded_sha=GRADED_SHA, test="a pointer"),
        rederivation_class=normalised,
        exercised_paths=paths,
    )
    assert built.rederivation_class is RederivationClass.cheap


@pytest.mark.parametrize(
    "rederivation_class", sorted(PATH_BOUND_CLASSES, key=lambda one: one.value)
)
def test_a_declaration_that_earns_no_class_reaches_the_board_as_cheap(
    rederivation_class,
):
    """The normalisation is on the path a session's own answer travels (KOD-696).

    The unit rows above pin what the normalisation returns; this pins that
    the one production site that builds a cross-off asks it. Reading a
    session's declaration straight through would hand the cross-off model a
    path-bound class with no prefixes — the pair it refuses — and the
    refusal would come out of the write rather than out of the answer,
    taking the whole fire down over one criterion.
    """
    (crossed,) = cross_offs_for(
        results=[
            CriterionResult(
                criterion_id=CriterionId(KEY),
                criterion=CHECK,
                passed=True,
                reasoning="Observed the selected check.",
                rederivation_class=rederivation_class,
                exercised_paths=(),
            )
        ],
        graded_sha=GRADED_SHA,
        observation=evaluation_observation(session_id="session-7", iteration=3),
        reasons={},
        standing=(),
        reading={},
    )

    assert crossed.rederivation_class is RederivationClass.cheap
    assert crossed.exercised_paths == ()
    assert crossed.state is CrossOffState.passed


# ---------------------------------------------------------------------------
# The whole roster's reading: this session's rows beside the standing ones.
# ---------------------------------------------------------------------------

SECOND_KEY = "lane/second"
SECOND_CHECK = "the check the second criterion states"
UNKNOWN_KEY = "lane/nobody-dispatched"


def two_criterion_roster() -> tuple[TrackerCriterion, TrackerCriterion]:
    """The roster the gate and the trajectory read, whatever was dispatched."""
    return (
        criterion(),
        TrackerCriterion(id=CriterionId(SECOND_KEY), text=SECOND_CHECK),
    )


def session_row(*, key: str, text: str, passed: bool = True) -> CriterionResult:
    """One row the evaluation session itself answered."""
    return CriterionResult(
        criterion_id=CriterionId(key),
        criterion=text,
        passed=passed,
        reasoning="Observed the selected check.",
    )


def graded_output(*rows: CriterionResult) -> AcceptanceCriteriaOutput:
    return AcceptanceCriteriaOutput(criteria_results=list(rows))


@pytest.mark.parametrize(
    "state,passed,reason",
    [
        pytest.param(GradedState.counted, True, CARRIED_REASON, id="still-standing"),
        pytest.param(GradedState.lapsed, False, LAPSE_REASON, id="lapsed"),
    ],
)
def test_the_roster_row_a_withheld_criterion_earns_reads_its_standing_grading(
    state, passed, reason
):
    """A lapse is not a pass, and the row the gate reads has to say which (KOD-695).

    The gate and the trajectory read the roster entire, so the denominator
    cannot move with whatever subset the session was handed. A withheld
    criterion therefore gets the row its standing grading earns — and a
    lapse read as a pass is a complete, clearing roster over an obligation
    the board is at the same moment holding in Todo.

    The class and the prefixes come from the standing grading too, so the
    next iteration reads the same declaration back rather than whatever a
    later session would have declared.
    """
    standing = standing_cross_off(
        rederivation_class=RederivationClass.observed,
        exercised_paths=("docs/architecture.md",),
    )

    output = iteration_output(
        criteria=two_criterion_roster(),
        standing=[standing],
        reading={standing.criterion: state},
        graded=graded_output(session_row(key=SECOND_KEY, text=SECOND_CHECK)),
    )

    withheld, graded = output.criteria_results
    assert withheld.criterion_id == KEY
    assert withheld.passed is passed
    assert withheld.reasoning == reason
    assert withheld.criterion == CHECK
    assert withheld.rederivation_class is RederivationClass.observed
    assert withheld.exercised_paths == ("docs/architecture.md",)
    assert (graded.criterion_id, graded.passed) == (SECOND_KEY, True)


def test_a_session_row_for_a_withheld_criterion_does_not_override_the_harness_row():
    """A session does not get to answer an obligation it was not handed.

    The harness's reading of a withheld criterion is arithmetic over what
    moved since its grading. A session row for it is an answer to a question
    nobody asked, and letting it through would let a hallucinated pass
    overwrite a lapse.
    """
    standing = standing_cross_off()

    output = iteration_output(
        criteria=two_criterion_roster(),
        standing=[standing],
        reading={standing.criterion: GradedState.lapsed},
        graded=graded_output(
            session_row(key=KEY, text=CHECK, passed=True),
            session_row(key=SECOND_KEY, text=SECOND_CHECK),
        ),
    )

    rows = [row for row in output.criteria_results if row.criterion_id == KEY]
    assert [(row.passed, row.reasoning) for row in rows] == [(False, LAPSE_REASON)]


def test_a_duplicate_row_and_a_row_for_an_undispatched_id_are_passed_through():
    """Both are the reconciler's to report, and filtering them here hides them.

    A second row for one id and a row for an id nobody dispatched are how a
    hallucinated roster shows itself. Dropping them here would leave it
    looking complete.
    """
    standing = standing_cross_off()

    output = iteration_output(
        criteria=two_criterion_roster(),
        standing=[standing],
        reading={standing.criterion: GradedState.counted},
        graded=graded_output(
            session_row(key=SECOND_KEY, text=SECOND_CHECK),
            session_row(key=SECOND_KEY, text=SECOND_CHECK, passed=False),
            session_row(key=UNKNOWN_KEY, text="a check no roster carries"),
        ),
    )

    assert [(row.criterion_id, row.passed) for row in output.criteria_results] == [
        (KEY, True),
        (SECOND_KEY, True),
        (SECOND_KEY, False),
        (UNKNOWN_KEY, True),
    ]
