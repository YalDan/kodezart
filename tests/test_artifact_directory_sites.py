"""The artifact directory is written and never read (KOD-96-AC-29).

The tracker is the source a run re-enters and reads satisfaction from.  A
directory committed onto a branch is a projection of that source: written for
a human to read in the repository, read back by nothing, so it can never
answer a question the tracker answers differently.  A single read would make
it a second source, and the two would part on the first write one of them
missed.

Nothing is listed by hand that the tree can be asked for: the directory is
the constant's own value, the modules permitted to name it are the constant's
module and the module the persister class is defined in, and the scanned tree
is every ``.py`` module of the package the constant is packaged in.  What IS
listed is the calls the persister may make on a path under the directory,
each with the reason it is not a read, and that table is checked against the
walk in both directions, so an exemption for a call that no longer exists is
as red as an unexempted call.

Within the reach stated below, the walk sees the directory in these
spellings: a directory name bare, imported under another name, read as a
module attribute or spelled in a string (``getattr(constants,
"ARTIFACT_DIR")``, ``vars(x)["ARTIFACT_DIR"]``, ``x.__dict__[...]``, an
``attrgetter`` path, a format field, a ``module:attr`` string), and any
``str`` or ``bytes`` constant that holds the directory as a path segment --
split on ``/``, ``:`` and whitespace, so a relative, absolute,
``./``-prefixed, f-string, ``ref:path`` or shell-command spelling is one.
An expression is a directory path when any operand of it is: a binary
operation of any operator (``/``, ``+``, ``%``), either arm of a conditional
expression, any value of a boolean operation, an item of a display holding
one, and a call handed one or formatting one, positionally or by keyword.
The directory names are the constant's own name and every name a permitted
module binds to a directory path: by syntax, over every statement that runs
at import (the top level, the bodies of an ``if``, ``try``, ``with``, loop or
class there, and a top-level walrus); and, for the shipped tree, by object,
from ``vars()`` of the two imported modules and their classes' attributes
read through ``inspect.getattr_static``, so a path constant derived from the
directory is named wherever it is imported however its source bound it. A
name bound to such a path, by assignment, a walrus, a loop, a ``with``
target or an attribute such as ``self._artifact_dir``, carries it on; a
name bound to a method of one (``read = artifact_dir.read_text``) is a call
of that method on the directory; so does a list, tuple, set, starred or
``**`` display holding one, and a call of a function or method of the same
module whose return or yield is one, or of a name bound to a lambda that
returns one. In the permitted modules no return, yield or lambda hands a
directory path out at all, so no other module can be given one by calling
them.

The walk is textual and executes nothing, which is what lets it speak for
every ``.py`` module rather than for the paths a fixture reaches.  Its
reach is the one stated for every static guard; outside it:

- a value handed across a function boundary, where the other function is
  not resolved at this site (returned from a helper, stored on an object
  and read elsewhere, or passed through a container built elsewhere);
- a name built at run time;
- a binding made only when a function runs (``setattr`` or ``globals()``
  inside a function body).

Each of the three is held as unseen by a committed test below.
"""

import ast
import importlib.util
import inspect
import os
import re
import sys
import types
from collections.abc import Iterable
from pathlib import Path, PurePath

import pytest

from kodezart.adapters.git import artifact_persister
from kodezart.adapters.git.artifact_persister import GitArtifactPersister
from kodezart.core import constants
from kodezart.core.constants import ARTIFACT_DIR

#: The package the constant is packaged in, and so the tree this speaks for.
SOURCE_ROOT = Path(sys.modules[constants.__name__].__file__ or "").resolve().parents[1]
#: Where the directory is named, read off the constant itself.
CONSTANT_MODULE = (
    Path(sys.modules[constants.__name__].__file__ or "")
    .resolve()
    .relative_to(SOURCE_ROOT)
    .as_posix()
)
#: The one writer: the adapter that persists the projection onto a branch,
#: read off the module its class is defined in.
PERSISTER_MODULE = (
    Path(inspect.getfile(inspect.getmodule(GitArtifactPersister) or constants))
    .resolve()
    .relative_to(SOURCE_ROOT)
    .as_posix()
)
#: The name the constant is bound to, so a module naming it is seen whether it
#: spells the value or imports the name.
CONSTANT = "ARTIFACT_DIR"
#: The names the directory is known by before the tree is read: the constant's.
SEEDS = frozenset({CONSTANT})
#: The modules permitted to name the directory, in the order their derived
#: names are read: the constant's, then the persister's, which imports it.
PERMITTED = (CONSTANT_MODULE, PERSISTER_MODULE)
#: How a callable that returns a directory path is carried in an alias set.
CALLED = "{}()"

#: Every call the writer makes on a path under the directory, with the reason
#: it is not a read of what is there.
EXEMPT = {
    "mkdir": "persist: the directory is made before the projection is written",
    "write_text": "persist: the projection itself",
    "exists": "clean: whether there is anything to remove, never its content",
    "rmtree": "clean: the removal",
    "is_path_ignored": (
        "the target's ignore question, asked of the directory's name and "
        "answered by git, not by reading the directory"
    ),
}
#: Reading what is under the directory, in every spelling a reader takes.
READS = frozenset(
    {
        "read_text",
        "read_bytes",
        "open",
        "iterdir",
        "glob",
        "rglob",
        "load",
        "loads",
    }
)

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def docstrings(tree: ast.AST) -> frozenset[int]:
    """The ids of the string constants that are docstrings, not values.

    Prose naming the directory is documentation, and the sources describe the
    projection in several places; a module, class or function's first
    statement is that prose.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, *DEFINITIONS)):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return frozenset(found)


def holds_the_directory(text: str) -> bool:
    """Whether *text* has the directory as one of its path segments.

    Split on ``/``, ``:`` and whitespace, so a relative, absolute,
    ``./``-prefixed or ``ref:path`` spelling is seen, and so is a path inside
    a shell command; a word that only contains the name is not.
    """
    return ARTIFACT_DIR in re.split(r"[/:\s]", text)


def constant_text(node: ast.AST) -> str | None:
    """A ``str`` constant's text, or a ``bytes`` constant's, decoded."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return os.fsdecode(node.value)
    return None


def imported_as(tree: ast.AST, seeds: frozenset[str] = SEEDS) -> frozenset[str]:
    """Every local name an import binds a directory name to, its own included."""
    return frozenset(
        node.asname or node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.alias) and node.name in seeds
    )


def names_a_seed(text: str, seeds: frozenset[str]) -> bool:
    """Whether *text* spells a directory name as one of its identifier tokens.

    A string that names the name is a reference to it: ``getattr(x,
    "ARTIFACT_DIR")``, an ``attrgetter`` path, a format field such as
    ``"{0.ARTIFACT_DIR}"`` and a ``module:attr`` string all hold it as a token.
    """
    return not seeds.isdisjoint(re.findall(r"\w+", text))


def names_the_directory(tree: ast.AST, seeds: frozenset[str] = SEEDS) -> bool:
    """Whether this module names the artifact directory at all.

    By a directory name, bare, imported under another name, read as a module
    attribute or spelled in a string, or by the directory's value written into
    any string that is not a docstring — a literal path under it carries the
    directory as surely as the constant does.
    """
    prose = docstrings(tree)
    if imported_as(tree, seeds):
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in seeds:
            return True
        if isinstance(node, ast.Attribute) and node.attr in seeds:
            return True
        text = constant_text(node)
        if (
            text is not None
            and id(node) not in prose
            and (holds_the_directory(text) or names_a_seed(text, seeds))
        ):
            return True
    return False


def holds_a_literal_seed(node: ast.AST, seeds: frozenset[str]) -> bool:
    """Whether a string constant inside *node* spells a directory name.

    ``getattr(x, "ARTIFACT_DIR")``, ``vars(x)["ARTIFACT_DIR"]``,
    ``x.__dict__["ARTIFACT_DIR"]``, ``operator.attrgetter("ARTIFACT_DIR")(x)``
    and ``"{0.ARTIFACT_DIR}".format(x)`` each hold one.  The walk is over
    *node*'s own finite subtree.
    """
    return any(
        isinstance(inner, ast.Constant)
        and isinstance(inner.value, str)
        and names_a_seed(inner.value, seeds)
        for inner in ast.walk(node)
    )


def _is_directory_expression(
    node: ast.expr, aliases: frozenset[str], seeds: frozenset[str] = SEEDS
) -> bool:
    """Whether this expression IS a path at or under the directory."""

    def inner(child: ast.expr) -> bool:
        return _is_directory_expression(child, aliases, seeds)

    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Attribute):
        return node.attr in seeds or ast.unparse(node) in aliases
    text = constant_text(node)
    if text is not None:
        return holds_the_directory(text)
    if isinstance(node, ast.JoinedStr):
        return any(
            inner(part.value if isinstance(part, ast.FormattedValue) else part)
            for part in node.values
        )
    if isinstance(node, ast.NamedExpr):
        return inner(node.value)
    if isinstance(node, ast.BinOp):
        # Any operator: a path joined, concatenated or ``%``-formatted.
        return inner(node.left) or inner(node.right)
    if isinstance(node, ast.IfExp):
        return inner(node.body) or inner(node.orelse)
    if isinstance(node, ast.BoolOp):
        return any(inner(value) for value in node.values)
    if isinstance(node, ast.Subscript):
        # An item of a display holding one, or a namespace read by the
        # constant's literal name (``vars(x)["ARTIFACT_DIR"]``).
        return inner(node.value) or holds_a_literal_seed(node, seeds)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        # A container holding one, such as an argv list.
        return any(inner(element) for element in node.elts)
    if isinstance(node, ast.Starred):
        return inner(node.value)
    if isinstance(node, ast.Dict):
        # A ``**`` display: its keys and values are what the callee receives.
        return any(inner(item) for item in (*node.keys, *node.values) if item)
    if isinstance(node, ast.Call):
        # The constant read by its literal name, a module-local function or
        # method that returns one, a path built or formatted from one
        # (``Path(ws, ".kodezart")``, ``"{}/c.json".format(ARTIFACT_DIR)``),
        # or one derived from one by a method of it
        # (``artifact_dir.joinpath(name)``).
        return (
            holds_a_literal_seed(node, seeds)
            or CALLED.format(ast.unparse(node.func)) in aliases
            or (
                isinstance(node.func, ast.Attribute)
                and CALLED.format(node.func.attr) in aliases
            )
            or any(inner(arg) for arg in node.args)
            or any(inner(keyword.value) for keyword in node.keywords)
            or (isinstance(node.func, ast.Attribute) and inner(node.func.value))
        )
    return False


def _bound(target: ast.expr) -> set[str]:
    """What a binding target names: a local, or an attribute such as a field."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Attribute):
        return {ast.unparse(target)}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {name for inner in target.elts for name in _bound(inner)}
    if isinstance(target, ast.Starred):
        return _bound(target.value)
    return set()


def _returned(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.expr]:
    """What a function hands back: its own returns and yields, not a nested scope's."""
    found: list[ast.expr] = []
    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (*DEFINITIONS, ast.Lambda)):
            continue
        if (
            isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom))
            and node.value is not None
        ):
            found.append(node.value)
        stack.extend(ast.iter_child_nodes(node))
    return found


def _aliases(tree: ast.AST, seeds: frozenset[str] = SEEDS) -> frozenset[str]:
    """Every name that stands for a path at or under the directory.

    Seeded with the directory names and every local name one is imported as,
    and grown to a fixed point, because a name bound from another such name
    carries the same path rather than a value of another kind.  A binding is
    an assignment, a walrus, a loop or comprehension target, or a ``with``
    target; an attribute target is carried by its spelling.  A function or
    method of this module whose return or yield is such a path, and a name
    bound to a lambda whose body is one, are carried as their called
    spelling (``name()``), so a call of one is such a path too.
    """
    aliases = set(seeds) | set(imported_as(tree, seeds))
    bindings: list[tuple[list[ast.expr], ast.expr]] = []
    returners: list[tuple[str, list[ast.expr]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            bindings.append((list(node.targets), node.value))
        elif (
            isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None
        ):
            bindings.append(([node.target], node.value))
        elif isinstance(node, ast.NamedExpr):
            bindings.append(([node.target], node.value))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            bindings.append(([node.target], node.iter))
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            bindings.append(([node.optional_vars], node.context_expr))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            returners.append((node.name, _returned(node)))
    changed = True
    while changed:
        previous = set(aliases)
        known = frozenset(aliases)
        for targets, value in bindings:
            if isinstance(value, ast.Lambda) or bound_method(value, known, seeds):
                # A lambda returning one, or a method of one held unbound
                # from its call (``read = artifact_dir.read_text``): a call of
                # the name is a call made on the directory.
                body = value.body if isinstance(value, ast.Lambda) else value
                if _is_directory_expression(body, known, seeds):
                    aliases.update(
                        CALLED.format(name)
                        for target in targets
                        for name in _bound(target)
                    )
                continue
            if not _is_directory_expression(value, known, seeds):
                continue
            for target in targets:
                aliases.update(_bound(target))
        for name, values in returners:
            if any(_is_directory_expression(value, known, seeds) for value in values):
                aliases.add(CALLED.format(name))
        changed = aliases != previous
    return frozenset(aliases)


def bound_method(
    value: ast.expr, aliases: frozenset[str], seeds: frozenset[str]
) -> str | None:
    """The method name, when *value* is a method of a directory path, uncalled."""
    if isinstance(value, ast.Attribute) and _is_directory_expression(
        value.value, aliases, seeds
    ):
        return value.attr
    return None


def bound_methods(tree: ast.AST, seeds: frozenset[str] = SEEDS) -> dict[str, str]:
    """Every name bound to a directory path's method, with the method's name."""
    aliases = _aliases(tree, seeds)
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value:
            targets, value = [node.target], node.value
        else:
            continue
        method = bound_method(value, aliases, seeds)
        if method is not None:
            found.update(
                (name, method) for target in targets for name in _bound(target)
            )
    return found


def calls_on_the_directory(
    tree: ast.AST, seeds: frozenset[str] = SEEDS
) -> frozenset[str]:
    """Every call made ON a path under the directory, or handed one.

    A method called on such a path, the same method called through a name
    it was bound to, and a function handed one as an argument:
    ``shutil.rmtree(artifact_dir)`` reaches what is there exactly as
    ``artifact_dir.iterdir()`` would.
    """
    aliases = _aliases(tree, seeds)
    methods = bound_methods(tree, seeds)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        if ast.unparse(called) in methods:
            found.add(methods[ast.unparse(called)])
            continue
        if isinstance(called, ast.Attribute) and _is_directory_expression(
            called.value, aliases, seeds
        ):
            found.add(called.attr)
            continue
        name = called.attr if isinstance(called, ast.Attribute) else None
        name = called.id if isinstance(called, ast.Name) else name
        if name is None or name == "Path":
            continue
        if any(
            _is_directory_expression(argument, aliases, seeds) for argument in node.args
        ):
            found.add(name)
        if any(
            keyword.value is not None
            and _is_directory_expression(keyword.value, aliases, seeds)
            for keyword in node.keywords
        ):
            found.add(name)
    return frozenset(found)


#: The compound statements whose bodies run when the module is imported.
MODULE_BLOCKS = (
    ast.If,
    ast.Try,
    ast.TryStar,
    ast.With,
    ast.AsyncWith,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ClassDef,
)


def module_level_names(
    tree: ast.Module, seeds: frozenset[str] = SEEDS
) -> frozenset[str]:
    """Every name this module binds, at import, to a directory name or path.

    Every statement that runs when the module is imported is read: the top
    level, and the bodies of an ``if``, ``try``, ``with``, loop or class there
    (a class attribute is bound under its own name).  An import of a
    directory name, an assignment, an annotated assignment and a walrus are
    the bindings another module can import or read.  The walk visits each
    statement of a finite tree once and does not enter a function.
    """
    bound: set[str] = set()
    stack: list[ast.AST] = list(tree.body)
    while stack:
        statement = stack.pop()
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            bound.update(alias.asname or alias.name for alias in statement.names)
        elif isinstance(statement, ast.Assign):
            bound.update(
                name for target in statement.targets for name in _bound(target)
            )
        elif isinstance(statement, ast.AnnAssign):
            bound.update(_bound(statement.target))
        elif isinstance(statement, ast.Expr) and isinstance(
            statement.value, ast.NamedExpr
        ):
            bound.update(_bound(statement.value.target))
        elif isinstance(statement, MODULE_BLOCKS):
            for field in ("body", "orelse", "finalbody", "handlers"):
                stack.extend(getattr(statement, field, []))
        elif isinstance(statement, ast.ExceptHandler):
            stack.extend(statement.body)
    return frozenset(bound & _aliases(tree, seeds))


def holds_the_directory_value(value: object) -> bool:
    """Whether a ``str``, ``bytes`` or ``PurePath`` value holds the directory."""
    if isinstance(value, (str, bytes, PurePath)):
        return holds_the_directory(os.fsdecode(value))
    return False


def names_by_object(namespaces: Iterable[types.ModuleType]) -> frozenset[str]:
    """Every name the imported modules bind to a value holding the directory.

    Read by object after import, not by spelling: every name in
    ``vars(module)``, and every attribute a class there binds in its own
    namespace, read through ``inspect.getattr_static``, whose value is a
    ``str``, ``bytes`` or ``PurePath`` holding the directory as a segment.
    However the source bound it (a branch, a walrus, a class body, any
    formatting), the value is what is compared.  A docstring (``__doc__``) is
    prose, as it is to the syntactic walk.
    """
    found: set[str] = set()
    for module in namespaces:
        for name, value in vars(module).items():
            if name != DOC and holds_the_directory_value(value):
                found.add(name)
            if isinstance(value, type):
                found.update(
                    attribute
                    for attribute in vars(value)
                    if attribute != DOC
                    and holds_the_directory_value(
                        inspect.getattr_static(value, attribute)
                    )
                )
    return frozenset(found)


#: The attribute a module or class keeps its docstring under.
DOC = "__doc__"
#: The permitted modules as imported: the constant's and the writer's.
PERMITTED_OBJECTS = (constants, artifact_persister)


def real_tree_names(
    trees: dict[str, ast.Module],
    objects: Iterable[types.ModuleType] = PERMITTED_OBJECTS,
) -> frozenset[str]:
    """The directory names for an imported tree: by syntax and by object."""
    return directory_names(trees) | names_by_object(objects)


def directory_names(trees: dict[str, ast.Module]) -> frozenset[str]:
    """The names that stand for the directory in every module of *trees*.

    The constant's own name, and every module-level name a permitted module
    (the constant's, then the persister's) binds to a directory expression,
    so an import of a derived name elsewhere names the directory too.
    """
    seeds = SEEDS
    for module in PERMITTED:
        if module in trees:
            seeds = seeds | module_level_names(trees[module], seeds)
    return seeds


def directory_yields(tree: ast.AST, seeds: frozenset[str] = SEEDS) -> list[str]:
    """Every return, yield or lambda in this module that hands back a directory path."""
    aliases = _aliases(tree, seeds)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)):
            value = node.value
        elif isinstance(node, ast.Lambda):
            value = node.body
        else:
            continue
        if value is not None and _is_directory_expression(value, aliases, seeds):
            found.append(f"line {node.lineno}: {ast.unparse(node)}")
    return found


def modules(root: Path) -> dict[str, ast.Module]:
    """Every ``.py`` module under *root*, parsed, keyed by its path there."""
    return {
        path.relative_to(root).as_posix(): ast.parse(path.read_text())
        for path in sorted(root.rglob("*.py"))
    }


def naming(
    trees: dict[str, ast.Module], seeds: frozenset[str] | None = None
) -> frozenset[str]:
    """Every module that names the directory, by any name the tree derives for it."""
    seeds = directory_names(trees) if seeds is None else seeds
    return frozenset(
        module for module, tree in trees.items() if names_the_directory(tree, seeds)
    )


def test_the_artifact_directory_is_named_by_its_constant_and_the_persister_alone():
    """Two modules name it: where it is declared, and the one that writes it.

    Everything else on both arms is free of it, so no module can be reading a
    projection it never names.
    """
    trees = modules(SOURCE_ROOT)
    named = naming(trees, real_tree_names(trees))
    assert named == {CONSTANT_MODULE, PERSISTER_MODULE}, sorted(named)


def test_no_site_reads_a_path_under_the_artifact_directory():
    """The writer makes, writes, asks about and removes; it never reads."""
    trees = modules(SOURCE_ROOT)
    seeds = real_tree_names(trees)
    made = set(calls_on_the_directory(trees[PERSISTER_MODULE], seeds)) | set(
        calls_on_the_directory(trees[CONSTANT_MODULE], seeds)
    )
    assert made == set(EXEMPT), sorted(made ^ set(EXEMPT))
    assert made.isdisjoint(READS), sorted(made & READS)


def test_every_exemption_carries_the_reason_it_is_one():
    assert all(reason.strip() for reason in EXEMPT.values())
    assert READS.isdisjoint(EXEMPT)


#: Where a planted reader sits in its package: a services-shaped module.
PLANTED = "services/planted.py"


def plant(root: Path, body: str, *, at: str = PLANTED) -> dict[str, ast.Module]:
    """A package under *root* holding one planted module, walked as the tree is."""
    module = root / at
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(body)
    return modules(root)


@pytest.mark.parametrize(
    "body,at",
    [
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "def satisfied(workspace):\n"
            "    entry = Path(workspace) / ARTIFACT_DIR / 'criteria.json'\n"
            "    return json.loads(entry.read_text())\n",
            PLANTED,
            id="a-service-reading-the-projected-file",
        ),
        pytest.param(
            "def satisfied(workspace):\n"
            "    return Path(workspace, '.kodezart/criteria.json').read_text()\n",
            PLANTED,
            id="a-literal-path-under-the-directory",
        ),
        pytest.param(
            "from kodezart.core import constants\n"
            "def satisfied(workspace):\n"
            "    entry = Path(workspace) / constants.ARTIFACT_DIR / 'criteria.json'\n"
            "    return json.loads(entry.read_text())\n",
            PLANTED,
            id="the-constant-read-as-a-module-attribute",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR as projection\n"
            "def satisfied(workspace):\n"
            "    return (Path(workspace) / projection / 'criteria.json').read_text()\n",
            PLANTED,
            id="the-constant-imported-under-another-name",
        ),
        pytest.param(
            "def satisfied(workspace):\n"
            "    return open(f'{workspace}/.kodezart/criteria.json').read()\n",
            PLANTED,
            id="an-f-string-path-under-the-directory",
        ),
        pytest.param(
            "def satisfied():\n"
            "    return [entry.name for entry in Path('./.kodezart').iterdir()]\n",
            PLANTED,
            id="a-dot-slash-path",
        ),
        pytest.param(
            "async def satisfied(git, cwd):\n"
            "    return await git.show(cwd, 'origin:.kodezart/criteria.json')\n",
            PLANTED,
            id="a-ref-qualified-path",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "def satisfied(workspace):\n"
            "    if (entry := Path(workspace) / ARTIFACT_DIR / 'c.json').exists():\n"
            "        return entry.read_text()\n",
            PLANTED,
            id="a-walrus-bound-path",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "class ArtifactPersister:\n"
            "    def persist(self, workspace):\n"
            "        artifact_dir = Path(workspace) / ARTIFACT_DIR\n"
            "        return [entry.name for entry in artifact_dir.iterdir()]\n",
            PERSISTER_MODULE,
            id="a-read-inside-the-writer-itself",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "class ArtifactPersister:\n"
            "    def persist(self, workspace):\n"
            "        artifact_dir = Path(workspace) / ARTIFACT_DIR\n"
            "        artifact_dir.mkdir(exist_ok=True)\n"
            "        self._artifact_dir = artifact_dir\n"
            "        return self._artifact_dir.joinpath('ticket.json').read_text()\n",
            PERSISTER_MODULE,
            id="an-attribute-held-path-read-inside-the-writer",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "class ArtifactPersister:\n"
            "    def persist(self, workspace):\n"
            "        return open(f'{workspace}/{ARTIFACT_DIR}/criteria.json').read()\n",
            PERSISTER_MODULE,
            id="an-f-string-naming-the-constant-inside-the-writer",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "class ArtifactPersister:\n"
            "    def _projection(self, workspace):\n"
            "        return Path(workspace) / ARTIFACT_DIR\n"
            "    def persist(self, workspace):\n"
            "        entry = self._projection(workspace).joinpath('c.json')\n"
            "        return entry.read_text()\n",
            PERSISTER_MODULE,
            id="a-method-returning-the-path-read-inside-the-writer",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "def _projection(workspace):\n"
            "    return Path(workspace) / ARTIFACT_DIR\n"
            "def persist(workspace):\n"
            "    return _projection(workspace).read_text()\n",
            PERSISTER_MODULE,
            id="a-module-function-returning-the-path-read-inside-the-writer",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "def _entries(workspace):\n"
            "    yield Path(workspace) / ARTIFACT_DIR / 'c.json'\n"
            "def persist(workspace):\n"
            "    return [entry.read_text() for entry in _entries(workspace)]\n",
            PERSISTER_MODULE,
            id="a-generator-yielding-the-path-read-inside-the-writer",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "_projection = lambda workspace: Path(workspace) / ARTIFACT_DIR\n"
            "def persist(workspace):\n"
            "    return _projection(workspace).read_text()\n",
            PERSISTER_MODULE,
            id="a-lambda-returning-the-path-read-inside-the-writer",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "def persist(workspace):\n"
            "    argv = ['cat', f'{workspace}/{ARTIFACT_DIR}/c.json']\n"
            "    subprocess.run(argv, check=True)\n",
            PERSISTER_MODULE,
            id="an-argv-list-inside-the-writer",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "def persist(workspace):\n"
            "    subprocess.run(('cat', f'{workspace}/{ARTIFACT_DIR}/c.json'))\n",
            PERSISTER_MODULE,
            id="an-argv-tuple-inside-the-writer",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "def persist(workspace, consume):\n"
            "    return consume({Path(workspace) / ARTIFACT_DIR})\n",
            PERSISTER_MODULE,
            id="a-set-argument-inside-the-writer",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "def persist(workspace, consume):\n"
            "    return consume(*[Path(workspace) / ARTIFACT_DIR])\n",
            PERSISTER_MODULE,
            id="a-starred-argument-inside-the-writer",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "def persist(workspace, consume):\n"
            "    return consume(**{'path': Path(workspace) / ARTIFACT_DIR})\n",
            PERSISTER_MODULE,
            id="a-keyword-display-inside-the-writer",
        ),
        pytest.param(
            "from kodezart.core import constants\n"
            "def persist(workspace):\n"
            "    directory = Path(workspace) / getattr(constants, 'ARTIFACT_DIR')\n"
            "    entry = directory / 'c.json'\n"
            "    return entry.read_text()\n",
            PERSISTER_MODULE,
            id="the-constant-read-by-getattr-inside-the-writer",
        ),
        pytest.param(
            "from pathlib import Path as _P\n"
            "from kodezart.core import constants as _c\n"
            "def decide(repo_path, decide_lane_entry):\n"
            '    _d = _P(repo_path) / getattr(_c, "ARTIFACT_DIR")\n'
            '    _ = [p.read_text() for p in _d.glob("*.json")]'
            " if _d.exists() else []\n"
            "    return decide_lane_entry()\n",
            "services/lane_entry.py",
            id="a-lane-entry-read-through-getattr-on-the-constants-module",
        ),
        pytest.param(
            "def decide(repo_path, decide_lane_entry):\n"
            "    try:\n"
            '        _ = __import__("subprocess").run("cat .kodezart/criteria.json",'
            " shell=True, cwd=repo_path, capture_output=True).stdout\n"
            "    except OSError:\n"
            "        pass\n"
            "    return decide_lane_entry()\n",
            "services/lane_entry.py",
            id="a-lane-entry-read-through-a-shell-command",
        ),
        pytest.param(
            "def decide(repo_path, decide_lane_entry):\n"
            "    try:\n"
            "        _ = open(repo_path.encode()"
            ' + b"/.kodezart/criteria.json").read()\n'
            "    except OSError:\n"
            "        pass\n"
            "    return decide_lane_entry()\n",
            "services/lane_entry.py",
            id="a-lane-entry-read-through-a-bytes-path",
        ),
    ],
)
def test_a_planted_read_is_reported(body, at, tmp_path):
    trees = plant(tmp_path, body, at=at)
    assert at in naming(trees)
    assert calls_on_the_directory(trees[at], directory_names(trees)) - set(EXEMPT)


#: The writer's module, as a planted read inside it starts.
WRITER_HEAD = (
    "import operator\n"
    "from pathlib import Path\n"
    "from kodezart.core import constants as _constants\n"
    "from kodezart.core.constants import ARTIFACT_DIR\n"
    "class GitArtifactPersister:\n"
    "    def persist(self, workspace, artifacts):\n"
    "        artifact_dir = Path(workspace) / ARTIFACT_DIR\n"
    "        artifact_dir.mkdir(exist_ok=True)\n"
)


@pytest.mark.parametrize(
    "read",
    [
        pytest.param(
            "open('%s/%s/criteria.json' % (workspace, ARTIFACT_DIR)).read()",
            id="a-percent-formatted-path",
        ),
        pytest.param(
            "open('{}/{}/criteria.json'.format(workspace, ARTIFACT_DIR)).read()",
            id="a-str-format-call",
        ),
        pytest.param(
            "open('{w}/{d}/c.json'.format(w=workspace, d=ARTIFACT_DIR)).read()",
            id="a-str-format-call-by-keyword",
        ),
        pytest.param(
            "(Path(workspace) / (ARTIFACT_DIR if workspace else '.') / 'c.json')"
            ".read_text()",
            id="a-conditional-expression",
        ),
        pytest.param(
            "(Path(workspace) / (ARTIFACT_DIR or '.') / 'c.json').read_text()",
            id="a-boolean-operation",
        ),
        pytest.param(
            "(Path(workspace) / (ARTIFACT_DIR,)[0] / 'c.json').read_text()",
            id="an-item-of-a-display",
        ),
        pytest.param(
            "(Path(workspace) / vars(_constants)['ARTIFACT_DIR'] / 'c.json')"
            ".read_text()",
            id="a-vars-subscript-by-the-literal-name",
        ),
        pytest.param(
            "(Path(workspace) / _constants.__dict__['ARTIFACT_DIR'] / 'c.json')"
            ".read_text()",
            id="a-dict-subscript-by-the-literal-name",
        ),
        pytest.param(
            "(Path(workspace) / operator.attrgetter('ARTIFACT_DIR')(_constants)"
            " / 'c.json').read_text()",
            id="an-attrgetter-by-the-literal-name",
        ),
        pytest.param(
            "(Path(workspace) / '{0.ARTIFACT_DIR}'.format(_constants) / 'c.json')"
            ".read_text()",
            id="a-format-field-by-the-literal-name",
        ),
        pytest.param(
            "open(workspace.encode() + b'/.kodezart/criteria.json').read()",
            id="a-bytes-path",
        ),
    ],
)
def test_a_read_inside_the_writer_is_reported_in_every_spelling(read, tmp_path):
    """Each expression form, planted as a read inside the permitted writer."""
    trees = plant(
        tmp_path, WRITER_HEAD + f"        return {read}\n", at=PERSISTER_MODULE
    )
    calls = calls_on_the_directory(trees[PERSISTER_MODULE], directory_names(trees))
    assert calls & READS, sorted(calls)


@pytest.mark.parametrize(
    "held",
    [
        pytest.param(
            "        previous = artifact_dir / 'criteria.json' if artifacts else None\n"
            "        if previous is not None and previous.exists():\n"
            "            return previous.read_text()\n",
            id="a-name-bound-to-a-conditional-expression",
        ),
        pytest.param(
            "        read = (artifact_dir / 'criteria.json').read_text\n"
            "        return read()\n",
            id="a-bound-method-held-in-a-local",
        ),
        pytest.param(
            "        self._read = (artifact_dir / 'criteria.json').read_bytes\n"
            "        return self._read()\n",
            id="a-bound-method-held-on-the-instance",
        ),
        pytest.param(
            "        segment = (ARTIFACT_DIR,)[0]\n"
            "        return (Path(workspace) / segment / 'c.json').read_text()\n",
            id="a-name-bound-to-an-item-of-a-display",
        ),
    ],
)
def test_a_read_through_a_held_name_inside_the_writer_is_reported(held, tmp_path):
    """A name bound to a directory path, or to a method of one, carries it."""
    trees = plant(tmp_path, WRITER_HEAD + held, at=PERSISTER_MODULE)
    calls = calls_on_the_directory(trees[PERSISTER_MODULE], directory_names(trees))
    assert calls & READS, sorted(calls)


def test_a_planted_write_only_module_is_not_reported(tmp_path):
    """The control's control: writing under the directory stays green."""
    trees = plant(
        tmp_path,
        "from kodezart.core.constants import ARTIFACT_DIR\n"
        "def persist(workspace, artifacts):\n"
        "    artifact_dir = Path(workspace) / ARTIFACT_DIR\n"
        "    artifact_dir.mkdir(exist_ok=True)\n"
        "    for name, content in artifacts.items():\n"
        "        (artifact_dir / name).write_text(content)\n",
        at=PERSISTER_MODULE,
    )
    assert PERSISTER_MODULE in naming(trees)
    made = calls_on_the_directory(trees[PERSISTER_MODULE], directory_names(trees))
    assert made <= set(EXEMPT)


def test_prose_naming_the_directory_is_not_a_module_naming_it(tmp_path):
    """A docstring that holds the path as a segment is documentation."""
    trees = plant(
        tmp_path,
        '""".kodezart/criteria.json is written for a person and read by nothing."""\n'
        "def satisfied(record):\n"
        '    """.kodezart/criteria.json is never consulted here."""\n'
        "    return record.done\n",
    )
    # The prose does hold the directory as a segment: only its being a
    # docstring keeps the module out.
    assert holds_the_directory(ast.get_docstring(trees[PLANTED]) or "")
    assert PLANTED not in naming(trees)


def test_the_permitted_modules_derive_the_constant_alone_and_hand_out_no_path():
    """The directory names the tree derives are the constant's own, at head.

    Each permitted module binds the directory, at its top level, to the
    constant's name and to nothing else, and no return, yield or lambda in
    either hands a directory path out: so no other module can import a
    derived path or be given one by calling a permitted module.
    """
    trees = modules(SOURCE_ROOT)
    seeds = SEEDS
    derived: dict[str, frozenset[str]] = {}
    for module in PERMITTED:
        derived[module] = module_level_names(trees[module], seeds)
        seeds = seeds | derived[module]
    assert derived == {CONSTANT_MODULE: SEEDS, PERSISTER_MODULE: SEEDS}
    assert directory_names(trees) == SEEDS
    # By object, after import: the names the two modules bind to a value
    # holding the directory, however their source spells the binding.
    assert names_by_object(PERMITTED_OBJECTS) == {CONSTANT}
    assert {module: directory_yields(trees[module], SEEDS) for module in PERMITTED} == {
        CONSTANT_MODULE: [],
        PERSISTER_MODULE: [],
    }


#: A services-shaped module reading the derived path under its derived name.
DERIVED_READER = (
    "from kodezart.core.constants import CRITERIA_PATH\n"
    "def satisfied(workspace):\n"
    "    return (Path(workspace) / CRITERIA_PATH).read_text()\n"
)

#: The constant's module as the tree declares it, with one derived path.
DERIVING_CONSTANTS = (
    'ARTIFACT_DIR = ".kodezart"\nCRITERIA_PATH = f"{ARTIFACT_DIR}/criteria.json"\n'
)


@pytest.mark.parametrize(
    "deriving,derived_at,reader",
    [
        pytest.param(
            DERIVING_CONSTANTS,
            CONSTANT_MODULE,
            "from kodezart.core.constants import CRITERIA_PATH\n"
            "def satisfied(workspace):\n"
            "    return (Path(workspace) / CRITERIA_PATH).read_text()\n",
            id="a-derived-path-imported-from-the-constants-module",
        ),
        pytest.param(
            DERIVING_CONSTANTS,
            CONSTANT_MODULE,
            "from kodezart.core import constants\n"
            "def satisfied(workspace):\n"
            "    return (Path(workspace) / constants.CRITERIA_PATH).read_text()\n",
            id="a-derived-path-read-as-a-module-attribute",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "PROJECTED = f'{ARTIFACT_DIR}/criteria.json'\n",
            PERSISTER_MODULE,
            "from kodezart.adapters.git.artifact_persister import PROJECTED as p\n"
            "def satisfied(workspace):\n"
            "    return (Path(workspace) / p).read_text()\n",
            id="a-derived-path-imported-from-the-writer-under-another-name",
        ),
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\n'
            'CRITERIA_PATH = "%s/criteria.json" % ARTIFACT_DIR\n',
            CONSTANT_MODULE,
            DERIVED_READER,
            id="a-percent-formatted-derived-path",
        ),
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\n'
            "if ARTIFACT_DIR:\n"
            '    CRITERIA_PATH = f"{ARTIFACT_DIR}/criteria.json"\n',
            CONSTANT_MODULE,
            DERIVED_READER,
            id="a-derived-path-bound-under-an-if",
        ),
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\n'
            "try:\n"
            '    CRITERIA_PATH = f"{ARTIFACT_DIR}/criteria.json"\n'
            "except ValueError:\n"
            "    pass\n",
            CONSTANT_MODULE,
            DERIVED_READER,
            id="a-derived-path-bound-under-a-try",
        ),
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\n'
            '(CRITERIA_PATH := f"{ARTIFACT_DIR}/criteria.json")\n',
            CONSTANT_MODULE,
            DERIVED_READER,
            id="a-derived-path-bound-by-a-top-level-walrus",
        ),
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\n'
            'CRITERIA_PATH = ARTIFACT_DIR and f"{ARTIFACT_DIR}/criteria.json"\n',
            CONSTANT_MODULE,
            DERIVED_READER,
            id="a-derived-path-through-a-boolean-operation",
        ),
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\n'
            "class ArtifactPaths:\n"
            '    CRITERIA = f"{ARTIFACT_DIR}/criteria.json"\n',
            CONSTANT_MODULE,
            "from kodezart.core.constants import ArtifactPaths\n"
            "def satisfied(workspace):\n"
            "    return (Path(workspace) / ArtifactPaths.CRITERIA).read_text()\n",
            id="a-class-attribute-of-the-constants-module",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "class GitArtifactPersister:\n"
            '    CRITERIA = f"{ARTIFACT_DIR}/criteria.json"\n',
            PERSISTER_MODULE,
            "from kodezart.adapters.git.artifact_persister import "
            "GitArtifactPersister\n"
            "def satisfied(workspace):\n"
            "    entry = Path(workspace) / GitArtifactPersister.CRITERIA\n"
            "    return entry.read_text()\n",
            id="a-class-attribute-of-the-writer",
        ),
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\nCRITERIA_BYTES = b".kodezart/criteria.json"\n',
            CONSTANT_MODULE,
            "from kodezart.core.constants import CRITERIA_BYTES\n"
            "def satisfied(workspace):\n"
            "    return open(workspace.encode() + b'/' + CRITERIA_BYTES).read()\n",
            id="a-derived-bytes-path",
        ),
    ],
)
def test_a_derived_name_read_in_another_module_is_reported(
    deriving, derived_at, reader, tmp_path
):
    """A permitted module's derived path names the directory wherever it goes."""
    plant(tmp_path, deriving, at=derived_at)
    trees = plant(tmp_path, reader)
    seeds = directory_names(trees)
    assert seeds > SEEDS, sorted(seeds)
    assert PLANTED in naming(trees)
    assert calls_on_the_directory(trees[PLANTED], seeds) & READS


def imported(source: str, root: Path) -> types.ModuleType:
    """*source* written under *root* and imported, for its values.

    Imported by path under a name of its own, so the shipped module of the
    same source path is neither replaced nor read.
    """
    path = root / "planted_constants.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("planted_constants", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "source,derived",
    [
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\nCRITERIA_PATH = "%s/c.json" % ARTIFACT_DIR\n',
            "CRITERIA_PATH",
            id="a-percent-formatted-value",
        ),
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\n'
            "if ARTIFACT_DIR:\n"
            '    CRITERIA_PATH = f"{ARTIFACT_DIR}/c.json"\n',
            "CRITERIA_PATH",
            id="a-value-bound-under-an-if",
        ),
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\n(CRITERIA_PATH := f"{ARTIFACT_DIR}/c.json")\n',
            "CRITERIA_PATH",
            id="a-value-bound-by-a-walrus",
        ),
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\n'
            "class ArtifactPaths:\n"
            '    CRITERIA = f"{ARTIFACT_DIR}/c.json"\n',
            "CRITERIA",
            id="a-class-attribute",
        ),
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\nCRITERIA_BYTES = b".kodezart/c.json"\n',
            "CRITERIA_BYTES",
            id="a-bytes-value",
        ),
        pytest.param(
            "from pathlib import PurePosixPath\n"
            'ARTIFACT_DIR = ".kodezart"\n'
            'CRITERIA = PurePosixPath(ARTIFACT_DIR, "c.json")\n',
            "CRITERIA",
            id="a-pure-path-value",
        ),
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\n'
            'globals()["CRITERIA_PATH"] = ARTIFACT_DIR + "/c.json"\n',
            "CRITERIA_PATH",
            id="a-value-bound-through-globals-at-import",
        ),
    ],
)
def test_the_names_a_permitted_module_binds_are_derived_by_object(
    source, derived, tmp_path
):
    """Every name whose value holds the directory, however the source bound it."""
    assert names_by_object([imported(source, tmp_path)]) == {CONSTANT, derived}


def test_a_value_only_resembling_the_directory_or_a_docstring_is_not_derived(
    tmp_path,
):
    """A look-alike segment and a docstring naming the path are not names of it."""
    module = imported(
        '""".kodezart/c.json is written for a person."""\n'
        'ARTIFACT_DIR = ".kodezart"\n'
        'BACKUP = ".kodezart-backup/c.json"\n'
        "class Paths:\n"
        '    """.kodezart/c.json is documented here."""\n',
        tmp_path,
    )
    assert names_by_object([module]) == {CONSTANT}


def test_a_name_only_the_object_shows_names_the_directory_where_it_is_read(
    tmp_path,
):
    """A binding the syntax cannot see is derived by object and named elsewhere.

    ``globals()[...]`` at the constants module's top level binds a name the
    syntactic walk does not read; the imported module's own namespace holds it.
    """
    source = (
        'ARTIFACT_DIR = ".kodezart"\n'
        'globals()["CRITERIA_PATH"] = ARTIFACT_DIR + "/criteria.json"\n'
    )
    plant(tmp_path, source, at=CONSTANT_MODULE)
    trees = plant(tmp_path, DERIVED_READER)
    assert PLANTED not in naming(trees)
    seeds = real_tree_names(trees, [imported(source, tmp_path)])
    assert PLANTED in naming(trees, seeds)
    assert calls_on_the_directory(trees[PLANTED], seeds) & READS


@pytest.mark.parametrize(
    "body,at",
    [
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "class GitArtifactPersister:\n"
            "    def criteria_path(self, workspace):\n"
            "        return Path(workspace) / ARTIFACT_DIR / 'criteria.json'\n",
            PERSISTER_MODULE,
            id="a-writer-method-returning-the-path",
        ),
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\n'
            "def entries(workspace):\n"
            "    yield Path(workspace) / ARTIFACT_DIR\n",
            CONSTANT_MODULE,
            id="a-constants-generator-yielding-the-path",
        ),
        pytest.param(
            'ARTIFACT_DIR = ".kodezart"\n'
            "projection = lambda workspace: Path(workspace) / ARTIFACT_DIR\n",
            CONSTANT_MODULE,
            id="a-constants-lambda-returning-the-path",
        ),
    ],
)
def test_a_permitted_module_handing_a_path_out_is_reported(body, at, tmp_path):
    """A return, a yield or a lambda that hands the path out is seen."""
    trees = plant(tmp_path, body, at=at)
    assert directory_yields(trees[at], directory_names(trees))


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "from kodezart.core import constants\n"
            "def satisfied():\n"
            "    return getattr(constants, 'ARTIFACT_DIR')\n",
            id="getattr-with-the-literal-name",
        ),
        pytest.param(
            "import operator\n"
            "from kodezart import core\n"
            "def satisfied():\n"
            "    return operator.attrgetter('constants.ARTIFACT_DIR')(core)\n",
            id="an-attrgetter-path",
        ),
        pytest.param(
            "from kodezart.core import constants\n"
            "def satisfied():\n"
            "    return '{0.ARTIFACT_DIR}'.format(constants)\n",
            id="a-format-field",
        ),
        pytest.param(
            "import pkgutil\n"
            "def satisfied():\n"
            "    return pkgutil.resolve_name('kodezart.core.constants:ARTIFACT_DIR')\n",
            id="a-module-attr-string",
        ),
    ],
)
def test_a_literal_naming_the_constant_names_the_directory(body, tmp_path):
    """A string that spells the constant's name is a reference to it."""
    trees = plant(tmp_path, body)
    assert PLANTED in naming(trees)


def test_a_path_returned_from_a_helper_in_another_module_is_unseen_at_its_reader(
    tmp_path,
):
    """The stated limit: a value handed across a function boundary.

    The helper names the directory and is reported for it; the reader, which
    only calls the helper, names nothing the walk can resolve at its site.
    """
    plant(
        tmp_path,
        "from kodezart.core.constants import ARTIFACT_DIR\n"
        "def projection(workspace):\n"
        "    return Path(workspace) / ARTIFACT_DIR\n",
        at="services/helper.py",
    )
    trees = plant(
        tmp_path,
        "from kodezart.services.helper import projection\n"
        "def satisfied(workspace):\n"
        "    return projection(workspace).read_text()\n",
        at="services/reader.py",
    )
    assert "services/helper.py" in naming(trees)
    assert "services/reader.py" not in naming(trees)
    assert (
        calls_on_the_directory(trees["services/reader.py"], directory_names(trees))
        == frozenset()
    )


def test_a_directory_name_built_at_run_time_is_unseen(tmp_path):
    """The stated limit: a name built at run time names nothing."""
    trees = plant(
        tmp_path,
        "from kodezart.core import constants\n"
        "def satisfied(workspace):\n"
        "    entry = Path(workspace) / getattr(constants, 'ARTIFACT' + '_DIR')\n"
        "    return (entry / 'criteria.json').read_text()\n",
    )
    assert PLANTED not in naming(trees)


def test_a_binding_made_only_when_a_function_runs_is_unseen(tmp_path):
    """The stated limit: a ``globals()`` binding inside a function body."""
    trees = plant(
        tmp_path,
        "from kodezart.core.constants import ARTIFACT_DIR\n"
        "def persist(workspace):\n"
        "    globals()['projection'] = Path(workspace) / ARTIFACT_DIR\n"
        "    return projection.read_text()\n",
        at=PERSISTER_MODULE,
    )
    assert PERSISTER_MODULE in naming(trees)
    made = calls_on_the_directory(trees[PERSISTER_MODULE], directory_names(trees))
    assert made & READS == set(), sorted(made)
