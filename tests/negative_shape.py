"""Static readings of the tree, shared by the negative-shape census.

One walk, one parse per file, many readings: the baseline that compares the
rosters, the lane guard that follows a lane's imports, and anything that
comes after all read the same walk instead of each growing one.

Every token class and every directive form this module knows is held here
as a string literal and named nowhere in code, so the module's own reading
of itself finds nothing.
"""

import ast
import functools
import re
import tokenize
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The two trees the gate covers: shipped code and the suite that exercises it.
SCANNED: Final[tuple[str, ...]] = ("src/kodezart", "tests")

#: The directive comments that take a line out of the gate's reach.
SUPPRESSION: Final[re.Pattern[str]] = re.compile(
    r"#\s*(?:type:\s*ignore|(?:ruff:\s*)?noqa)"
)

#: The pytest forms that keep a collected test from running.
SKIP_FORMS: Final[frozenset[str]] = frozenset(
    {
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.skip",
        "pytest.importorskip",
        "pytest.mark.xfail",
        "pytest.xfail",
    }
)


@dataclass(frozen=True, slots=True)
class Source:
    """One walked module: where it lives, its bytes, and its parsed tree."""

    path: str
    text: bytes
    tree: ast.Module

    @classmethod
    def of(cls, path: str, text: str) -> "Source":
        """A source built from text rather than read, for a control."""
        data = text.encode("utf-8")
        return cls(path=path, text=data, tree=ast.parse(data))


@functools.cache
def sources() -> tuple[Source, ...]:
    """Every module under the scanned trees, sorted per tree, parsed once."""
    walked: list[Source] = []
    for tree in SCANNED:
        for module in sorted((REPO_ROOT / tree).rglob("*.py")):
            text = module.read_bytes()
            walked.append(
                Source(
                    path=module.relative_to(REPO_ROOT).as_posix(),
                    text=text,
                    tree=ast.parse(text),
                )
            )
    return tuple(walked)


@functools.cache
def _walked() -> dict[str, Source]:
    return {walked.path: walked for walked in sources()}


def source(path: str) -> Source:
    """The one source at *path*, or a refusal naming the path."""
    walked = _walked().get(path)
    if walked is None:
        msg = f"the census does not walk {path}"
        raise KeyError(msg)
    return walked


def census[T](read: Callable[[Source], T]) -> dict[str, T]:
    """{path: reading} for every source whose reading is truthy, in walk order."""
    found: dict[str, T] = {}
    for walked in sources():
        reading = read(walked)
        if reading:
            found[walked.path] = reading
    return found


def comment_directives(module: Source) -> tuple[str, ...]:
    """The directive comments the module carries, stripped, in file order.

    Read from the token stream, so the same words inside a string -- the
    prompts that name these tokens, and the tests that assert those prompts
    render -- are not directives and are not counted.
    """
    lines = iter(module.text.splitlines(keepends=True))
    return tuple(
        token.string.strip()
        for token in tokenize.tokenize(lambda: next(lines, b""))
        if token.type is tokenize.COMMENT and SUPPRESSION.search(token.string)
    )


def dotted(node: ast.AST) -> str | None:
    """``a.b.c`` for a name-or-attribute chain; None for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = dotted(node.value)
        return None if head is None else f"{head}.{node.attr}"
    return None


def _through(name: str | None, bindings: Mapping[str, str]) -> str | None:
    """*name* with its head replaced by the origin the module bound it to."""
    if name is None:
        return None
    head, _, rest = name.partition(".")
    origin = bindings.get(head)
    if origin is None:
        return None
    return f"{origin}.{rest}" if rest else origin


def pytest_bindings(tree: ast.Module) -> dict[str, str]:
    """Local name -> the dotted pytest origin the module bound it to.

    ``import pytest as pt`` binds ``pt``; ``from pytest import mark as m``
    binds ``m`` to the mark factory; a module-level ``marks = pytest.mark``
    binds ``marks`` to the same.  Only origins under pytest are kept.
    """
    bindings: dict[str, str] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.name == "pytest" or alias.name.startswith("pytest."):
                    bindings[alias.asname or alias.name] = alias.name
        elif isinstance(statement, ast.ImportFrom) and statement.level == 0:
            origin = statement.module or ""
            if origin == "pytest" or origin.startswith("pytest."):
                for alias in statement.names:
                    bindings[alias.asname or alias.name] = f"{origin}.{alias.name}"

    # An assignment alias can be written above the alias it copies, so the
    # finite set is resolved before any chain is read.
    changed = True
    while changed:
        changed = False
        for statement in tree.body:
            if not isinstance(statement, ast.Assign):
                continue
            origin = _through(dotted(statement.value), bindings)
            if origin is None:
                continue
            for target in statement.targets:
                if isinstance(target, ast.Name) and bindings.get(target.id) != origin:
                    bindings[target.id] = origin
                    changed = True
    return bindings


def sites(module: Source, forms: frozenset[str]) -> tuple[str, ...]:
    """The forms the module names in code, in file order.

    Each maximal name-or-attribute chain is resolved through the module's
    own pytest bindings, so an aliased import names the same form.  A string
    constant is never a site.  Not followed, and so not seen: a form reached
    through ``getattr``, a marker added from a string at collection time, an
    alias bound inside a function, a module reached through ``importlib``.
    """
    bindings = pytest_bindings(module.tree)
    inner = {
        id(node.value)
        for node in ast.walk(module.tree)
        if isinstance(node, ast.Attribute)
    }
    found: list[tuple[int, int, str]] = []
    for node in ast.walk(module.tree):
        if not isinstance(node, ast.Name | ast.Attribute) or id(node) in inner:
            continue
        # The name an alias is bound to is not itself a use of the form.
        if not isinstance(node.ctx, ast.Load):
            continue
        name = _through(dotted(node), bindings)
        if name is not None and name in forms:
            found.append((node.lineno, node.col_offset, name))
    return tuple(name for _, _, name in sorted(found))


def test_declarations(module: Source) -> tuple[str, ...]:
    """The qualified name of every test declaration, in file order."""

    def declared(body: Sequence[ast.stmt], prefix: str) -> Iterator[str]:
        for statement in body:
            if isinstance(statement, ast.ClassDef):
                yield from declared(statement.body, f"{prefix}{statement.name}.")
            elif isinstance(
                statement, ast.FunctionDef | ast.AsyncFunctionDef
            ) and statement.name.startswith("test_"):
                yield f"{prefix}{statement.name}"

    return tuple(declared(module.tree.body, ""))


def assert_count(module: Source) -> int:
    """How many assertions the module makes; one wrapped over three lines is one."""
    return sum(1 for node in ast.walk(module.tree) if isinstance(node, ast.Assert))


def vanished(
    recorded: Mapping[str, Sequence[str]],
    current: Mapping[str, Sequence[str]],
) -> list[str]:
    """Every recorded declaration gone, and every file declaring one with no row."""
    findings: list[str] = []
    for path, names in recorded.items():
        lost = Counter(names) - Counter(current.get(path, ()))
        findings.extend(f"{path}::{name}" for name in lost.elements())
    findings.extend(f"{path} has no row" for path in current if path not in recorded)
    return sorted(findings)


def shortfalls(recorded: Mapping[str, int], current: Mapping[str, int]) -> list[str]:
    """Every file below its recorded floor, and every file asserting with no row."""
    findings = [
        f"{path}: {current.get(path, 0)} below {floor}"
        for path, floor in recorded.items()
        if current.get(path, 0) < floor
    ]
    findings.extend(f"{path} has no row" for path in current if path not in recorded)
    return sorted(findings)


def snapshot() -> dict[str, dict[str, object]]:
    """The two open rosters, in the shape the baseline file records.

    A reader, called by hand when a commit adds files to the walk.  Nothing
    in the tree writes the file: what it records is a decision made in a diff.
    """
    return {
        "declarations": {
            path: list(names) for path, names in census(test_declarations).items()
        },
        "asserts": dict(census(assert_count)),
    }


def _under_tests(name: str) -> bool:
    return name == "tests" or name.startswith("tests.")


def test_imports(module: Source) -> tuple[str, ...]:
    """Every suite module this one imports, wherever the import sits.

    An import written inside a function body is read exactly as one written
    at the top.  A relative import is not followed and the suite writes none.
    """
    found: list[str] = []
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if _under_tests(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            origin = node.module or ""
            if not _under_tests(origin):
                continue
            found.append(origin)
            for alias in node.names:
                member = f"{origin}.{alias.name}"
                if module_path(member) is not None:
                    found.append(member)
    return tuple(dict.fromkeys(found))


def module_path(name: str) -> str | None:
    """The repository-relative file a dotted suite module names, or None."""
    stem = name.replace(".", "/")
    for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
        if (REPO_ROOT / candidate).is_file():
            return candidate
    return None


def reach(roots: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """{path: its suite imports} for the roots and everything they import.

    Bounded by the finite set of walked sources: a visited set stops a cycle
    and every path is read once.
    """
    found: dict[str, tuple[str, ...]] = {}
    pending = list(roots)
    while pending:
        path = pending.pop()
        if path in found:
            continue
        imports = test_imports(source(path))
        found[path] = imports
        for name in imports:
            resolved = module_path(name)
            if resolved is not None and resolved not in found:
                pending.append(resolved)
    return found


def frozen(value: object) -> object:
    """Lists to tuples, recursively, so a parsed table compares with a literal."""
    if isinstance(value, list):
        return tuple(frozen(item) for item in value)
    if isinstance(value, dict):
        return {key: frozen(item) for key, item in value.items()}
    return value


def config_tables(pyproject: Path, keys: Sequence[str]) -> dict[str, object]:
    """The named dotted subtrees of the project file, with lists frozen."""
    with pyproject.open("rb") as handle:
        parsed = tomllib.load(handle)
    tables: dict[str, object] = {}
    for key in keys:
        table = parsed
        for step in key.split("."):
            table = table[step]
        tables[key] = frozen(table)
    return tables


def declared_markers(pyproject: Path) -> frozenset[str]:
    """The marker names the project file declares, each read before its colon."""
    key = "tool.pytest.ini_options.markers"
    declared: Sequence[str] = config_tables(pyproject, (key,))[key]
    return frozenset(entry.split(":", 1)[0].strip() for entry in declared)
