"""Each audit-lane symbol lives in the module its placement names.

A relocation used to red nothing: the only placement pins in the tree were
two wire-schema dispatch rows and the ordinary import graph, so a protocol
copied beside its implementation, or a type re-declared as a local stub,
passed every test.  This guard states the placement once and derives the
other side, so a move reds and a copy reds with it (KOD-540).

Every module path here is read off the symbol itself, never written down
beside it: ``declared_in`` resolves the object's own module file, so the
left column of ``PLACEMENTS`` is the source of truth and the right column
is only the Check's enumeration.  The scanned surface is the whole
package, derived by walking it, not a hand-listed set of audit modules —
a copy planted in a module no list names is exactly the copy a list
misses.  The walk is textual and executes nothing; the controls at the
bottom plant each shape the guard is for and prove it reds.

The same mechanism, asked one more question, states that the check-red
vocabulary and the body that classifies a red are each declared once in the
package: the member names, the member values and the answered shape are read
off the symbols, and only the owner's own class and function are exempt, by
module and name together.  An enum is recognised through ``from enum import
… as …``, through ``import enum`` and an attribute base, and in the
functional form; a vocabulary is carried by any superset of its names or its
values; a classifier is any function whose return annotation resolves,
through import aliases, dotted paths, unions and string annotations, to the
classification or the vocabulary; and a construction of the classification
outside the owner's function is reported (KOD-322).
"""

import ast
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Protocol

import pytest

from kodezart.chains.audit_sweep import AuditReadSweep, AuditReadSweepResult
from kodezart.chains.write_back_verifier import FreshWriteBackJudge, WriteBackVerifier
from kodezart.composition.audit import build_audit_pass
from kodezart.core.protocols import LaneEventHistory, WriteBackJudge, WriteBackStep
from kodezart.services.check_classification import classify_red_checks
from kodezart.types.domain.audit import AuditClaimReport, AuditVerdict
from kodezart.types.domain.criterion_lifecycle import CrossOffState
from kodezart.types.domain.delivery import CheckRedClass
from kodezart.types.domain.organize import DefectRole, SpecFinding
from tests.chains.test_write_back_adoption import step_members

SOURCE = Path(__file__).resolve().parents[1] / "src" / "kodezart"


class Declared(Protocol):
    """Anything that carries its own declaring module: a class or a function."""

    __module__: str
    __name__: str


def declared_in(obj: Declared) -> str:
    """Where *obj* is actually declared, inside the package.

    Read off the object's own module, so a symbol that moves answers
    differently the moment it moves — which is what a placement assertion
    needs and what a path spelled out beside the symbol cannot give.
    """
    return (
        Path(sys.modules[obj.__module__].__file__ or "")
        .resolve()
        .relative_to(SOURCE)
        .as_posix()
    )


#: Each symbol a placement clause names, against the module it names.
PLACEMENTS: tuple[tuple[Declared, str], ...] = (
    (WriteBackStep, "core/protocols.py"),
    (WriteBackJudge, "core/protocols.py"),
    (LaneEventHistory, "core/protocols.py"),
    (WriteBackVerifier, "chains/write_back_verifier.py"),
    (FreshWriteBackJudge, "chains/write_back_verifier.py"),
    (AuditReadSweep, "chains/audit_sweep.py"),
    (AuditReadSweepResult, "chains/audit_sweep.py"),
    (AuditVerdict, "types/domain/audit.py"),
    (AuditClaimReport, "types/domain/audit.py"),
    (build_audit_pass, "composition/audit.py"),
    (CrossOffState, "types/domain/criterion_lifecycle.py"),
    (classify_red_checks, "services/check_classification.py"),
)

#: The symbols another lane owns and this one may only import by name.
GUARDED_SYMBOLS: tuple[Declared, ...] = (
    SpecFinding,
    DefectRole,
    CheckRedClass,
    WriteBackStep,
    WriteBackJudge,
)

#: Each guarded symbol's owning module, derived once from the symbol.
OWNERS: Mapping[str, str] = {
    symbol.__name__: declared_in(symbol) for symbol in GUARDED_SYMBOLS
}

#: What a class must define to be one of the two write-back roles, read
#: off the protocol objects rather than restated here.
ROLE_SHAPES: Mapping[str, frozenset[str]] = {
    WriteBackStep.__name__: step_members(WriteBackStep),
    WriteBackJudge.__name__: step_members(WriteBackJudge),
}

ROLE_NAMES = frozenset(ROLE_SHAPES)

#: The red vocabulary's own member set, read off the enum: a second enum
#: assigning these members is a second vocabulary whatever it is called.
RED_MEMBERS = frozenset(member.name for member in CheckRedClass)
#: The shape a classifier answers with, read off the classifier's own return
#: annotation rather than named here: a second body answering it is a second
#: classifier.
RED_OBSERVATION = classify_red_checks.__annotations__["return"].__name__
#: The vocabulary's member values: a copy may keep the values under other
#: member names, which is the same vocabulary as persisted.
RED_VALUES = frozenset(member.value for member in CheckRedClass)
#: What a classifier answers with: the classification, or the bare vocabulary.
RED_SHAPES = frozenset({RED_OBSERVATION, CheckRedClass.__name__})


def _modules(root: Path) -> list[tuple[str, ast.Module]]:
    """Every module under *root*, keyed by its path inside it."""
    return [
        (path.relative_to(root).as_posix(), ast.parse(path.read_text()))
        for path in sorted(root.rglob("*.py"))
    ]


def _declared_members(node: ast.ClassDef) -> frozenset[str]:
    """The public members *node* states itself, as methods or fields."""
    members: set[str] = set()
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members.add(child.name)
        elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            members.add(child.target.id)
        elif isinstance(child, ast.Assign):
            members.update(
                target.id for target in child.targets if isinstance(target, ast.Name)
            )
    return frozenset(name for name in members if not name.startswith("_"))


def _protocol_names(tree: ast.Module) -> frozenset[str]:
    """Every name this module binds ``typing.Protocol`` to.

    Read out of the module's own imports rather than assumed to be the
    word ``Protocol``: ``from typing import Protocol as _P`` and a class
    based on ``_P`` is the same copied protocol under another spelling, and
    a base matched by spelling alone misses it.
    """
    return frozenset(
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "typing"
        for alias in node.names
        if alias.name == "Protocol"
    )


def _is_protocol(node: ast.ClassDef, names: frozenset[str]) -> bool:
    return any(isinstance(base, ast.Name) and base.id in names for base in node.bases)


def _rebindings(tree: ast.Module) -> frozenset[str]:
    """Each owned name this module binds at its top level by assignment.

    A ``class`` is not the only way to state a symbol a second time:
    ``SpecFinding = _LocalStub`` leaves the ``ImportFrom`` in place for the
    import assertion to find and hands the rest of the module a local stub
    under the owned name, which is the same evasion in one line.

    Only a binding at the module's own top level shadows the name the
    module reads, so the scan stops there: a local variable inside a
    function is that function's own and shadows nothing.
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        names.update(
            target.id
            for target in targets
            if isinstance(target, ast.Name) and target.id in OWNERS
        )
    return frozenset(names)


def _redeclarations(root: Path) -> dict[str, list[str]]:
    """Each module under *root* declaring a symbol another module owns."""
    found: dict[str, list[str]] = {}
    for module, tree in _modules(root):
        declared = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name in OWNERS
        } | _rebindings(tree)
        names = sorted(name for name in declared if OWNERS[name] != module)
        if names:
            found[module] = names
    return found


def _role_shaped_classes(root: Path) -> dict[str, list[str]]:
    """Each class outside the owning module shaped like a write-back role.

    A copy need not keep the name, so the member set is what is compared.
    """
    found: dict[str, list[str]] = {}
    for module, tree in _modules(root):
        protocols = _protocol_names(tree)
        copies = sorted(
            {
                f"{node.name} as {role}"
                for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef) and _is_protocol(node, protocols)
                for role, shape in ROLE_SHAPES.items()
                if _declared_members(node) == shape and OWNERS[role] != module
            }
        )
        if copies:
            found[module] = copies
    return found


def _second_import_paths(root: Path) -> dict[str, list[str]]:
    """Each ``X as X`` re-export of a write-back role outside its owner."""
    found: dict[str, list[str]] = {}
    for module, tree in _modules(root):
        aliases = sorted(
            {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                for alias in node.names
                if alias.name in ROLE_NAMES
                and alias.asname == alias.name
                and OWNERS[alias.name] != module
            }
        )
        if aliases:
            found[module] = aliases
    return found


def _imported_from(module: str, source_module: str) -> frozenset[str]:
    """The names *module* imports from *source_module* by name."""
    tree = ast.parse((SOURCE / module).read_text())
    return frozenset(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == source_module
        for alias in node.names
    )


def _enum_names(tree: ast.Module) -> tuple[frozenset[str], frozenset[str]]:
    """The names this module binds an enum class to, and the enum module to.

    Read out of the module's own imports, the way protocol bases are:
    ``from enum import StrEnum as _S`` binds ``_S`` to an enum class, and
    ``import enum as _e`` binds ``_e`` to the module, so ``_e.StrEnum`` is
    an enum class too.
    """
    classes = frozenset(
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "enum"
        for alias in node.names
    )
    modules = frozenset(
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "enum"
    )
    return classes, modules


def _names_an_enum(
    expression: ast.expr, enums: tuple[frozenset[str], frozenset[str]]
) -> bool:
    classes, modules = enums
    return (isinstance(expression, ast.Name) and expression.id in classes) or (
        isinstance(expression, ast.Attribute)
        and isinstance(expression.value, ast.Name)
        and expression.value.id in modules
    )


def _class_vocabulary(node: ast.ClassDef) -> tuple[frozenset[str], frozenset[object]]:
    """The member names a class states, and the constant values it assigns."""
    values = frozenset(
        child.value.value
        for child in node.body
        if isinstance(child, ast.Assign | ast.AnnAssign)
        and isinstance(child.value, ast.Constant)
    )
    return _declared_members(node), values


def _functional_vocabulary(
    call: ast.Call,
) -> tuple[frozenset[str], frozenset[object]]:
    """The member names and values of ``Enum("Name", members)``.

    The members may be one string of names, a sequence of names, a sequence
    of name and value pairs, or a mapping of names to values.
    """
    if len(call.args) < 2:
        return frozenset(), frozenset()
    members = call.args[1]
    if isinstance(members, ast.Constant) and isinstance(members.value, str):
        names = frozenset(members.value.replace(",", " ").split())
        return names, frozenset()
    if isinstance(members, ast.Dict):
        return (
            frozenset(
                key.value for key in members.keys if isinstance(key, ast.Constant)
            ),
            frozenset(
                value.value
                for value in members.values
                if isinstance(value, ast.Constant)
            ),
        )
    if isinstance(members, ast.List | ast.Tuple):
        names: set[str] = set()
        values: set[object] = set()
        for element in members.elts:
            if isinstance(element, ast.Constant):
                names.add(element.value)
            elif (
                isinstance(element, ast.Tuple | ast.List)
                and len(element.elts) == 2
                and all(isinstance(part, ast.Constant) for part in element.elts)
            ):
                name, value = element.elts
                assert isinstance(name, ast.Constant)
                assert isinstance(value, ast.Constant)
                names.add(name.value)
                values.add(value.value)
        return frozenset(names), frozenset(values)
    return frozenset(), frozenset()


def _carries_the_red_vocabulary(
    vocabulary: tuple[frozenset[str], frozenset[object]],
) -> bool:
    names, values = vocabulary
    return names >= RED_MEMBERS or values >= RED_VALUES


def _red_vocabularies(root: Path) -> dict[str, list[str]]:
    """Each enum but the owner's that carries the red vocabulary.

    An enum carries it when its member names, or its member values, include
    every member of the vocabulary: a superset is the vocabulary with more
    beside it.  Both the class form and the functional form are read, and
    only the owner's own class is exempt, by module and name together.
    """
    found: dict[str, list[str]] = {}
    owner = (declared_in(CheckRedClass), CheckRedClass.__name__)
    for module, tree in _modules(root):
        enums = _enum_names(tree)
        copies: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ClassDef)
                and (module, node.name) != owner
                and any(_names_an_enum(base, enums) for base in node.bases)
                and _carries_the_red_vocabulary(_class_vocabulary(node))
            ):
                copies.add(node.name)
            elif (
                isinstance(node, ast.Call)
                and _names_an_enum(node.func, enums)
                and _carries_the_red_vocabulary(_functional_vocabulary(node))
            ):
                first = node.args[0]
                copies.add(
                    first.value
                    if isinstance(first, ast.Constant) and isinstance(first.value, str)
                    else f"<call at line {node.lineno}>"
                )
        if copies:
            found[module] = sorted(copies)
    return found


def _imported_names(tree: ast.Module) -> Mapping[str, str]:
    """Each name this module binds by ``from … import``, to the name it imports."""
    return {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }


def _resolves_to(
    annotation: ast.expr | None, targets: frozenset[str], imported: Mapping[str, str]
) -> bool:
    """Whether *annotation* names one of *targets*, however it is spelled.

    Through an import alias, a dotted module path, ``X | None``, ``Optional``,
    ``Union`` and any other subscript, and a string annotation.
    """
    match annotation:
        case None:
            return False
        case ast.Name(id=name):
            return imported.get(name, name) in targets
        case ast.Attribute(attr=attr):
            return attr in targets
        case ast.BinOp(left=left, right=right):
            return _resolves_to(left, targets, imported) or _resolves_to(
                right, targets, imported
            )
        case ast.Subscript(value=value, slice=inner):
            return _resolves_to(value, targets, imported) or _resolves_to(
                inner, targets, imported
            )
        case ast.Tuple(elts=elements):
            return any(_resolves_to(element, targets, imported) for element in elements)
        case ast.Constant(value=str() as text):
            try:
                parsed = ast.parse(text, mode="eval").body
            except SyntaxError:
                return False
            return _resolves_to(parsed, targets, imported)
    return False


def _red_classifiers(root: Path) -> dict[str, list[str]]:
    """Each function but the owner's that answers with a red classification.

    A classifier is any function, synchronous or not, whose return
    annotation resolves to the classification or to the vocabulary itself.
    Only the owner's own function is exempt, by module and name together,
    so a second classifier beside it is reported too.
    """
    found: dict[str, list[str]] = {}
    owner = (declared_in(classify_red_checks), classify_red_checks.__name__)
    for module, tree in _modules(root):
        imported = _imported_names(tree)
        classifiers = sorted(
            {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and (module, node.name) != owner
                and _resolves_to(node.returns, RED_SHAPES, imported)
            }
        )
        if classifiers:
            found[module] = classifiers
    return found


def _red_constructions(root: Path) -> dict[str, list[str]]:
    """Each construction of the classification outside the owner's function."""
    found: dict[str, list[str]] = {}
    owner = (declared_in(classify_red_checks), classify_red_checks.__name__)
    for module, tree in _modules(root):
        imported = _imported_names(tree)
        inside = {
            id(node)
            for function in ast.walk(tree)
            if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
            and (module, function.name) == owner
            for node in ast.walk(function)
        }
        sites = sorted(
            f"line {node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and id(node) not in inside
            and _resolves_to(node.func, frozenset({RED_OBSERVATION}), imported)
        )
        if sites:
            found[module] = sites
    return found


def test_no_module_but_the_owner_declares_the_red_vocabulary() -> None:
    """One red vocabulary in the package, recognised by its member set.

    A lane is not a boundary a syntax tree can read, so the scanned side is
    the whole package: a copy planted in a module no list names is exactly
    the copy a list misses.
    """
    assert RED_MEMBERS
    assert _red_vocabularies(SOURCE) == {}


def test_no_module_but_the_owner_declares_a_red_classifier() -> None:
    """One body in the package answers with a red classification, and only
    that body constructs one."""
    assert _red_classifiers(SOURCE) == {}
    assert _red_constructions(SOURCE) == {}


def test_each_named_module_owns_the_symbol_the_placement_names() -> None:
    assert [declared_in(symbol) for symbol, _ in PLACEMENTS] == [
        path for _, path in PLACEMENTS
    ]


def test_the_grading_history_role_holds_its_one_read_and_nothing_beside_it() -> None:
    """A narrowed role's narrowness is a property, so it is asserted.

    ``TrackerPort`` already carries ``post_run_event``, so a role widened
    with it still satisfies every holder and every composition site, and
    the other guards in this module and in the docs read class names rather
    than members. What the role leaves out is the whole of its point: no
    append, so a holder cannot add the grading whose absence it reads for.
    Read off the protocol object, the way the write-back roles' shapes are.
    """
    assert step_members(LaneEventHistory) == frozenset({"lane_run_events"})


def test_the_pass_builder_is_wired_into_the_scheduled_passes() -> None:
    """Declared in its own module, and reached from the one that schedules.

    The builder's placement clause is two statements, not one: where it
    is declared and where the scheduled passes pick it up. Asserting only
    the first would pass over a builder nothing calls.

    What is collected is the name the import BINDS, not the name it reads
    from: ``import build_audit_pass as _make_audit`` binds another word, and
    a set of the read names would report the builder as imported while the
    call below reaches for something else.
    """
    tree = ast.parse((SOURCE / "composition" / "passes.py").read_text())
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "kodezart.composition.audit"
        for alias in node.names
    }
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert build_audit_pass.__name__ in imported
    assert build_audit_pass.__name__ in called


def test_no_module_but_the_owner_declares_a_cross_lane_symbol() -> None:
    """A stub keeping the name is the loophole inside "imported by name"."""
    assert _redeclarations(SOURCE) == {}


def test_no_module_but_the_owner_declares_a_class_shaped_like_a_write_back_role() -> (
    None
):
    """A copy under another name is still a second statement of the role."""
    assert _role_shaped_classes(SOURCE) == {}


def test_neither_write_back_role_is_re_exported_under_a_second_import_path() -> None:
    """One owning module per role, so a consumer has one name to read.

    Plain ``from kodezart.core.protocols import WriteBackStep`` for use
    stays legal; what this refuses is the ``X as X`` form, which offers a
    second module a consumer may name the role through.
    """
    assert _second_import_paths(SOURCE) == {}


def test_the_cross_lane_symbols_are_imported_from_their_owning_modules() -> None:
    """A local shim reds here even when it keeps the imported name."""
    assert {SpecFinding.__name__, DefectRole.__name__} <= _imported_from(
        "types/domain/audit.py", "kodezart.types.domain.organize"
    )
    assert CheckRedClass.__name__ in _imported_from(
        "types/domain/audit_forge.py", "kodezart.types.domain.delivery"
    )


# ---------------------------------------------------------------------------
# Controls — a guard with no control is a guard nobody has seen red
# ---------------------------------------------------------------------------


def test_a_relocated_symbol_reds_the_placement_it_used_to_satisfy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reading follows the symbol, so a written-down path cannot fake it."""
    elsewhere = ModuleType("relocated_for_the_control")
    elsewhere.__file__ = str(SOURCE / "chains" / "elsewhere.py")
    monkeypatch.setitem(sys.modules, elsewhere.__name__, elsewhere)
    monkeypatch.setattr(WriteBackStep, "__module__", elsewhere.__name__)
    assert declared_in(WriteBackStep) == "chains/elsewhere.py"
    with pytest.raises(AssertionError):
        test_each_named_module_owns_the_symbol_the_placement_names()


def test_a_planted_stub_reds_the_single_declaration_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "shim.py").write_text(
        "from pydantic import BaseModel\n\n\nclass SpecFinding(BaseModel):\n    pass\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "SOURCE", tmp_path.resolve())
    with pytest.raises(AssertionError):
        test_no_module_but_the_owner_declares_a_cross_lane_symbol()


def test_a_stub_rebound_to_the_owned_name_reds_the_single_declaration_assertion(
    tmp_path: Path,
) -> None:
    """A class under a private name plus one rebind is the same evasion.

    The module keeps its ``ImportFrom``, so the import assertion still
    passes, and every read of the owned name below the rebind reaches the
    local stub instead.  Planted on a tree of this control's own rather
    than on the authored module, so what is demonstrated is the detector
    answering and not the real tree happening to stay clean.
    """
    (tmp_path / "rebind.py").write_text(
        "from pydantic import BaseModel\n"
        "\n"
        "from kodezart.types.domain.organize import SpecFinding\n"
        "\n"
        "\n"
        "class _LocalSpecFinding(BaseModel): ...\n"
        "\n"
        "\n"
        "SpecFinding = _LocalSpecFinding\n"
    )
    assert _redeclarations(tmp_path) == {"rebind.py": [SpecFinding.__name__]}


def test_a_planted_role_copy_under_another_name_reds_the_shape_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "copy.py").write_text(
        "from typing import Protocol\n"
        "\n"
        "\n"
        "class WritingStep(Protocol):\n"
        "    @property\n"
        "    def surface(self) -> object: ...\n"
        "\n"
        "    async def write(self, *, finding: object) -> None: ...\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "SOURCE", tmp_path.resolve())
    with pytest.raises(AssertionError):
        test_no_module_but_the_owner_declares_a_class_shaped_like_a_write_back_role()


def test_a_planted_second_import_path_reds_the_re_export_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "reexport.py").write_text(
        "from kodezart.core.protocols import WriteBackStep as WriteBackStep\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "SOURCE", tmp_path.resolve())
    with pytest.raises(AssertionError):
        test_neither_write_back_role_is_re_exported_under_a_second_import_path()


def test_a_nested_declaration_is_not_a_hiding_place(tmp_path: Path) -> None:
    """The walk is over the whole tree of each module, not its top level."""
    (tmp_path / "nested.py").write_text(
        "def build():\n"
        "    class CheckRedClass:\n"
        "        pass\n"
        "\n"
        "    return CheckRedClass\n"
    )
    assert _redeclarations(tmp_path) == {"nested.py": [CheckRedClass.__name__]}


def test_a_plain_import_of_a_role_for_use_is_not_a_second_import_path(
    tmp_path: Path,
) -> None:
    (tmp_path / "consumer.py").write_text(
        "from kodezart.core.protocols import WriteBackJudge\n"
        "\n"
        "\n"
        "def judged(judge: WriteBackJudge) -> WriteBackJudge:\n"
        "    return judge\n"
    )
    assert _second_import_paths(tmp_path) == {}


def test_a_planted_second_red_vocabulary_reds_the_vocabulary_assertion(
    tmp_path: Path,
) -> None:
    """The member set is what is compared, under any name and any alias."""
    members = "\n".join(
        f"    {name} = {name.lower()!r}" for name in sorted(RED_MEMBERS)
    )
    (tmp_path / "copy.py").write_text(
        f"from enum import StrEnum as _S\n\n\nclass RedKind(_S):\n{members}\n"
    )
    assert _red_vocabularies(tmp_path) == {"copy.py": ["RedKind"]}


def test_a_planted_second_red_classifier_reds_the_classifier_assertion(
    tmp_path: Path,
) -> None:
    (tmp_path / "second.py").write_text(
        f"async def classify(initial) -> {RED_OBSERVATION}:\n    return initial\n"
    )
    assert _red_classifiers(tmp_path) == {"second.py": ["classify"]}


def test_importing_or_reading_the_red_vocabulary_is_not_declaring_it(
    tmp_path: Path,
) -> None:
    """A consumer names both symbols; a synchronous reader is not a classifier."""
    (tmp_path / "consumer.py").write_text(
        "from kodezart.types.domain.delivery import (\n"
        "    CheckRedClass,\n"
        f"    {RED_OBSERVATION},\n"
        ")\n"
        "\n"
        "\n"
        f"def is_defect(red: {RED_OBSERVATION}) -> bool:\n"
        "    return red.red_class is CheckRedClass.WORK_DEFECT\n"
    )
    assert _red_vocabularies(tmp_path) == {}
    assert _red_classifiers(tmp_path) == {}


def _members(*, extra: str = "", by_value: bool = False) -> str:
    rows = (
        [f"    {value} = {value!r}" for value in sorted(RED_VALUES)]
        if by_value
        else [f"    {name} = {name.lower()!r}" for name in sorted(RED_MEMBERS)]
    )
    return "\n".join(rows) + extra + "\n"


RED_VOCABULARY_SPELLINGS = {
    "attribute-base": f"import enum\n\n\nclass RedKind(enum.StrEnum):\n{_members()}",
    "module-alias": f"import enum as _e\n\n\nclass RedKind(_e.Enum):\n{_members()}",
    "functional": (
        "from enum import StrEnum\n\n"
        f"RedKind = StrEnum('RedKind', {sorted(RED_MEMBERS)!r})\n"
    ),
    "functional-string": (
        "import enum\n\n"
        f"RedKind = enum.Enum('RedKind', {' '.join(sorted(RED_MEMBERS))!r})\n"
    ),
    "superset": (
        "from enum import StrEnum\n\n\nclass RedKind(StrEnum):\n"
        + _members(
            extra="\n    OTHER = 'other'\n\n    def blocking(self) -> bool:\n"
            "        return True"
        )
    ),
    "by-value": (
        "from enum import StrEnum\n\n\nclass RedKind(StrEnum):\n"
        + _members(by_value=True)
    ),
}


@pytest.mark.parametrize("spelling", sorted(RED_VOCABULARY_SPELLINGS))
def test_a_red_vocabulary_is_reported_however_it_is_spelled(
    tmp_path: Path, spelling: str
) -> None:
    (tmp_path / "copy.py").write_text(RED_VOCABULARY_SPELLINGS[spelling])
    assert _red_vocabularies(tmp_path) == {"copy.py": ["RedKind"]}


def test_a_second_red_vocabulary_in_the_owners_module_is_reported(
    tmp_path: Path,
) -> None:
    owner = tmp_path / declared_in(CheckRedClass)
    owner.parent.mkdir(parents=True)
    owner.write_text(
        "from enum import StrEnum\n\n\n"
        f"class {CheckRedClass.__name__}(StrEnum):\n{_members()}\n\n"
        f"class RedKind(StrEnum):\n{_members()}"
    )
    assert _red_vocabularies(tmp_path) == {declared_in(CheckRedClass): ["RedKind"]}


RED_CLASSIFIER_SPELLINGS = {
    "sync-vocabulary": (
        f"from kodezart.types.domain.delivery import {CheckRedClass.__name__}\n\n\n"
        f"def classify(observed) -> {CheckRedClass.__name__}:\n"
        "    return observed\n"
    ),
    "async-vocabulary": (
        f"async def classify(observed) -> {CheckRedClass.__name__}:\n"
        "    return observed\n"
    ),
    "qualified": (
        "from kodezart.types.domain import delivery\n\n\n"
        f"async def classify(observed) -> delivery.{RED_OBSERVATION}:\n"
        "    return observed\n"
    ),
    "aliased": (
        f"from kodezart.types.domain.delivery import {RED_OBSERVATION} as _Obs\n\n\n"
        "async def classify(observed) -> _Obs:\n"
        "    return observed\n"
    ),
    "optional-union": (
        f"async def classify(observed) -> {RED_OBSERVATION} | None:\n"
        "    return observed\n"
    ),
    "optional": (
        "from typing import Optional\n\n\n"
        f"def classify(observed) -> Optional[{RED_OBSERVATION}]:\n"
        "    return observed\n"
    ),
    "string": (
        f"async def classify(observed) -> {RED_OBSERVATION!r}:\n    return observed\n"
    ),
}


@pytest.mark.parametrize("spelling", sorted(RED_CLASSIFIER_SPELLINGS))
def test_a_red_classifier_is_reported_however_it_is_spelled(
    tmp_path: Path, spelling: str
) -> None:
    (tmp_path / "second.py").write_text(RED_CLASSIFIER_SPELLINGS[spelling])
    assert _red_classifiers(tmp_path) == {"second.py": ["classify"]}


def test_a_second_classifier_and_construction_beside_the_owner_are_reported(
    tmp_path: Path,
) -> None:
    owner = tmp_path / declared_in(classify_red_checks)
    owner.parent.mkdir(parents=True)
    owner.write_text(
        f"async def {classify_red_checks.__name__}(initial) -> {RED_OBSERVATION}:\n"
        f"    return {RED_OBSERVATION}(red_class=None, observation=initial)\n\n\n"
        f"async def strictly(initial) -> {RED_OBSERVATION}:\n"
        f"    return {RED_OBSERVATION}(red_class=None, observation=initial)\n"
    )
    module = declared_in(classify_red_checks)
    assert _red_classifiers(tmp_path) == {module: ["strictly"]}
    assert _red_constructions(tmp_path) == {module: ["line 6"]}


def test_a_classification_constructed_outside_the_classifier_is_reported(
    tmp_path: Path,
) -> None:
    (tmp_path / "builder.py").write_text(
        f"from kodezart.types.domain.delivery import {RED_OBSERVATION}\n\n\n"
        "def restamp(observed, red_class):\n"
        f"    return {RED_OBSERVATION}(red_class=red_class, observation=observed)\n"
    )
    assert _red_classifiers(tmp_path) == {}
    assert _red_constructions(tmp_path) == {"builder.py": ["line 5"]}
