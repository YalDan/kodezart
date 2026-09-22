"""The artifact directory is written and never read (KOD-96-AC-29).

The tracker is the source a run re-enters and reads satisfaction from.  A
directory committed onto a branch is a projection of that source: written for
a human to read in the repository, read back by nothing, so it can never
answer a question the tracker answers differently.  A single read would make
it a second source, and the two would part on the first write one of them
missed.

Nothing is listed by hand that the tree can be asked for: the directory is
the constant's own value, the modules permitted to name it are the constant's
module and the persister that writes it, and the scanned tree is the package
the constant is packaged in.  What IS listed is the calls the persister may
make on a path under the directory, each with the reason it is not a read,
and that table is checked against the walk in both directions, so an
exemption for a call that no longer exists is as red as an unexempted call.

The walk is textual and executes nothing, which is what lets it speak for the
whole tree rather than for the paths a fixture reaches.  Its blind spots,
which review has to read from the code instead: a directory name assembled
from fragments or read from configuration, and a read made through a helper
that is handed the path and names none of it.
"""

import ast
import sys
from pathlib import Path

import pytest

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
#: The one writer: the adapter that persists the projection onto a branch.
PERSISTER_MODULE = "adapters/git/artifact_persister.py"
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


def names_the_directory(tree: ast.AST) -> bool:
    """Whether this module names the artifact directory at all.

    By the constant's name, or by its value written out — a literal path
    under it carries the directory as surely as the constant does.
    """
    prose = docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == CONSTANT:
            return True
        if isinstance(node, ast.alias) and (node.asname or node.name) == CONSTANT:
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in prose
            and (
                node.value == ARTIFACT_DIR or node.value.startswith(f"{ARTIFACT_DIR}/")
            )
        ):
            return True
    return False


def _is_directory_expression(node: ast.expr, aliases: frozenset[str]) -> bool:
    """Whether this expression IS a path at or under the directory."""
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value == ARTIFACT_DIR or node.value.startswith(f"{ARTIFACT_DIR}/")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _is_directory_expression(node.left, aliases) or _is_directory_expression(
            node.right, aliases
        )
    if isinstance(node, ast.Call):
        return any(_is_directory_expression(arg, aliases) for arg in node.args)
    return False


def _aliases(tree: ast.AST) -> frozenset[str]:
    """Every name that stands for a path at or under the directory.

    Grown to a fixed point, because a name bound from another such name
    carries the same path rather than a value of another kind.
    """
    aliases = {CONSTANT}
    bindings: list[tuple[list[ast.expr], ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            bindings.append((list(node.targets), node.value))
        elif (
            isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None
        ):
            bindings.append(([node.target], node.value))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            bindings.append(([node.target], node.iter))
    changed = True
    while changed:
        previous = set(aliases)
        for targets, value in bindings:
            if not _is_directory_expression(value, frozenset(aliases)):
                continue
            for target in targets:
                aliases.update(
                    inner.id
                    for inner in ast.walk(target)
                    if isinstance(inner, ast.Name)
                )
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


def modules() -> dict[str, ast.Module]:
    """Every shipped module, parsed once, keyed by its path in the package."""
    return {
        path.relative_to(SOURCE_ROOT).as_posix(): ast.parse(path.read_text())
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
    }


def test_the_artifact_directory_is_named_by_its_constant_and_the_persister_alone():
    """Two modules name it: where it is declared, and the one that writes it.

    Everything else on both arms is free of it, so no module can be reading a
    projection it never names.
    """
    naming = {module for module, tree in modules().items() if names_the_directory(tree)}
    assert naming == {CONSTANT_MODULE, PERSISTER_MODULE}, sorted(naming)


def test_no_site_reads_a_path_under_the_artifact_directory():
    """The writer makes, writes, asks about and removes; it never reads."""
    trees = modules()
    made = set(calls_on_the_directory(trees[PERSISTER_MODULE])) | set(
        calls_on_the_directory(trees[CONSTANT_MODULE])
    )
    assert made == set(EXEMPT), sorted(made ^ set(EXEMPT))
    assert made.isdisjoint(READS), sorted(made & READS)


def test_every_exemption_carries_the_reason_it_is_one():
    assert all(reason.strip() for reason in EXEMPT.values())
    assert READS.isdisjoint(EXEMPT)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "def satisfied(workspace):\n"
            "    entry = Path(workspace) / ARTIFACT_DIR / 'criteria.json'\n"
            "    return json.loads(entry.read_text())\n",
            id="a-service-reading-the-projected-file",
        ),
        pytest.param(
            "def satisfied(workspace):\n"
            "    return Path(workspace, '.kodezart/criteria.json').read_text()\n",
            id="a-literal-path-under-the-directory",
        ),
        pytest.param(
            "from kodezart.core.constants import ARTIFACT_DIR\n"
            "class ArtifactPersister:\n"
            "    def persist(self, workspace):\n"
            "        artifact_dir = Path(workspace) / ARTIFACT_DIR\n"
            "        return [entry.name for entry in artifact_dir.iterdir()]\n",
            id="a-read-inside-the-writer-itself",
        ),
    ],
)
def test_a_planted_read_is_reported(body):
    tree = ast.parse(body)
    assert names_the_directory(tree)
    assert calls_on_the_directory(tree) & READS


def test_a_planted_write_only_module_is_not_reported():
    """The control's control: writing under the directory stays green."""
    tree = ast.parse(
        "from kodezart.core.constants import ARTIFACT_DIR\n"
        "def persist(workspace, artifacts):\n"
        "    artifact_dir = Path(workspace) / ARTIFACT_DIR\n"
        "    artifact_dir.mkdir(exist_ok=True)\n"
        "    for name, content in artifacts.items():\n"
        "        (artifact_dir / name).write_text(content)\n"
    )
    assert names_the_directory(tree)
    assert not calls_on_the_directory(tree) & READS
