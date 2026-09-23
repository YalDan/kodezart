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

from tests.conftest import GATED_MARKERS

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The two trees the census walks: shipped code and the suite that exercises it.
SCANNED: Final[tuple[str, ...]] = ("src/kodezart", "tests")

#: Both suffixes the gate's tools read.  The linter lints a stub under the
#: same table as a module and honours a directive comment in it, and the
#: type checker reads a stub sitting beside a module in place of that
#: module and honours the stub's own inline setting, so a directive in a
#: stub is a suppression and a walk over one suffix counts none of them.
SOURCE_SUFFIXES: Final[tuple[str, ...]] = ("*.py", "*.pyi")

#: The directive comments that take a line, or a whole file, out of the
#: gate's reach.  The file-level forms are the cheapest hole of all: one
#: comment at the top of a module and the tool reads none of it.  Every
#: family below was verified against the binary the gate itself runs.  The
#: linter honours its own whole-file exemption, the one it inherited from
#: the linter it replaced, and the import-sorter exemptions, so all three
#: are read; it honours a sorter exemption spelled bare and spelled under
#: its own prefix, so the prefix is optional there as it is on the
#: exemption beside it.  The formatter the gate runs beside it honours its
#: own whole-region pair and its per-statement form, and it honours the
#: whole-region pair of the formatter it replaced, so all of those are one
#: family here.  Any inline setting of the type checker is a per-module
#: configuration change, the same class its own table in the project file
#: pins, so the prefix alone is what is read: a match on prose would cost
#: one row of the allowed map, which is the safe direction.  No form for a
#: checker the gate does not run.
#:
#: The tools match these words without regard to case, so the pattern does
#: too.  A spelling read here that no tool honours costs one row of that
#: map; a spelling a tool honours and the pattern does not read is a hole,
#: so the whole pattern is case-blind rather than one family of it.
SUPPRESSION: Final[re.Pattern[str]] = re.compile(
    r"#\s*(?:type:\s*ignore"
    r"|(?:ruff:\s*|flake8:\s*)?noqa"
    r"|mypy:"
    r"|(?:ruff:\s*)?isort:\s*(?:skip_file|skip|off)"
    r"|ruff:\s*(?:disable|enable)\b"
    r"|fmt:\s*(?:off|on|skip)"
    r"|yapf:\s*(?:disable|enable))",
    re.IGNORECASE,
)

#: The forms that keep a collected test from running: the runner's own, and
#: the standard library's.  Four of the standard library's -- the
#: unconditional skip, the two conditional ones and the raised exception --
#: the runner honours on a plain test function as well as on a case class, so
#: each of those is a collected test that does not run.  The fifth, the
#: expected failure, the runner honours on a case class only, and it is
#: rostered for a reason of its own: a test carrying it is a test whose
#: failure does not count, which is the same silence reached by another route.
SKIP_FORMS: Final[frozenset[str]] = frozenset(
    {
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.skip",
        "pytest.importorskip",
        "pytest.mark.xfail",
        "pytest.xfail",
        "unittest.skip",
        "unittest.skipIf",
        "unittest.skipUnless",
        "unittest.SkipTest",
        "unittest.expectedFailure",
    }
)

#: The modules the form rosters name members of.  A chain resolves to a form
#: only through a binding whose origin is one of these or a module under
#: one, so the roster and the resolver cannot drift: a form added under a
#: root that is not here would resolve nowhere and its control would red.
FORM_ROOTS: Final[tuple[str, ...]] = ("pytest", "unittest")


def gated_mark_forms() -> frozenset[str]:
    """The mark forms the collection gate turns into a skip.

    Derived from the gate's own table, so a marker class added there is
    rostered by the census without a second list being kept in step.
    """
    return frozenset(f"pytest.mark.{name}" for name in GATED_MARKERS)


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


def walk(root: Path) -> tuple[Source, ...]:
    """Every module and stub under *root*'s scanned trees, sorted per tree.

    Both suffixes are read because both tools read both, and a stub carries
    a directive exactly as a module does.  *root* is a parameter so a
    control can walk a tree it built rather than the repository.
    """
    walked: list[Source] = []
    for tree in SCANNED:
        found = sorted(
            path for suffix in SOURCE_SUFFIXES for path in (root / tree).rglob(suffix)
        )
        for module in found:
            text = module.read_bytes()
            walked.append(
                Source(
                    path=module.relative_to(root).as_posix(),
                    text=text,
                    tree=ast.parse(text),
                )
            )
    return tuple(walked)


@functools.cache
def sources() -> tuple[Source, ...]:
    """The repository's own walk, parsed once and shared by every reading."""
    return walk(REPO_ROOT)


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


def _rooted(origin: str) -> bool:
    """True when *origin* is a roster root or a module under one."""
    return any(origin == root or origin.startswith(f"{root}.") for root in FORM_ROOTS)


def _in_source_order(tree: ast.Module) -> list[ast.stmt]:
    """Every statement of the module, nested ones included, in source order.

    The walk the parser's own helper does is breadth-first: it yields every
    module-body statement before any statement nested in a ``try``, an
    ``if`` or a function.  Reading bindings in that order reads an alias
    assignment before the wrapped import it copies from, which is a shape the
    alias arms exist for, so the statements are sorted by where they are
    written instead.  Bounded by the parse: the module has finitely many
    statements and each is visited once.
    """
    return sorted(
        (node for node in ast.walk(tree) if isinstance(node, ast.stmt)),
        key=lambda node: (node.lineno, node.col_offset),
    )


def form_bindings(tree: ast.Module) -> dict[str, str]:
    """Local name -> the dotted origin the module bound it to.

    ``import pytest as pt`` binds ``pt``; ``from pytest import mark as m``
    binds ``m`` to the mark factory; a module-level ``marks = pytest.mark``
    binds ``marks`` to the same, and an annotated ``marks: Final =
    pytest.mark`` binds the same way.  Only origins under a roster root are
    kept, so the standard library's own skip forms resolve the same way and
    an import of a package under a root binds nothing else.

    An import is read wherever it sits, and it binds the name for every use
    in the module wherever that use sits.  One written inside the function
    that calls the form binds the name for that call, and for a use spelled
    above it as well; one wrapped in a ``try`` or an ``if`` at module level
    binds it too.  A module that imports the root nowhere else would
    otherwise resolve none of its own calls.  An alias assignment is read at
    module level only, which is the blind spot the resolver states.

    One forward pass in source order is the whole semantics: a binding is
    read before anything written below it, so the alias an assignment copies
    is already bound when that assignment is reached however the import it
    came from was wrapped, and a name bound again replaces the binding it
    had only when the rebinding is to a rooted origin -- the ``None`` an
    except arm assigns is not one, so the import above it stands.  Source
    order, not scope: a name bound at module level and bound again lower down
    inside a function is read everywhere as the lower binding, so a module
    that spells one form at the top and rebinds the same name to another
    below reports only the second.  A star import binds nothing, and the
    roster states that.
    """
    bindings: dict[str, str] = {}
    outer = {id(statement) for statement in tree.body}
    for statement in _in_source_order(tree):
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if _rooted(alias.name):
                    bindings[alias.asname or alias.name] = alias.name
        elif isinstance(statement, ast.ImportFrom) and statement.level == 0:
            origin = statement.module or ""
            if _rooted(origin):
                for alias in statement.names:
                    if alias.name == "*":
                        continue
                    bindings[alias.asname or alias.name] = f"{origin}.{alias.name}"
        elif isinstance(statement, ast.Assign) and id(statement) in outer:
            copied = _through(dotted(statement.value), bindings)
            if copied is not None:
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        bindings[target.id] = copied
        elif (
            isinstance(statement, ast.AnnAssign)
            and id(statement) in outer
            and statement.value is not None
            and isinstance(statement.target, ast.Name)
        ):
            copied = _through(dotted(statement.value), bindings)
            if copied is not None:
                bindings[statement.target.id] = copied
    return bindings


def sites(module: Source, forms: frozenset[str]) -> tuple[str, ...]:
    """The forms the module names in code, in file order.

    Every name-or-attribute chain is resolved through the module's own
    bindings, so an aliased import names the same form.  A chain a longer one
    is built from is read in its own right, so a form is a site whatever
    continues it: the mark factory's own combinator takes a decorator form
    and returns another, and the longer chain that spells it is not in the
    roster while the form it starts with is.  No form is a chain another
    form continues, so nothing is counted twice: two forms that share a
    spelling up to a letter, as the conditional skips do, are still two
    separate chains.  A string constant is never a site.
    Not followed, and so not seen: a form reached through ``getattr``, a
    marker added from a string at collection time, an alias bound inside a
    function, a module reached through ``importlib``, a star import from
    either root, a root reached only through a dotted import of a package
    under it -- importing the mock package alone binds the dotted string and
    not the root it sits under, so a skip decorator spelled on that root
    after such an import alone resolves to nothing -- and an import from the
    private ``_pytest`` packages.
    """
    bindings = form_bindings(module.tree)
    found: list[tuple[int, int, str]] = []
    for node in ast.walk(module.tree):
        if not isinstance(node, ast.Name | ast.Attribute):
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


def foreign_configuration(
    project: Path, walked: Iterable[str], names: Sequence[str]
) -> list[str]:
    """Every tool configuration file the tree carries besides *project*, by path.

    The linter reads the configuration file closest to each file it checks
    and inherits nothing from the one above, so a file with one of these
    names below a walked directory turns that subtree loose while the pinned
    table goes on reading exactly as it did.  The directories searched are
    derived from the walk -- every ancestor of every walked module, up to and
    including the root the project file sits in -- rather than listed, so a
    tree added to the walk is searched with it.  Bounded by the walk: each
    walked path has finitely many ancestors and each directory is read once.
    *project* itself is the pinned file and is never a hit; a second copy of
    it under a walked tree is one.
    """
    root = project.parent
    directories = {root}
    for path in walked:
        directories.update(root / parent for parent in Path(path).parents)
    found: list[str] = []
    for directory in sorted(directories):
        for name in names:
            candidate = directory / name
            if candidate != project and candidate.is_file():
                found.append(candidate.relative_to(root).as_posix())
    return sorted(found)


def declared_markers(pyproject: Path) -> frozenset[str]:
    """The marker names the project file declares, each read before its colon."""
    key = "tool.pytest.ini_options.markers"
    declared: Sequence[str] = config_tables(pyproject, (key,))[key]
    return frozenset(entry.split(":", 1)[0].strip() for entry in declared)
