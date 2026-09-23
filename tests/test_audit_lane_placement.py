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
attribute stored under the owned word and the owned word spelled as a whole
string literal are read, because ``module.Word = ...`` and
``globals()["Word"] = ...`` bind it with no name the table records
(``tests/name_resolution.py``'s ``rebound_words``).  An import is judged by
what it binds: an alias is a rebind when the word it binds is not the
imported symbol's own name, whichever module it is imported out of, and
``X as X`` is a rebind unless it comes out of the owner (KOD-540).

Not read: a word built at run time (``getattr``, ``setattr``,
``globals()`` or ``importlib`` with a computed name), and ``eval`` or
``exec``.  Read and reported although it binds nothing: a quoted
annotation of an owned word, or an ``__all__`` entry naming it, outside
its owner.
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
from kodezart.types.domain.audit import AuditClaimReport, AuditVerdict
from kodezart.types.domain.criterion_lifecycle import CrossOffState
from kodezart.types.domain.delivery import CheckRedClass
from kodezart.types.domain.organize import DefectRole, SpecFinding
from tests.chains.test_write_back_adoption import step_members
from tests.name_resolution import rebound_words

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


def _redeclarations(root: Path) -> dict[str, list[str]]:
    """Each module under *root* declaring a symbol another module owns."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        names = sorted(
            name for name in _declared(path.read_text()) if OWNERS[name] != module
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

    Each owning module does state its owned word, so the reader has to find
    every one of them there; the owner filter is what keeps them out of the
    clean result, not a reader that finds nothing anywhere.
    """
    unseen = sorted(
        name
        for name, owner in OWNERS.items()
        if name not in _declared((SOURCE / owner).read_text())
    )
    assert OWNERS and unseen == [], unseen


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
#: last rows are the writes the table does not record.
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
    "module-attribute": WRITE_BACK + "import kodezart.types.domain.audit as _audit\n\n"
    "_audit.SpecFinding = WriteBackFinding\n",
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
