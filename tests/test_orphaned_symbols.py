"""No public symbol under ``src/kodezart`` may be referenced by nothing.

This lane rediscovered the same defect four times — a capability shipped
with no caller — and closed it four times as a list of instances. This is
the class, held mechanically: a public class, function or method that
appears nowhere in the repository except at its own definition is a
failure of ``make check``, not a finding for the next reader to make
again.

Two rules decide what counts as a reference, and both are stated rather
than tuned:

* **A decorator is a reference.** A route handler, a ``@property`` and a
  validator are all reached by registration rather than by name, and their
  decorator sits on the definition. A decorated definition is therefore
  never orphaned by this check. This is why the check cannot see a route
  handler that no router mounts — a real gap, named here rather than
  papered over, and one ``tests/docs/test_documented_surface.py`` already
  covers from the endpoint side.
* **Any textual mention outside the definition counts**, anywhere in the
  repository — source, tests, documentation, the build file. Deliberately
  generous: the question this asks is "does anything at all reach this?",
  and a name that appears only in its own module's docstring is a case
  this will miss. It is a floor on deadness, not a proof of liveness.

Private names are out of scope. A leading underscore already says the
symbol is local, and an unused one is `ruff`'s job.
"""

import ast
import re
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "kodezart"

#: Directories carrying no authored content — build products, caches and
#: the virtual environment. Reading them would let a stale artifact vouch
#: for a symbol nothing in the repository reaches.
_IGNORED_DIRECTORIES: frozenset[str] = frozenset(
    {".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"},
)

#: Suffixes and bare filenames whose text is read for references. Anything
#: else in the tree is binary or generated.
_TEXT_SUFFIXES: frozenset[str] = frozenset({".py", ".md", ".toml", ".cfg", ".example"})
_TEXT_FILENAMES: frozenset[str] = frozenset(
    {"Makefile", "Dockerfile", "CODEOWNERS", ".env.example"},
)

def _public_definitions() -> dict[str, list[str]]:
    """Every public, undecorated definition under ``src/kodezart``.

    Top-level classes and functions, plus methods on any class. A method
    reached through a protocol still carries its own name at the call
    site, so it is in scope by the same rule as anything else.
    """
    definitions: dict[str, list[str]] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for member in node.body:
                    _record(definitions, member, path)
            elif isinstance(node, ast.Module):
                for member in node.body:
                    _record(definitions, member, path)
    return definitions


def _record(
    definitions: dict[str, list[str]],
    node: ast.stmt,
    path: Path,
) -> None:
    if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
        return
    if node.name.startswith("_") or node.decorator_list:
        return
    site = f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}"
    definitions.setdefault(node.name, []).append(site)


@cache
def _repository_text() -> str:
    """Every authored text file in the repository, concatenated."""
    chunks: list[str] = []
    for path in sorted(REPO_ROOT.rglob("*")):
        if any(part in _IGNORED_DIRECTORIES for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix not in _TEXT_SUFFIXES and path.name not in _TEXT_FILENAMES:
            continue
        chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


def orphans_among(definitions: dict[str, list[str]]) -> list[str]:
    """Every definition in *definitions* the repository mentions nowhere else."""
    blob = _repository_text()
    return sorted(
        f"{name} ({', '.join(sites)})"
        for name, sites in definitions.items()
        if len(re.findall(rf"\b{re.escape(name)}\b", blob)) <= len(sites)
    )


def test_no_public_symbol_is_referenced_only_by_its_own_definition() -> None:
    """A capability nothing reaches is not delivered, and this is the guard."""
    assert orphans_among(_public_definitions()) == []


def test_the_scan_reports_a_symbol_nothing_reaches() -> None:
    """The check above must be able to fail, or it is decoration.

    The same predicate, over the same repository text, with one definition
    injected that nothing anywhere mentions. A counting rule that made
    every symbol look referenced would pass the assertion above silently
    and fail here.
    """
    invented = "AnOrphanNoModuleDefinesOrMentions"
    site = "src/kodezart/nowhere.py:1"
    reached = "FirePrepPass"

    reported = orphans_among({invented: [site], reached: [site]})

    assert reported == [f"{invented} ({site})"]


def test_the_scan_reads_a_meaningful_number_of_definitions() -> None:
    """An empty definition set would make the guard pass over anything."""
    definitions = _public_definitions()

    assert len(definitions) > 100
    assert "FirePrepPass" in definitions
    assert "classify_check_failures" in definitions
