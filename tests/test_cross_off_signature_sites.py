"""A cross-off state never travels without the sha it was graded at (KOD-709).

A state is a claim about a criterion at one commit.  Carried on its own it is
a claim about the criterion at whatever commit the reader assumes, which is
how a pass recorded at one tree comes to stand for a tree nobody graded.  The
pair — the state and its evidence — is therefore the unit every signature
carries, and the model refuses a half: a tick built without a sha fails where
it is built rather than on the board.

Nothing is listed by hand that the tree can be asked for: the pair is the
production type's own name, the two halves are the types of the pair model's
own state and evidence fields, and the scanned surface is every module of the
shipped package.  What IS listed is the one exemption, with the reason it is
one, checked against the walk in both directions, so an exemption for a
signature that no longer exists is as red as an unexempted one.

Three shapes carry a state.  A function signature carries one unless the pair
is beside it; its return is judged on its own, so pairs going in and a lone
state coming out is still a state leaving without its sha.  A class carries
one in an annotated field — a model, a dataclass or a ``NamedTuple`` field is
a constructor parameter — unless another field carries the evidence or the
pair.  And a local name carries the state wherever it is bound to it: an
import alias, an assignment whose value names it (``tuple[State, ...]``, a
union, ``Annotated[...]``), a ``TypeAlias`` annotation, or a ``type``
statement.

The walk is textual and executes nothing, which is what lets it speak for the
whole tree.  Its blind spots, which review has to read from the code instead:
an unannotated parameter, and a state carried as its ``str`` value rather
than as its type.
"""

import ast
import inspect
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import CriterionCrossOff, CrossOffState

#: The shipped package: the whole tree, derived, not a hand list.
SOURCE_ROOT = Path(sys.modules["kodezart"].__file__ or "").resolve().parent

#: The pair's own field names for its two halves, found by the halves' types.
STATE_FIELD, EVIDENCE_FIELD = (
    next(
        name
        for name, field in CriterionCrossOff.model_fields.items()
        if field.annotation is half
    )
    for half in (CrossOffState, CriterionEvidence)
)


def _field_type(field: str) -> str:
    """The name of the type the pair model declares for *field*."""
    annotation = CriterionCrossOff.model_fields[field].annotation
    return getattr(annotation, "__name__", "")


#: The two halves' type names, read off the pair model's own fields, and the
#: pair's own name.
STATE = _field_type(STATE_FIELD)
EVIDENCE = _field_type(EVIDENCE_FIELD)
PAIR = CriterionCrossOff.__name__

#: The one signature that carries a state alone, with the reason it may.
EXEMPT = {
    "domain/criterion_cross_off.py::cross_off_state": (
        "the arithmetic itself: it answers what one result is worth, and the "
        "one construction site pairs that answer with its evidence at once"
    ),
}

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
DEFINITIONS = (*FUNCTIONS, ast.ClassDef)
#: The annotation that makes an annotated assignment a type alias.
TYPE_ALIAS = "TypeAlias"


def local_bindings(tree: ast.AST, name: str) -> frozenset[str]:
    """Every local name that stands for the type called *name*.

    The name as imported and an ``import ... as alias`` rebinding; an
    assignment whose value names a bound type anywhere in it (a plain
    rebinding, ``tuple[State, ...]``, ``State | None``, ``Annotated[...]``);
    an ``X: TypeAlias = ...`` annotation; and a PEP 695 ``type X = ...``
    statement.  Grown to a fixed point, so an alias of an alias is one.  The
    attribute form (``criterion_lifecycle.Name``) is matched on the attribute
    itself, so a module reaching the type through its module object needs no
    binding here at all.
    """
    bound = {name}
    changed = True
    while changed:
        previous = set(bound)
        for node in ast.walk(tree):
            if isinstance(node, ast.alias) and node.name.rpartition(".")[2] == name:
                bound.add(node.asname or name)
            elif isinstance(node, ast.Assign) and annotation_names(node.value) & bound:
                bound.update(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and TYPE_ALIAS in annotation_names(node.annotation)
                and annotation_names(node.value) & bound
            ):
                bound.add(node.target.id)
            elif (
                isinstance(node, ast.TypeAlias) and annotation_names(node.value) & bound
            ):
                bound.add(node.name.id)
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


def parameter_annotations(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[frozenset[str]]:
    """The type names of every parameter, one set each."""
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
    return [annotation_names(argument.annotation) for argument in flattened]


def carries_a_lone_state(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    states: frozenset[str],
    pairs: frozenset[str],
) -> bool:
    """Whether this signature lets a state in or out without its pair.

    The return is judged on its own: a state returned is lone unless the
    return itself carries the pair.  A state taken as a parameter is lone
    unless the pair is somewhere in the signature.
    """
    parameters = parameter_annotations(function)
    returned = annotation_names(function.returns)
    if returned & states and not returned & pairs:
        return True
    carries_pair = returned & pairs or any(spelled & pairs for spelled in parameters)
    return any(spelled & states for spelled in parameters) and not carries_pair


def field_annotations(cls: ast.ClassDef) -> list[frozenset[str]]:
    """The type names of every annotated field in the class body, one set each."""
    return [
        annotation_names(statement.annotation)
        for statement in cls.body
        if isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
    ]


def carries_a_lone_state_field(
    cls: ast.ClassDef, *, states: frozenset[str], evidence: frozenset[str]
) -> bool:
    """Whether a field carries a state and no field carries its evidence."""
    fields = field_annotations(cls)
    return any(spelled & states for spelled in fields) and not any(
        spelled & evidence for spelled in fields
    )


def lone_state_signatures(tree: ast.AST) -> frozenset[str]:
    """Each signature or class carrying a state with no pair beside it, once."""
    states = local_bindings(tree, STATE)
    pairs = local_bindings(tree, PAIR)
    evidence = local_bindings(tree, EVIDENCE) | pairs
    found: set[str] = set()

    def visit(scope: ast.AST, label: str | None) -> None:
        for child in ast.iter_child_nodes(scope):
            name = child.name if isinstance(child, DEFINITIONS) else None
            qualified = name if label is None else f"{label}.{name}"
            if isinstance(child, FUNCTIONS) and carries_a_lone_state(
                child, states=states, pairs=pairs
            ):
                found.add(qualified or "")
            if isinstance(child, ast.ClassDef) and carries_a_lone_state_field(
                child, states=states, evidence=evidence
            ):
                found.add(qualified or "")
            if isinstance(child, DEFINITIONS):
                visit(child, qualified)
            else:
                visit(child, label)

    visit(tree, None)
    return frozenset(found)


def surface(root: Path) -> frozenset[str]:
    """Every lone-state signature under *root*, as module::qualname."""
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        found.update(
            f"{module}::{site}"
            for site in lone_state_signatures(ast.parse(path.read_text()))
        )
    return frozenset(found)


def test_the_pair_model_is_what_the_field_rule_reads():
    """The walk keys on the pair model's own fields, read from its source.

    The pair's class body, as the walk parses it, holds a field of the state
    type and a field of the evidence type, so it is the one shape the field
    rule accepts; the same class with its evidence field removed is reported.
    """
    source = inspect.getsource(CriterionCrossOff)
    [pair] = [node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)]
    fields = {
        statement.target.id: annotation_names(statement.annotation)
        for statement in pair.body
        if isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
    }
    assert STATE in fields[STATE_FIELD]
    assert EVIDENCE in fields[EVIDENCE_FIELD]
    halves = {"states": frozenset({STATE}), "evidence": frozenset({EVIDENCE})}
    assert not carries_a_lone_state_field(pair, **halves)
    pair.body = [
        statement
        for statement in pair.body
        if not (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == EVIDENCE_FIELD
        )
    ]
    assert carries_a_lone_state_field(pair, **halves)


def test_no_signature_carries_a_cross_off_state_apart_from_its_pair():
    found = surface(SOURCE_ROOT)
    assert found == frozenset(EXEMPT), sorted(found ^ frozenset(EXEMPT))


def test_every_exemption_carries_the_reason_it_is_one():
    """A stale exemption reds: the table cannot outlive its signature."""
    found = surface(SOURCE_ROOT)
    assert frozenset(EXEMPT) <= found, sorted(frozenset(EXEMPT) - found)
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
    # The sha's shape is asserted by what the model does with one: forty hex
    # digits are a sha, and an empty value or no value at all is not.
    forty_hex = "0123456789abcdef" * 2 + "01234567"
    assert CriterionEvidence(graded_sha=forty_hex, test="t").graded_sha == forty_hex
    for missing in ("", None):
        with pytest.raises(ValidationError):
            CriterionEvidence.model_validate({"gradedSha": missing, "test": "t"})

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
        pytest.param(
            "from kodezart.types.domain.criterion_lifecycle import CrossOffState\n"
            "_Verdicts = tuple[CrossOffState, ...]\n"
            "def _f(verdicts: _Verdicts) -> None:\n    return None\n",
            id="a-tuple-alias",
        ),
        pytest.param(
            "from typing import TypeAlias\n"
            "from kodezart.types.domain.criterion_lifecycle import CrossOffState\n"
            "_Verdict: TypeAlias = CrossOffState\n"
            "def _f(verdict: _Verdict) -> None:\n    return None\n",
            id="a-typealias-annotation",
        ),
        pytest.param(
            "from kodezart.types.domain.criterion_lifecycle import CrossOffState\n"
            "type _Verdict = CrossOffState\n"
            "def _tick(verdict: _Verdict, sha: str) -> None:\n    return None\n",
            id="a-type-statement",
        ),
        pytest.param(
            "from collections.abc import Sequence\n"
            "from kodezart.types.domain.criterion_lifecycle import (\n"
            "    CriterionCrossOff,\n"
            "    CrossOffState,\n"
            ")\n"
            "def _f(cross_offs: Sequence[CriterionCrossOff]) -> CrossOffState:\n"
            "    return cross_offs[0].state\n",
            id="pairs-in-a-lone-state-out",
        ),
        pytest.param(
            "from kodezart.types.base import CamelCaseModel\n"
            "from kodezart.types.domain.criterion_lifecycle import (\n"
            "    CriterionRef,\n"
            "    CrossOffState,\n"
            ")\n"
            "class CrossOffVerdict(CamelCaseModel):\n"
            "    criterion: CriterionRef\n"
            "    state: CrossOffState\n",
            id="a-model-field",
        ),
        pytest.param(
            "from typing import NamedTuple\n"
            "from kodezart.types.domain.criterion_lifecycle import CrossOffState\n"
            "class _Verdict(NamedTuple):\n"
            "    criterion: str\n"
            "    state: CrossOffState\n",
            id="a-namedtuple-field",
        ),
    ],
)
def test_every_spelling_of_a_lone_state_is_reported(body, tmp_path):
    assert PLANTED in modules_of(surface(plant(tmp_path, body)))


#: Where a planted control sits in its package: a services-shaped module.
PLANTED = "services/planted.py"


def plant(root: Path, body: str) -> Path:
    """A package under *root* holding one planted module, for the walk."""
    module = root / PLANTED
    module.parent.mkdir(parents=True)
    module.write_text(body)
    return root


def modules_of(found: frozenset[str]) -> frozenset[str]:
    """The modules a walk's findings are in."""
    return frozenset(site.partition("::")[0] for site in found)


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
        pytest.param(
            "from kodezart.types.domain.criterion_evidence import CriterionEvidence\n"
            "from kodezart.types.domain.criterion_lifecycle import CrossOffState\n"
            "class Tick(CamelCaseModel):\n"
            "    state: CrossOffState\n"
            "    evidence: CriterionEvidence\n",
            id="a-model-carrying-both-halves",
        ),
    ],
)
def test_a_signature_carrying_the_pair_is_not_reported(body, tmp_path):
    assert surface(plant(tmp_path, body)) == frozenset()
