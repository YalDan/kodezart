"""Production code imports no mock package (KOD-394).

A mock value in shipped code answers every call with another mock, so a
path that reaches it looks as if it worked. The shipped tree is walked,
not listed: every module under ``src`` is parsed, and an import of
``unittest.mock`` or ``mock`` in any spelling, dynamic ones included, is
refused wherever it sits.

A hardcoded fallback or a compatibility shim has no import or signature
to recognise it by, so those stay a reading of the diff, not a guard.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
#: The packages whose values stand in for real ones.
MOCK_PACKAGES = ("unittest.mock", "mock")
#: The calls that import a module named by a string.
DYNAMIC_IMPORTS = frozenset({"__import__", "import_module"})


def _names_mock(name: str) -> bool:
    return any(name == root or name.startswith(f"{root}.") for root in MOCK_PACKAGES)


def _called(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _imported(node: ast.AST) -> Iterator[str]:
    """Every module name one node imports, however it is spelled.

    ``from unittest import mock`` imports ``unittest.mock`` through its
    imported name, and a dynamic import is read from its name argument,
    positional or by keyword.
    """
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name
    elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
        yield node.module
        for alias in node.names:
            yield f"{node.module}.{alias.name}"
    elif isinstance(node, ast.Call) and _called(node) in DYNAMIC_IMPORTS:
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        argument = node.args[0] if node.args else keywords.get("name")
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            yield argument.value


def mock_imports(root: Path) -> tuple[list[Path], list[str]]:
    """Every module under *root*, and every mock import one of them makes."""
    modules = sorted(root.rglob("*.py"))
    found: list[str] = []
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for name in _imported(node):
                if _names_mock(name):
                    lineno = getattr(node, "lineno", 0)
                    found.append(f"{path.relative_to(root)}:{lineno}: {name}")
    return modules, found


def test_no_production_module_imports_a_mock_package() -> None:
    """Nothing under ``src`` imports a mock package, in any spelling."""
    modules, found = mock_imports(SOURCE_ROOT)

    assert found == []
    assert len(modules) >= 369


#: One module that imports a mock package in every spelling the scan
#: claims to read, each on its own line, beside imports of the same
#: standard-library package that are not mocks and a mention in prose.
PLANTED = '''"""Mentions unittest.mock in prose, which imports nothing."""

import importlib
import unittest
from unittest import TestCase

import unittest.mock
from unittest import mock
from unittest.mock import MagicMock
import mock
from mock import patch

PROBE = __import__("unittest.mock").mock.MagicMock()


def later() -> None:
    from unittest.mock import AsyncMock
    importlib.import_module(name="mock")
'''


def test_the_scan_reports_every_planted_mock_import_and_nothing_else(
    tmp_path: Path,
) -> None:
    """The scan's positive control: one that finds nothing looks green."""
    (tmp_path / "package").mkdir()
    (tmp_path / "package" / "planted.py").write_text(PLANTED, encoding="utf-8")
    (tmp_path / "package" / "clean.py").write_text(
        "import unittest\n", encoding="utf-8"
    )

    modules, found = mock_imports(tmp_path)
    lines = PLANTED.splitlines()
    reported = {lines[int(entry.split(":")[1]) - 1].strip() for entry in found}

    assert len(modules) == 2
    assert all(entry.startswith("package/planted.py:") for entry in found)
    assert reported == {
        "import unittest.mock",
        "from unittest import mock",
        "from unittest.mock import MagicMock",
        "import mock",
        "from mock import patch",
        'PROBE = __import__("unittest.mock").mock.MagicMock()',
        "from unittest.mock import AsyncMock",
        'importlib.import_module(name="mock")',
    }
