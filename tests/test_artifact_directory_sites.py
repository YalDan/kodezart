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

The directory is seen in every spelling a module can give it: the constant's
bare name, an import of it under another name, the constant read as a
module attribute, and any string that holds it as a path segment — split on
``/`` and ``:``, so a relative, absolute, ``./``-prefixed, f-string or
``ref:path`` spelling is one.  A name bound to such a path, by assignment, a
walrus, a loop, a ``with`` target or an attribute such as
``self._artifact_dir``, carries it on.

The walk is textual and executes nothing, which is what lets it speak for
every ``.py`` module rather than for the paths a fixture reaches.  Its blind
spots, which review has to read from the code instead: a directory name
assembled from fragments or read from configuration, and a read made through
a helper that is handed the path and names none of it.
"""

import ast
import inspect
import re
import sys
from pathlib import Path

import pytest

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

    Split on ``/`` and on ``:``, so a relative, absolute, ``./``-prefixed or
    ``ref:path`` spelling is seen, and prose that merely mentions the word
    beside other text is not.
    """
    return ARTIFACT_DIR in re.split(r"[/:]", text)


def imported_as(tree: ast.AST) -> frozenset[str]:
    """Every local name an import binds the constant to, its own included."""
    return frozenset(
        node.asname or node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.alias) and node.name == CONSTANT
    )


def names_the_directory(tree: ast.AST) -> bool:
    """Whether this module names the artifact directory at all.

    By the constant's name, bare, imported under another name or read as a
    module attribute, or by its value written into any string that is not a
    docstring — a literal path under it carries the directory as surely as
    the constant does.
    """
    prose = docstrings(tree)
    if imported_as(tree):
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == CONSTANT:
            return True
        if isinstance(node, ast.Attribute) and node.attr == CONSTANT:
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in prose
            and holds_the_directory(node.value)
        ):
            return True
    return False


def _is_directory_expression(node: ast.expr, aliases: frozenset[str]) -> bool:
    """Whether this expression IS a path at or under the directory."""
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Attribute):
        return node.attr == CONSTANT or ast.unparse(node) in aliases
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return holds_the_directory(node.value)
    if isinstance(node, ast.JoinedStr):
        return any(
            _is_directory_expression(
                part.value if isinstance(part, ast.FormattedValue) else part, aliases
            )
            for part in node.values
        )
    if isinstance(node, ast.NamedExpr):
        return _is_directory_expression(node.value, aliases)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        return _is_directory_expression(node.left, aliases) or _is_directory_expression(
            node.right, aliases
        )
    if isinstance(node, ast.Call):
        # A path built from one (``Path(ws, ".kodezart")``), or derived from
        # one by a method of it (``artifact_dir.joinpath(name)``).
        return any(_is_directory_expression(arg, aliases) for arg in node.args) or (
            isinstance(node.func, ast.Attribute)
            and _is_directory_expression(node.func.value, aliases)
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


def _aliases(tree: ast.AST) -> frozenset[str]:
    """Every name that stands for a path at or under the directory.

    Seeded with every local name the constant is imported as, and grown to a
    fixed point, because a name bound from another such name carries the
    same path rather than a value of another kind.  A binding is an
    assignment, a walrus, a loop or comprehension target, or a ``with``
    target; an attribute target is carried by its spelling.
    """
    aliases = {CONSTANT} | set(imported_as(tree))
    bindings: list[tuple[list[ast.expr], ast.expr]] = []
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
    changed = True
    while changed:
        previous = set(aliases)
        for targets, value in bindings:
            if not _is_directory_expression(value, frozenset(aliases)):
                continue
            for target in targets:
                aliases.update(_bound(target))
        changed = aliases != previous
    return frozenset(aliases)


def calls_on_the_directory(tree: ast.AST) -> frozenset[str]:
    """Every call made ON a path under the directory, or handed one.

    A method called on such a path, and a function handed one as an
    argument: ``shutil.rmtree(artifact_dir)`` reaches what is there exactly
    as ``artifact_dir.iterdir()`` would.
    """
    aliases = _aliases(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        if isinstance(called, ast.Attribute) and _is_directory_expression(
            called.value, aliases
        ):
            found.add(called.attr)
            continue
        name = called.attr if isinstance(called, ast.Attribute) else None
        name = called.id if isinstance(called, ast.Name) else name
        if name is None or name == "Path":
            continue
        if any(_is_directory_expression(argument, aliases) for argument in node.args):
            found.add(name)
        if any(
            keyword.value is not None
            and _is_directory_expression(keyword.value, aliases)
            for keyword in node.keywords
        ):
            found.add(name)
    return frozenset(found)


def modules(root: Path) -> dict[str, ast.Module]:
    """Every ``.py`` module under *root*, parsed, keyed by its path there."""
    return {
        path.relative_to(root).as_posix(): ast.parse(path.read_text())
        for path in sorted(root.rglob("*.py"))
    }


def naming(trees: dict[str, ast.Module]) -> frozenset[str]:
    """Every module that names the directory."""
    return frozenset(
        module for module, tree in trees.items() if names_the_directory(tree)
    )


def test_the_artifact_directory_is_named_by_its_constant_and_the_persister_alone():
    """Two modules name it: where it is declared, and the one that writes it.

    Everything else on both arms is free of it, so no module can be reading a
    projection it never names.
    """
    named = naming(modules(SOURCE_ROOT))
    assert named == {CONSTANT_MODULE, PERSISTER_MODULE}, sorted(named)


def test_no_site_reads_a_path_under_the_artifact_directory():
    """The writer makes, writes, asks about and removes; it never reads."""
    trees = modules(SOURCE_ROOT)
    made = set(calls_on_the_directory(trees[PERSISTER_MODULE])) | set(
        calls_on_the_directory(trees[CONSTANT_MODULE])
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
    ],
)
def test_a_planted_read_is_reported(body, at, tmp_path):
    trees = plant(tmp_path, body, at=at)
    assert at in naming(trees)
    assert calls_on_the_directory(trees[at]) - set(EXEMPT)


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
    assert calls_on_the_directory(trees[PERSISTER_MODULE]) <= set(EXEMPT)


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
