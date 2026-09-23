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

A second statement of an owned symbol is any binding of the owned word
outside its owner, and what counts as a binding is read off the compiler's
own symbol table rather than off a list of statement kinds: a ``class``,
an assignment to any target shape, ``def``, ``type``, ``for``, ``with ...
as``, ``except ... as``, a match capture, a walrus, a parameter, a
``global`` declaration, in any scope of the module.  Beside the table, an
attribute stored or deleted under the owned word, the owned word spelled
as a whole string literal, and the owned word as a keyword argument are
read, because ``module.Word = ...``, ``globals()["Word"] = ...`` and
``globals().update(Word=...)`` bind it with no name the table records
(``tests/name_resolution.py``'s ``rebound_words``).  An import is judged by
what it binds: an alias is a rebind when the word it binds is not the
imported symbol's own name, whichever module it is imported out of, and
``X as X`` is a rebind unless it comes out of the owner (KOD-540).  Read
and reported although it binds nothing: a quoted annotation of an owned
word, an ``__all__`` entry naming it, or a keyword of its name in any call,
outside its owner.

The same clause is also read by object: every module of the package is
imported, and its namespace (``vars(module)``) must bind each owned word to
the owned symbol itself or not at all.  That reads whatever runs at import,
however the word is spelled or built.  A copied protocol is found by what
its bases name in the module's namespace after import (``object_named``),
so ``typing.Protocol``, ``Protocol[T]``, an alias of it and
``typing_extensions.Protocol`` are one base.

Outside this guard's reach, as outside every static guard's: a value handed
across a function boundary, where the other function is not resolved at
this site (returned from a helper, stored on an object and read elsewhere,
or passed through a container built elsewhere); a name built at run time;
a binding made only when a function runs (``setattr`` or ``globals()``
inside a function body).  For this guard those come to one shape, a word
the source never spells whole, bound where import does not run it; and a
word spelled whole is read wherever it is written, a function body
included.  ``LIMIT_SHAPES`` holds each as unseen, and
``PROTOCOL_LIMIT_SHAPES`` holds a copy whose base is bound only inside a
function body; ``eval`` or ``exec`` is not read either.  Only ``*.py`` is
walked: a ``.pyi`` stub restating an owned word is left to code review.
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
import typing
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Protocol

import pytest
import typing_extensions

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
from tests.name_resolution import namespace_after_import, object_named, rebound_words

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

#: The objects a class names as a base to be a protocol, whichever module it
#: took them from and however it spells them.
PROTOCOL_BASES: tuple[object, ...] = (typing.Protocol, typing_extensions.Protocol)
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


def _is_protocol(node: ast.ClassDef, namespace: Mapping[str, object]) -> bool:
    """Whether a base of *node* names ``Protocol``, read by object.

    Each base is resolved in the module's own namespace after import, so
    ``Protocol``, ``_P`` bound to it, ``typing.Protocol``, ``Protocol[T]``
    and ``typing_extensions.Protocol`` are the same base, and a class named
    anything at all is a copied protocol when that is what it is based on.
    """
    return any(
        object_named(base, namespace) is protocol
        for base in node.bases
        for protocol in PROTOCOL_BASES
    )


def _source_module(node: ast.ImportFrom | ast.Import, alias: ast.alias) -> str | None:
    """Which module *alias* is imported out of, as a path inside the package.

    ``None`` when the import names nothing under ``kodezart``, which is a
    source no owning module can be and therefore never the owner.
    """
    dotted = node.module if isinstance(node, ast.ImportFrom) else alias.name
    if dotted is None or not dotted.startswith("kodezart."):
        return None
    return "/".join(dotted.split(".")[1:]) + ".py"


def _import_rebindings(node: ast.ImportFrom | ast.Import) -> set[str]:
    """Each owned name this import node binds to something else.

    ``import ... as`` binds a word exactly as ``=`` does, so the two are one
    evasion under two spellings: ``from elsewhere import Other as
    SpecFinding`` hands the rest of the module another lane's class under
    the owned name with no assignment and no ``class`` statement in the
    file at all.

    What is compared is the SYMBOL, not the module it came out of: an alias
    binding an owned word to anything whose own name is a different word is
    a rebind wherever it is imported from, and the owning module of a
    sibling class is an owning module too.  The source is read for one case
    only — ``X as X``, where the bound word is the imported symbol's own
    name — which is a re-export of the owned symbol when it comes out of
    the owner and a second statement of it when it does not (KOD-540).
    """
    names: set[str] = set()
    for alias in node.names:
        bound = alias.asname
        if bound is None or bound not in OWNERS:
            continue
        if alias.name != bound or _source_module(node, alias) != OWNERS[bound]:
            names.add(bound)
    return names


def _declared(source: str) -> frozenset[str]:
    """Each owned word *source* binds, in any scope, by any statement.

    Every binding but an import is a statement of the word, whatever its
    form, which ``rebound_words`` reads off the symbol table.  An import is
    a statement of it only when ``_import_rebindings`` says it binds the
    word to something else, and every import node of the module is read,
    in a function body as much as at the top (KOD-540).
    """
    imports = (
        name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom | ast.Import)
        for name in _import_rebindings(node)
    )
    return rebound_words(source, names=OWNERS) | frozenset(imports)


def _declarations(root: Path) -> dict[str, list[str]]:
    """Each module under *root* with every owned word it binds, owner or not."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        names = sorted(_declared(path.read_text()))
        if names:
            found[path.relative_to(root).as_posix()] = names
    return found


def _redeclarations(root: Path) -> dict[str, list[str]]:
    """Each module under *root* declaring a symbol another module owns."""
    found: dict[str, list[str]] = {}
    for module, declared in _declarations(root).items():
        names = [name for name in declared if OWNERS[name] != module]
        if names:
            found[module] = names
    return found


def _namespaces(root: Path) -> dict[str, Mapping[str, object]]:
    """Each module under *root*, keyed by its path inside it, once imported."""
    return {
        path.relative_to(root).as_posix(): namespace_after_import(path, root)
        for path in sorted(root.rglob("*.py"))
    }


def _rebound_after_import(root: Path) -> dict[str, list[str]]:
    """Each module under *root* whose namespace, once imported, binds an
    owned word to anything but the owned symbol itself.

    Read by object and not by spelling: whatever the module runs at import
    -- a keyword update of ``globals()``, a write through
    ``sys.modules[__name__].__dict__``, a name built out of pieces -- has
    left its binding in ``vars(module)``, which is compared by identity.
    """
    owned = {symbol.__name__: symbol for symbol in GUARDED_SYMBOLS}
    found: dict[str, list[str]] = {}
    for module, namespace in _namespaces(root).items():
        names = sorted(
            word
            for word, symbol in owned.items()
            if namespace.get(word, symbol) is not symbol
        )
        if names:
            found[module] = names
    return found


def _role_shaped_classes(root: Path) -> dict[str, list[str]]:
    """Each class outside the owning module shaped like a write-back role.

    A copy need not keep the name, so the member set is what is compared.
    """
    found: dict[str, list[str]] = {}
    for module, tree in _modules(root):
        namespace = namespace_after_import(root / module, root)
        copies = sorted(
            {
                f"{node.name} as {role}"
                for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef) and _is_protocol(node, namespace)
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


def test_the_binding_reader_finds_each_owned_word_in_its_own_owner() -> None:
    """The clean result above is a reader that sees, not one gone blind.

    Each owning module does state its owned word, so the scan has to find
    every one of them there; the owner filter is what keeps them out of the
    clean result, not a reader that finds nothing anywhere.  Read through
    the same walk the clean result is, so a walk that stopped short of an
    owner -- one reduced to the package's top level, say -- reds here too.
    """
    declared = _declarations(SOURCE)
    unseen = sorted(
        name for name, owner in OWNERS.items() if name not in declared.get(owner, [])
    )
    assert OWNERS and unseen == [], unseen


def test_no_module_binds_an_owned_word_to_another_object_once_imported() -> None:
    """The same clause read by object: whatever runs at import is in it."""
    assert _rebound_after_import(SOURCE) == {}


def test_each_owner_binds_its_owned_word_to_the_symbol_once_imported() -> None:
    """The clean result above is a namespace read, not a walk gone empty.

    Each owning module's own namespace is taken by the same walk and binds
    the owned word to the very symbol this module imported, so a walk or a
    lookup that found nothing could not pass as a clean package.
    """
    namespaces = _namespaces(SOURCE)
    unseen = sorted(
        symbol.__name__
        for symbol in GUARDED_SYMBOLS
        if namespaces.get(OWNERS[symbol.__name__], {}).get(symbol.__name__)
        is not symbol
    )
    assert GUARDED_SYMBOLS and unseen == [], unseen


def test_a_package_is_read_as_the_module_every_importer_sees() -> None:
    """A package's ``__init__`` is imported as the package itself, not run a
    second time under a name whose namespace no importer reads."""
    assert _namespaces(SOURCE)["__init__.py"] is vars(sys.modules[SOURCE.name])


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


def test_an_import_alias_onto_the_owned_name_reds_the_single_declaration_assertion(
    tmp_path: Path,
) -> None:
    """The third spelling of the rebind, and the one that needs no statement.

    No assignment and no ``class`` anywhere in the module: the import itself
    binds the owned word to another lane's class, and every read below it
    reaches that class.  A scan over the two assignment forms alone reported
    a module of this shape clean.  Planted on this control's own tree rather
    than on an authored module, so what is demonstrated is the detector
    answering and not the real tree happening to stay clean.
    """
    (tmp_path / "alias.py").write_text(
        "from kodezart.types.domain.write_back import (\n"
        "    WriteBackFinding as SpecFinding,\n"
        ")\n"
    )
    assert _redeclarations(tmp_path) == {"alias.py": [SpecFinding.__name__]}


#: One row per alias that binds an owned word to something whose own name is
#: another word, with the source module the owner itself — the case the arm
#: used to exempt by source alone. A row per NODE kind, because the two are
#: one hole under two statements: ``ImportFrom`` binding a sibling class of
#: the owner, and ``Import`` binding the owning module itself. Both leave
#: every read of the owned word below them reaching another object.
OWNING_MODULE = "kodezart.types.domain.organize"
SIBLING_ALIASES = (
    f"from {OWNING_MODULE} import {DefectRole.__name__} as {SpecFinding.__name__}\n",
    f"import {OWNING_MODULE} as {SpecFinding.__name__}\n",
)


@pytest.mark.parametrize("source", SIBLING_ALIASES, ids=SIBLING_ALIASES)
def test_an_alias_out_of_the_owning_module_onto_a_sibling_name_is_a_rebind(
    source: str, tmp_path: Path
) -> None:
    """The source module is the owner and the binding is still a rebind.

    The arm used to ask which module the name came out of and never which
    symbol it bound, so an alias whose source happened to be the owning
    module was exempt however it was spelled — including when it bound the
    owner's OTHER class, or the owning module itself, to the owned word.
    Planted on this control's own tree, so what is demonstrated is the
    detector answering.
    """
    (tmp_path / "sibling.py").write_text(source)

    assert _redeclarations(tmp_path) == {"sibling.py": [SpecFinding.__name__]}


#: One row per way Python binds a word, each planted with the owned word as
#: the bound word, and every one a statement of it: the reader takes them off
#: the symbol table, so no row is a case the reader lists.  The first rows are
#: the alias under each block a module can carry statements in; a function
#: body is one of them, because ``global`` makes a function's binding the
#: module's own, and a class inside a function was already reported.  The
#: last rows are the writes the table does not record: through a string,
#: through a keyword argument, and through an attribute stored or deleted.
WRITE_BACK = "from kodezart.types.domain.write_back import WriteBackFinding\n\n"
ALIAS = "from kodezart.types.domain.write_back import WriteBackFinding as SpecFinding"
BINDING_FORMS = {
    "if": f"from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    {ALIAS}\n",
    "try": f"try:\n    {ALIAS}\nexcept ImportError:\n    pass\n",
    "try-star": f"try:\n    pass\nexcept* ImportError:\n    {ALIAS}\n",
    "with": "import contextlib\n\n"
    f"with contextlib.suppress(ImportError):\n    {ALIAS}\n",
    "for": f"for _ in range(1):\n    {ALIAS}\n",
    "while": f"while True:\n    {ALIAS}\n    break\n",
    "match": f"match 1:\n    case _:\n        {ALIAS}\n",
    "function-body": f"def _load() -> None:\n    {ALIAS}\n",
    "tuple-target": WRITE_BACK + "SpecFinding, _SPARE = WriteBackFinding, None\n",
    "list-target": WRITE_BACK + "[SpecFinding] = [WriteBackFinding]\n",
    "starred-target": WRITE_BACK + "*SpecFinding, = [WriteBackFinding]\n",
    "annotated": WRITE_BACK + "SpecFinding: type = WriteBackFinding\n",
    "for-target": WRITE_BACK + "for SpecFinding in (WriteBackFinding,):\n    pass\n",
    "with-as": WRITE_BACK + "import contextlib\n\n"
    "with contextlib.nullcontext(WriteBackFinding) as SpecFinding:\n    pass\n",
    "except-as": "try:\n    pass\nexcept ImportError as SpecFinding:\n    pass\n",
    "match-capture": WRITE_BACK
    + "match WriteBackFinding:\n    case SpecFinding:\n        pass\n",
    "walrus": WRITE_BACK + "_BOUND = (SpecFinding := WriteBackFinding)\n",
    "walrus-in-comprehension": WRITE_BACK
    + "_BOUND = [(SpecFinding := each) for each in (WriteBackFinding,)]\n",
    "comprehension-target": WRITE_BACK
    + "_BOUND = [SpecFinding for SpecFinding in (WriteBackFinding,)]\n",
    "type-alias": WRITE_BACK + "type SpecFinding = WriteBackFinding\n",
    "def": "def SpecFinding() -> None: ...\n",
    "async-def": "async def SpecFinding() -> None: ...\n",
    "conditional-def": "if True:\n\n    def SpecFinding() -> None: ...\n",
    "class-body": WRITE_BACK + "class _Holder:\n    SpecFinding = WriteBackFinding\n",
    "global-in-function": WRITE_BACK + "def _rebind() -> None:\n"
    "    global SpecFinding\n    SpecFinding = WriteBackFinding\n\n\n_rebind()\n",
    "parameter": "def _take(SpecFinding: object) -> object:\n    return SpecFinding\n",
    "type-parameter": "def _take[SpecFinding](value: SpecFinding) -> SpecFinding:\n"
    "    return value\n",
    "del": "from kodezart.types.domain.organize import SpecFinding\n\n"
    "del SpecFinding\n",
    "globals-literal": WRITE_BACK + "globals()['SpecFinding'] = WriteBackFinding\n",
    "setattr-literal": WRITE_BACK + "import sys\n\n"
    "setattr(sys.modules[__name__], 'SpecFinding', WriteBackFinding)\n",
    "globals-update-keyword": WRITE_BACK
    + "globals().update(SpecFinding=WriteBackFinding)\n",
    "vars-update-keyword": WRITE_BACK
    + "import kodezart.types.domain.organize as _organize\n\n"
    "vars(_organize).update(SpecFinding=WriteBackFinding)\n",
    "module-attribute": WRITE_BACK + "import kodezart.types.domain.audit as _audit\n\n"
    "_audit.SpecFinding = WriteBackFinding\n",
    "module-attribute-del": "import kodezart.types.domain.audit as _audit\n\n"
    "del _audit.SpecFinding\n",
}


@pytest.mark.parametrize("source", BINDING_FORMS.values(), ids=list(BINDING_FORMS))
def test_every_binding_of_an_owned_word_is_a_second_statement_of_it(
    source: str, tmp_path: Path
) -> None:
    """What binds the word is what is read, not the statement that does it.

    The scan used to read two assignment forms and three block kinds, and
    every other row here left it clean: the owned word bound under another
    block, or by a target shape, a ``def``, a ``type`` statement, a
    ``global`` or a string, with every read of it below reaching another
    object.  Planted on this
    control's own tree, so what is demonstrated is the detector answering.
    """
    (tmp_path / "planted.py").write_text(source)

    assert _redeclarations(tmp_path) == {"planted.py": [SpecFinding.__name__]}


#: What reads the owned word binds nothing, so the arms beside the symbol
#: table cannot simply report every mention: a read, a plain import out of the
#: owner, and an attribute read off the owning module.
OWNED_WORD_READS = {
    "read": "from kodezart.types.domain.organize import SpecFinding\n\n"
    "_READ = SpecFinding\n",
    "attribute-read": "import kodezart.types.domain.organize as _organize\n\n"
    "_READ = _organize.SpecFinding\n",
}


@pytest.mark.parametrize(
    "source", OWNED_WORD_READS.values(), ids=list(OWNED_WORD_READS)
)
def test_a_read_of_an_owned_word_is_not_a_statement_of_it(
    source: str, tmp_path: Path
) -> None:
    (tmp_path / "reader.py").write_text(source)

    assert _redeclarations(tmp_path) == {}


#: What the arms beside the symbol table read although it binds nothing: the
#: owned word written whole as a string or as a keyword is reported wherever
#: it stands, which is the cost of reading every write that spells it.
WHOLE_WORD_MENTIONS = {
    "quoted-annotation": "def _take(value: 'SpecFinding') -> None: ...\n",
    "dunder-all": "__all__ = ['SpecFinding']\n",
    "call-keyword": "_HELD = dict(SpecFinding=None)\n",
}


@pytest.mark.parametrize(
    "source", WHOLE_WORD_MENTIONS.values(), ids=list(WHOLE_WORD_MENTIONS)
)
def test_a_whole_word_mention_is_reported_though_it_binds_nothing(
    source: str, tmp_path: Path
) -> None:
    (tmp_path / "mention.py").write_text(source)

    assert _redeclarations(tmp_path) == {"mention.py": [SpecFinding.__name__]}


#: One row per way a module can leave an owned word bound to another object
#: once it has run, each planted in a module the pin imports: the pin reads
#: the namespace, so the row's spelling is not what it keys on -- a name
#: built out of pieces, which the source scan cannot read, is one of them.
IMPORT_BINDINGS = {
    "class-statement": "class SpecFinding: ...\n",
    "globals-update-keyword": WRITE_BACK
    + "globals().update(SpecFinding=WriteBackFinding)\n",
    "name-built-at-import": WRITE_BACK
    + "globals()['Spec' + 'Finding'] = WriteBackFinding\n",
}


@pytest.mark.parametrize("source", IMPORT_BINDINGS.values(), ids=list(IMPORT_BINDINGS))
def test_every_binding_left_at_import_is_read_by_object(
    source: str, tmp_path: Path
) -> None:
    (tmp_path / "planted.py").write_text(source)

    assert _rebound_after_import(tmp_path) == {"planted.py": [SpecFinding.__name__]}


def test_each_reading_sees_what_the_other_cannot(tmp_path: Path) -> None:
    """Why both are kept: the source scan reads a function body that import
    never runs, and the namespace reads a name the source never spells whole.
    """
    (tmp_path / "built.py").write_text(IMPORT_BINDINGS["name-built-at-import"])
    (tmp_path / "deferred.py").write_text(
        WRITE_BACK + "def _bind() -> None:\n"
        "    globals().update(SpecFinding=WriteBackFinding)\n"
    )

    assert _redeclarations(tmp_path) == {"deferred.py": [SpecFinding.__name__]}
    assert _rebound_after_import(tmp_path) == {"built.py": [SpecFinding.__name__]}


#: The stated limit, held as unseen by both readings: a word the source never
#: spells whole, bound only when a function runs, or handed to the function
#: that binds it across a function boundary.  Neither function runs at
#: import, and no whole spelling of the word is anywhere in the source.
LIMIT_SHAPES = {
    "bound-when-a-function-runs": WRITE_BACK + "def _bind() -> None:\n"
    "    globals()['Spec' + 'Finding'] = WriteBackFinding\n",
    "handed-across-a-function-boundary": WRITE_BACK
    + "def _bind(namespace: dict[str, object], word: str) -> None:\n"
    "    namespace[word] = WriteBackFinding\n\n\n"
    "def _load() -> None:\n"
    "    _bind(globals(), 'Spec' + 'Finding')\n",
}


@pytest.mark.parametrize("source", LIMIT_SHAPES.values(), ids=list(LIMIT_SHAPES))
def test_a_word_never_spelled_whole_and_bound_outside_import_is_not_read(
    source: str, tmp_path: Path
) -> None:
    (tmp_path / "limit.py").write_text(source)

    assert _redeclarations(tmp_path) == {}
    assert _rebound_after_import(tmp_path) == {}


def test_an_import_of_an_owned_symbol_from_its_owner_is_not_a_rebind(
    tmp_path: Path,
) -> None:
    """The alias arm reads the source module, so a re-export is not a rebind.

    Without this the arm could report every aliased import and still pass
    the control above, which would red the shipped tree the moment a module
    imported an owned symbol under a shorter word from the module that owns
    it.
    """
    (tmp_path / "consumer.py").write_text(
        "from kodezart.types.domain.organize import SpecFinding as SpecFinding\n"
    )
    assert _redeclarations(tmp_path) == {}


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


#: One row per way a class can name ``Protocol`` as its base, each a copy of
#: the judge role under another name: the base is read by the object it
#: names in the module's namespace, so no row is a spelling the reader lists.
JUDGE_BODY = "    async def judge(self, *, artifact: object, ref: str) -> object: ...\n"
PROTOCOL_COPIES = {
    "from-typing": "from typing import Protocol\n\n\nclass _Judge(Protocol):\n"
    + JUDGE_BODY,
    "aliased": "from typing import Protocol as _P\n\n\nclass _Judge(_P):\n"
    + JUDGE_BODY,
    "typing-attribute": "import typing\n\n\nclass _Judge(typing.Protocol):\n"
    + JUDGE_BODY,
    "subscripted": "from typing import Protocol, TypeVar\n\n"
    "T = TypeVar('T')\n\n\nclass _Judge(Protocol[T]):\n" + JUDGE_BODY,
    "typing-extensions": "import typing_extensions\n\n\n"
    "class _Judge(typing_extensions.Protocol):\n" + JUDGE_BODY,
    "inside-a-function": "from typing import Protocol\n\n\n"
    "def _build() -> object:\n"
    "    class _Judge(Protocol):\n"
    "        async def judge(self, *, artifact: object, ref: str) -> object: ...\n"
    "\n"
    "    return _Judge\n",
}


@pytest.mark.parametrize("source", PROTOCOL_COPIES.values(), ids=list(PROTOCOL_COPIES))
def test_a_role_copy_is_found_by_what_its_base_names(
    source: str, tmp_path: Path
) -> None:
    (tmp_path / "copy.py").write_text(source)

    assert _role_shaped_classes(tmp_path) == {
        "copy.py": [f"_Judge as {WriteBackJudge.__name__}"]
    }


def test_an_implementation_shaped_like_a_role_is_not_a_copy(tmp_path: Path) -> None:
    """A class that implements the role states its members too; only a class
    based on ``Protocol`` restates the role, so the shape alone reports
    nothing."""
    (tmp_path / "judge.py").write_text("class _Judge:\n" + JUDGE_BODY)

    assert _role_shaped_classes(tmp_path) == {}


#: The stated limit for a copy's base: a name bound only when a function runs
#: is not in the namespace import leaves, so a copy based on it is not read.
PROTOCOL_LIMIT_SHAPES = {
    "base-imported-in-the-function-body": "def _build() -> object:\n"
    "    from typing import Protocol as _P\n\n"
    "    class _Judge(_P):\n"
    "        async def judge(self, *, artifact: object, ref: str) -> object: ...\n"
    "\n"
    "    return _Judge\n",
    "module-imported-in-the-function-body": "def _build() -> object:\n"
    "    import typing as _t\n\n"
    "    class _Judge(_t.Protocol):\n"
    "        async def judge(self, *, artifact: object, ref: str) -> object: ...\n"
    "\n"
    "    return _Judge\n",
}


@pytest.mark.parametrize(
    "source", PROTOCOL_LIMIT_SHAPES.values(), ids=list(PROTOCOL_LIMIT_SHAPES)
)
def test_a_copy_based_on_a_name_bound_when_a_function_runs_is_not_read(
    source: str, tmp_path: Path
) -> None:
    (tmp_path / "copy.py").write_text(source)

    assert _role_shaped_classes(tmp_path) == {}


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
