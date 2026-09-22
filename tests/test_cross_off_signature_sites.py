"""A cross-off state never travels without the sha it was graded at (KOD-709).

A state is a claim about a criterion at one commit.  Carried on its own it is
a claim about the criterion at whatever commit the reader assumes, which is
how a pass recorded at one tree comes to stand for a tree nobody graded.  The
pair — the state and its evidence — is therefore the unit every signature
carries, and the model refuses a half: a tick built without a sha fails where
it is built rather than on the board.

Nothing is listed by hand that the tree can be asked for: the type names are
the production types' own, the field names are the pair model's own fields,
and the scanned surface is every module of the shipped package.  What IS
listed is the one exemption, with the reason it is one, checked against the
walk in both directions, so an exemption for a signature that no longer
exists is as red as an unexempted one.

The walk is textual and executes nothing, which is what lets it speak for the
whole tree.  Its blind spots, which review has to read from the code instead:
an unannotated parameter, and a state reached inside a container type the
annotation does not name.
"""

import ast
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import CriterionCrossOff, CrossOffState

#: The shipped package: the whole tree, derived, not a hand list.
SOURCE_ROOT = Path(sys.modules["kodezart"].__file__ or "").resolve().parent

#: The two halves' own type names, and the pair's own field names.
STATE = CrossOffState.__name__
PAIR = CriterionCrossOff.__name__
STATE_FIELD, EVIDENCE_FIELD = (
    next(
        name
        for name, field in CriterionCrossOff.model_fields.items()
        if field.annotation is half
    )
    for half in (CrossOffState, CriterionEvidence)
)

#: The one signature that carries a state alone, with the reason it may.
EXEMPT = {
    "domain/criterion_cross_off.py::cross_off_state": (
        "the arithmetic itself: it answers what one result is worth, and the "
        "one construction site pairs that answer with its evidence at once"
    ),
}

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
DEFINITIONS = (*FUNCTIONS, ast.ClassDef)


def local_bindings(tree: ast.AST, name: str) -> frozenset[str]:
    """Every local name that stands for the type called *name*.

    The name as imported, an ``import ... as alias`` rebinding, and a plain
    rebinding of either; the attribute form (``criterion_lifecycle.Name``) is
    matched on the attribute itself, so a module reaching the type through its
    module object needs no binding here at all.
    """
    bound = {name}
    changed = True
    while changed:
        previous = set(bound)
        for node in ast.walk(tree):
            if isinstance(node, ast.alias) and node.name.rpartition(".")[2] == name:
                bound.add(node.asname or name)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
                if node.value.id in bound:
                    bound.update(
                        target.id
                        for target in node.targets
                        if isinstance(target, ast.Name)
                    )
        changed = bound != previous
    return frozenset(bound)


def annotation_names(node: ast.expr | None) -> frozenset[str]:
    """Every type name this annotation spells, at any depth.

    A bare annotation, a container's element type, a union's member and a
    string annotation are all the same question: which types does this
    signature carry.
    """
    if node is None:
        return frozenset()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return frozenset()
    found: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name):
            found.add(inner.id)
        elif isinstance(inner, ast.Attribute):
            found.add(inner.attr)
    return frozenset(found)


def signature_annotations(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[frozenset[str]]:
    """The type names of every parameter and of the return, one set each."""
    arguments = function.args
    parameters = [
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
        *(
            [argument]
            for argument in (arguments.vararg, arguments.kwarg)
            if argument is not None
        ),
    ]
    flattened = [
        argument if isinstance(argument, ast.arg) else argument[0]
        for argument in parameters
    ]
    return [annotation_names(argument.annotation) for argument in flattened] + [
        annotation_names(function.returns)
    ]


def lone_state_signatures(tree: ast.AST) -> frozenset[str]:
    """Each signature carrying a state with no pair beside it, once."""
    states = local_bindings(tree, STATE)
    pairs = local_bindings(tree, PAIR)
    found: set[str] = set()

    def visit(scope: ast.AST, label: str | None) -> None:
        for child in ast.iter_child_nodes(scope):
            name = child.name if isinstance(child, DEFINITIONS) else None
            qualified = name if label is None else f"{label}.{name}"
            if isinstance(child, FUNCTIONS):
                annotations = signature_annotations(child)
                carries_state = any(spelled & states for spelled in annotations)
                carries_pair = any(spelled & pairs for spelled in annotations)
                if carries_state and not carries_pair:
                    found.add(qualified or "")
            if isinstance(child, DEFINITIONS):
                visit(child, qualified)
            else:
                visit(child, label)

    visit(tree, None)
    return frozenset(found)


def surface() -> frozenset[str]:
    """Every lone-state signature in the package, as module::qualname."""
    found: set[str] = set()
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        module = path.relative_to(SOURCE_ROOT).as_posix()
        found.update(
            f"{module}::{site}"
            for site in lone_state_signatures(ast.parse(path.read_text()))
        )
    return frozenset(found)


def test_the_names_the_walk_keys_on_are_the_types_own():
    """The halves and the pair's fields are read off production, not spelled."""
    assert (STATE_FIELD, EVIDENCE_FIELD) == ("state", "evidence")
    assert CriterionCrossOff.model_fields[STATE_FIELD].annotation is CrossOffState
    assert (
        CriterionCrossOff.model_fields[EVIDENCE_FIELD].annotation is CriterionEvidence
    )


def test_no_signature_carries_a_cross_off_state_apart_from_its_pair():
    found = surface()
    assert found == frozenset(EXEMPT), sorted(found ^ frozenset(EXEMPT))


def test_every_exemption_carries_the_reason_it_is_one():
    """A stale exemption reds: the table cannot outlive its signature."""
    assert frozenset(EXEMPT) <= surface(), sorted(frozenset(EXEMPT) - surface())
    assert all(reason.strip() for reason in EXEMPT.values())


def test_the_pair_binds_both_halves_and_neither_has_a_default():
    """The model is the boundary: a tick without a sha cannot be built.

    Both halves are required on a frozen model that forbids extras, and the
    sha the evidence carries is itself required and shaped, so neither half
    can be filled in later or left to a default nobody graded.
    """
    assert CriterionCrossOff.model_fields[STATE_FIELD].is_required()
    assert CriterionCrossOff.model_fields[EVIDENCE_FIELD].is_required()
    assert CriterionCrossOff.model_config["frozen"] is True
    assert CriterionCrossOff.model_config["extra"] == "forbid"
    graded = CriterionEvidence.model_fields["graded_sha"]
    assert graded.is_required()
    assert any(getattr(item, "pattern", None) for item in graded.metadata)

    with pytest.raises(ValidationError):
        CriterionCrossOff(criterion="C-1", state=CrossOffState.passed)
    with pytest.raises(ValidationError):
        CriterionEvidence(test="the case that graded it")


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "from kodezart.types.domain.criterion_lifecycle import CrossOffState\n"
            "def _tick(state: CrossOffState, sha: str) -> None:\n    return None\n",
            id="state-parameter",
        ),
        pytest.param(
            "from collections.abc import Sequence\n"
            "from kodezart.types.domain.criterion_lifecycle import CrossOffState\n"
            "async def _write(*, states: Sequence[CrossOffState]) -> None:\n"
            "    return None\n",
            id="sequence-of-states",
        ),
        pytest.param(
            "from kodezart.types.domain.criterion_lifecycle import CrossOffState\n"
            "class Writer:\n"
            "    def _verdict(self, passed: bool) -> CrossOffState | None:\n"
            "        return None\n",
            id="state-return-in-a-service",
        ),
        pytest.param(
            "from kodezart.types.domain.criterion_lifecycle import (\n"
            "    CrossOffState as Verdict,\n"
            ")\n"
            "def _tick(verdict: Verdict) -> None:\n    return None\n",
            id="alias-imported-state",
        ),
        pytest.param(
            "from kodezart.types.domain import criterion_lifecycle\n"
            "def _tick(verdict: criterion_lifecycle.CrossOffState) -> None:\n"
            "    return None\n",
            id="attribute-form-state",
        ),
    ],
)
def test_every_spelling_of_a_lone_state_is_reported(body):
    assert lone_state_signatures(ast.parse(body))


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "from kodezart.types.domain.criterion_lifecycle import (\n"
            "    CriterionCrossOff,\n"
            "    CrossOffState,\n"
            ")\n"
            "def _tick(cross_off: CriterionCrossOff, state: CrossOffState) -> None:\n"
            "    return None\n",
            id="state-beside-its-pair",
        ),
        pytest.param(
            "from kodezart.types.domain.criterion_lifecycle import CriterionCrossOff\n"
            "def _tick(cross_off: CriterionCrossOff) -> None:\n    return None\n",
            id="the-pair-alone",
        ),
    ],
)
def test_a_signature_carrying_the_pair_is_not_reported(body):
    assert not lone_state_signatures(ast.parse(body))
