"""The organize finding, policy and halt types have exactly one definition site.

The finding shape and its role, the configured bounds and the halt report on
exhaustion are each defined once.

The scanned surface is derived: every shipped source file is walked, and
every binding of a name in it counts as a definition site of that name, at
any depth. The bindings counted are exactly these:

- a ``class`` or ``def`` statement;
- every name stored by an assignment, plain, annotated or augmented,
  including each name a tuple, list or starred target unpacks into;
- the target of a ``:=`` expression;
- the target of a ``for`` statement or a comprehension, the name a ``with``
  item binds with ``as``, and the name an ``except`` handler binds;
- the ``as`` name of an ``import`` or ``from ... import``;
- a ``type`` alias;
- an attribute store, such as ``OrganizeOwner._converge = ...``, which is a
  site of its attribute's name;
- ``setattr(<object>, "<name>", ...)`` with the name written as a literal,
  and a store into ``globals()``, ``vars(<object>)`` or ``<object>.__dict__``
  under a literal key.

A consumed symbol is keyed on its last dotted segment, so a same-named class
nested in a function or in another class, a module-level function beside a
method of that name, or an assignment such as ``X = enum.StrEnum(...)`` is a
second site of it, in whatever module it sits. The one site a symbol may have
is then checked for its module and for the scopes that qualify it. The symbol
list is hand-named, because the point of the guard is that these particular
symbols have one home each; the controls below are hand-written sources, so a
detector arm the shipped tree happens not to exercise still has a witness.

Outside every static guard's reach:

- a value handed across a function boundary, where the other function is not
  resolved at this site (returned from a helper, stored on an object and read
  elsewhere, or passed through a container built elsewhere);
- a name built at run time;
- a binding made only when a function runs (``setattr`` or ``globals()``
  inside a function body).

Of the last, a ``setattr`` or ``globals()`` store whose name is written as a
literal is still counted wherever it is written; what stays unseen is a
binding whose name reaches it only while the function runs. Each of these
shapes is held unseen by a committed test below.
"""

import ast
from collections import defaultdict
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "kodezart"

#: module path -> the qualified names that module is the one home of.
CONSUMED = {
    "types/domain/organize.py": ("SpecFinding", "DefectRole"),
    "types/domain/organize_owner.py": (
        "OrganizePolicy",
        "OrganizeBoundEvidence",
        "ConvergenceExhaustedHalt",
        "StageHaltReport",
    ),
}

#: The statements that open a scope, and so qualify what they enclose.
DEFINITION = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


#: The calls whose result is a namespace a subscript store binds a name in.
NAMESPACE_CALLS = ("globals", "vars")


def _literal_namespace_key(node: ast.Subscript) -> str | None:
    """The literal key of a store into ``globals()``, ``vars(x)`` or ``x.__dict__``."""
    space = node.value
    into_namespace = (
        isinstance(space, ast.Call)
        and isinstance(space.func, ast.Name)
        and space.func.id in NAMESPACE_CALLS
    ) or (isinstance(space, ast.Attribute) and space.attr == "__dict__")
    key = node.slice
    if into_namespace and isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value
    return None


def _bound_names(node: ast.AST) -> tuple[str, ...]:
    """The bare names *node* binds, in any of the forms the docstring lists."""
    if isinstance(node, DEFINITION):
        return (node.name,)
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        # Assignment targets at any depth of unpacking, ``:=``, ``for``,
        # comprehension and ``with ... as`` targets, and ``type`` aliases.
        return (node.id,)
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
        return (node.attr,)
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
        key = _literal_namespace_key(node)
        return () if key is None else (key,)
    if isinstance(node, ast.ExceptHandler) and node.name is not None:
        return (node.name,)
    if isinstance(node, ast.alias) and node.asname is not None:
        return (node.asname,)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "setattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return (node.args[1].value,)
    return ()


def _qualifier(node: ast.AST, name: str) -> str:
    """How a binding reads in its scope: the stored attribute keeps its object."""
    if isinstance(node, ast.Attribute):
        return ast.unparse(node)
    return name


def definitions(tree: ast.AST) -> list[tuple[str, str, int]]:
    """Every binding in *tree* at any depth: bare name, qualified name, line.

    A class or function body is a scope; an ``if``, ``try`` or loop body is
    not, so a conditional re-definition keeps the qualified name it shadows.
    The bare name is what the sites are counted by; the qualified name is
    what says which scope the one allowed site sits in.
    """
    found: list[tuple[str, str, int]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            for name in _bound_names(child):
                local = _qualifier(child, name)
                qualified = f"{prefix}.{local}" if prefix else local
                found.append((name, qualified, child.lineno))
            here = prefix
            if isinstance(child, DEFINITION):
                here = f"{prefix}.{child.name}" if prefix else child.name
            walk(child, here)

    walk(tree, "")
    return found


def scanned_sources() -> dict[str, ast.Module]:
    """Every shipped source file, derived from the tree rather than listed."""
    return {
        path.relative_to(SOURCE).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(SOURCE.rglob("*.py"))
    }


def definition_sites(trees: dict[str, ast.Module]) -> dict[str, list[str]]:
    """Every binding site of each bare name, as ``module:line``."""
    sites: dict[str, list[str]] = defaultdict(list)
    for module, tree in trees.items():
        for name, _, lineno in definitions(tree):
            sites[name].append(f"{module}:{lineno}")
    return sites


def qualified_sites(trees: dict[str, ast.Module], symbol: str) -> list[tuple[str, str]]:
    """Every binding of *symbol*'s bare name, as module and qualified name."""
    bare = symbol.rsplit(".", 1)[-1]
    return [
        (module, qualified)
        for module, tree in trees.items()
        for name, qualified, _ in definitions(tree)
        if name == bare
    ]


CONSUMED_SYMBOLS = [
    (module, name) for module, names in CONSUMED.items() for name in names
]


@pytest.mark.parametrize(
    ("module", "name"), CONSUMED_SYMBOLS, ids=[n for _, n in CONSUMED_SYMBOLS]
)
def test_each_consumed_symbol_is_defined_exactly_once(module: str, name: str) -> None:
    """One binding of the bare name in the whole tree, the named one."""
    assert qualified_sites(scanned_sources(), name) == [(module, name)]


#: One control per binding form the collector claims to see: the name, the
#: planted source, and the lines its sites sit on. Hand-written, because the
#: shipped tree has no duplicate to draw them from — which is the whole point
#: of the guard.
CONTROLS = (
    (
        "a second top-level definition",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\ndef organize_gap():\n    ...\n",
        [1, 5],
    ),
    (
        "a re-definition inside a conditional",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\nif True:\n    def organize_gap():\n"
        "        ...\n",
        [1, 6],
    ),
    (
        "a module-level assignment",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\norganize_gap = lambda **kwargs: ()\n",
        [1, 5],
    ),
    (
        "a functional enum assignment",
        "DefectRole",
        "import enum\n\n\nclass DefectRole(enum.StrEnum):\n    INSTANCE = 'instance'\n"
        "\n\nDefectRole = enum.StrEnum('DefectRole', {'INSTANCE': 'instance'})\n",
        [4, 8],
    ),
    (
        "a class nested in a function",
        "DefectRole",
        "class DefectRole:\n    ...\n\n\ndef _legacy_roles():\n"
        "    class DefectRole:\n        INSTANCE = 'instance'\n",
        [1, 6],
    ),
    (
        "a class nested in a class",
        "SpecFinding",
        "class SpecFinding:\n    ...\n\n\nclass _Shadow:\n"
        "    class SpecFinding(SpecFinding):\n        pass\n",
        [1, 6],
    ),
    (
        "an annotated assignment",
        "ConvergenceExhaustedHalt",
        "class ConvergenceExhaustedHalt:\n    ...\n\n\n"
        "ConvergenceExhaustedHalt: type = dict\n",
        [1, 5],
    ),
    (
        "an augmented assignment",
        "organize_gap",
        "organize_gap = ()\norganize_gap += ()\n",
        [1, 2],
    ),
    (
        "a type alias",
        "StageHaltReport",
        "class StageHaltReport:\n    ...\n\n\ntype StageHaltReport = dict\n",
        [1, 5],
    ),
    (
        "a tuple-unpacking assignment",
        "SpecFinding",
        "class SpecFinding:\n    ...\n\n\nSpecFinding, _x = object, object\n",
        [1, 5],
    ),
    (
        "a list-unpacking assignment",
        "DefectRole",
        "class DefectRole:\n    ...\n\n\n[_x, DefectRole] = [object, object]\n",
        [1, 5],
    ),
    (
        "a starred and nested unpacking",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\n_x, (_y, *organize_gap) = 1, (2, 3)\n",
        [1, 5],
    ),
    (
        "a walrus target",
        "DefectRole",
        "class DefectRole:\n    ...\n\n\nif (DefectRole := None) is None:\n    pass\n",
        [1, 5],
    ),
    (
        "a for target",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\nfor organize_gap in ():\n    pass\n",
        [1, 5],
    ),
    (
        "a comprehension target",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\n"
        "_x = [organize_gap for organize_gap in ()]\n",
        [1, 5],
    ),
    (
        "a with target",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\nwith open('x') as organize_gap:\n    pass\n",
        [1, 5],
    ),
    (
        "an except target",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\ntry:\n    pass\n"
        "except Exception as organize_gap:\n    pass\n",
        [1, 7],
    ),
    (
        "an import as",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\nimport os as organize_gap\n",
        [1, 5],
    ),
    (
        "a from-import as",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\n"
        "from kodezart.domain.scope_approval import scope_carries as organize_gap\n",
        [1, 5],
    ),
    (
        "an attribute store",
        "_converge",
        "class OrganizeOwner:\n    def _converge(self):\n        ...\n\n\n"
        "OrganizeOwner._converge = print\n",
        [2, 6],
    ),
    (
        "a setattr with a literal name",
        "_converge",
        "class OrganizeOwner:\n    def _converge(self):\n        ...\n\n\n"
        "setattr(OrganizeOwner, '_converge', print)\n",
        [2, 6],
    ),
    (
        "a setattr with a literal name inside a function body",
        "_converge",
        "class OrganizeOwner:\n    def _converge(self):\n        ...\n\n\n"
        "def _install():\n    setattr(OrganizeOwner, '_converge', print)\n",
        [2, 7],
    ),
    (
        "a globals() store under a literal key",
        "organize_gap",
        "def organize_gap():\n    ...\n\n\nglobals()['organize_gap'] = print\n",
        [1, 5],
    ),
    (
        "a vars() store under a literal key",
        "_converge",
        "class OrganizeOwner:\n    def _converge(self):\n        ...\n\n\n"
        "vars(OrganizeOwner)['_converge'] = print\n",
        [2, 6],
    ),
    (
        "a __dict__ store under a literal key",
        "_converge",
        "class OrganizeOwner:\n    def _converge(self):\n        ...\n\n\n"
        "OrganizeOwner.__dict__['_converge'] = print\n",
        [2, 6],
    ),
)


@pytest.mark.parametrize(
    ("shape", "name", "source", "lines"),
    CONTROLS,
    ids=[shape for shape, _, _, _ in CONTROLS],
)
def test_the_detector_sees_each_duplicate_shape(
    shape: str, name: str, source: str, lines: list[int]
) -> None:
    sites = definition_sites({"planted.py": ast.parse(source)})
    assert sites[name] == [f"planted.py:{line}" for line in lines], sites


#: One source per shape the docstring states is outside the guard's reach.
#: Each binds a consumed name at run time, and the guard does not see it.
LIMITS = (
    (
        "a value handed across a function boundary",
        "def _install(namespace, bindings):\n    namespace.update(bindings)\n\n\n"
        "_install(globals(), {'organize_gap': print})\n",
    ),
    (
        "a name built at run time",
        "setattr(OrganizeOwner, '_con' + 'verge', print)\n"
        "globals()['organize' + '_gap'] = print\n",
    ),
    (
        "a binding made only when a function runs",
        "def _late(name, value):\n    globals()[name] = value\n"
        "    setattr(OrganizeOwner, name, value)\n\n\n_late('organize_gap', print)\n"
        "_late('_converge', print)\n",
    ),
)


@pytest.mark.parametrize(
    ("shape", "source"), LIMITS, ids=[shape for shape, _ in LIMITS]
)
def test_the_stated_limit_is_unseen(shape: str, source: str) -> None:
    sites = definition_sites({"planted.py": ast.parse(source)})
    assert sites["organize_gap"] == [], sites
    assert sites["_converge"] == [], sites


def test_a_method_and_a_module_function_of_one_name_are_two_sites_of_it() -> None:
    """A module function beside the method is a second ``_converge``."""
    source = (
        "class OrganizeOwner:\n"
        "    def _converge(self):\n"
        "        ...\n"
        "\n"
        "\n"
        "def _converge():\n"
        "    ...\n"
    )
    trees = {"planted.py": ast.parse(source)}
    assert definition_sites(trees)["_converge"] == ["planted.py:2", "planted.py:6"]
    assert qualified_sites(trees, "OrganizeOwner._converge") == [
        ("planted.py", "OrganizeOwner._converge"),
        ("planted.py", "_converge"),
    ]


def test_the_guard_reddens_when_a_consumed_symbol_gains_a_second_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The scanned surface is the tree, so a new module is scanned unasked."""
    second = tmp_path / "kodezart"
    second.mkdir()
    (second / "elsewhere.py").write_text("def organize_gap():\n    ...\n")
    (second / "domain").mkdir()
    (second / "domain" / "organize.py").write_text("def organize_gap():\n    ...\n")
    monkeypatch.setattr("tests.test_organize_definition_sites.SOURCE", second)
    assert definition_sites(scanned_sources())["organize_gap"] == [
        "domain/organize.py:1",
        "elsewhere.py:1",
    ]
    with pytest.raises(AssertionError):
        test_each_consumed_symbol_is_defined_exactly_once(
            "domain/organize.py", "organize_gap"
        )
