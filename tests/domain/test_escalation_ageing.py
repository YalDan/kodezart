"""The run-shape signal modules are arithmetic over the readings they are handed."""

import ast
import inspect
from pathlib import Path

import pytest

import kodezart.domain
from kodezart.domain import mandate_graph, run_alarm_record, run_shape

#: Every domain module that names an alarm signal, with the exact imports it
#: may make. The set is not chosen by hand: the test below derives it from
#: the package and requires this table to cover exactly that.
SIGNAL_MODULES = [
    (
        run_shape,
        {
            "typing",
            "pydantic",
            "kodezart.domain.errors",
            "kodezart.domain.run_alarm_record",
            "kodezart.types.domain.escalation",
            "kodezart.types.domain.run_alarm",
            "kodezart.types.domain.run_state",
            "kodezart.types.domain.surface",
            "kodezart.types.domain.scope",
            "kodezart.types.domain.organize",
        },
    ),
    # The two landed folds over a lane's decisions and its milestone graph:
    # the same kind of module, held to the same rules.
    (
        mandate_graph,
        {
            "collections.abc",
            "kodezart.domain.run_shape",
            "kodezart.types.domain.agent",
            "kodezart.types.domain.mandate_graph",
            "kodezart.types.domain.run_alarm",
            "kodezart.types.domain.scope",
            "kodezart.types.domain.tracker",
        },
    ),
    # The record's address and its one-listing read: it names every signal
    # because an address is composed from one, and it holds no count at all.
    (
        run_alarm_record,
        {
            "collections.abc",
            "pydantic",
            "kodezart.domain.comment_markers",
            "kodezart.domain.errors",
            "kodezart.domain.run_event_stream",
            "kodezart.types.domain.run_alarm",
            "kodezart.types.domain.run_event",
            "kodezart.types.domain.scope",
            "kodezart.types.domain.surface",
            "kodezart.types.domain.tracker",
        },
    ),
]


@pytest.mark.parametrize(("module", "allowed_imports"), SIGNAL_MODULES)
def test_signal_module_is_pure_and_count_comparisons_have_no_literal_bound(
    module, allowed_imports
):
    tree = ast.parse(inspect.getsource(module))
    imports = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert imports <= allowed_imports
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    forbidden_calls = {
        "open",
        "input",
        "exec",
        "eval",
        "__import__",
        "print",
        "breakpoint",
    }
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in forbidden_calls
        for node in ast.walk(tree)
    )
    # Arithmetic over values it was handed has nothing to await. With the
    # import allow-list an await cannot by itself reach I/O, but "performs no
    # I/O" is held directly by there being nothing here that could wait.
    assert not any(
        isinstance(node, (ast.Await, ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith))
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(child, ast.Constant) and isinstance(child.value, (int, float))
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        for child in ast.walk(node)
    )


def _names_an_alarm_signal(source: str) -> bool:
    """Whether *source* names the signal vocabulary in any spelling.

    An import of the name, a bare use of it, and an attribute read of it off
    a module imported whole (``run_alarm.AlarmSignal``) all count.
    """
    tree = ast.parse(source)
    return any(
        (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "AlarmSignal" for alias in node.names)
        )
        or (isinstance(node, ast.Name) and node.id == "AlarmSignal")
        or (isinstance(node, ast.Attribute) and node.attr == "AlarmSignal")
        for node in ast.walk(tree)
    )


def _modules_naming_an_alarm_signal(root: Path, package: str) -> set[str]:
    """Every module under *root*, at any depth, that names the vocabulary."""
    found = set()
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if _names_an_alarm_signal(path.read_text(encoding="utf-8")):
            found.add(".".join((package, *parts)))
    return found


def test_every_domain_module_naming_an_alarm_signal_is_in_the_purity_scan():
    """The scanned surface is derived from the package, never listed by hand.

    A new signal module that names the vocabulary and is missing from the
    table above would otherwise carry none of its protection: no import
    allow-list, no await, no literal bound. Derived from every module under
    the package, subpackages included, so a module added anywhere under it is
    found.
    """
    derived = _modules_naming_an_alarm_signal(
        Path(kodezart.domain.__path__[0]), kodezart.domain.__name__
    )
    scanned = {module.__name__ for module, _ in SIGNAL_MODULES}

    # Not vacuous: the fold modules are found by the derivation itself.
    assert {run_shape.__name__, mandate_graph.__name__} <= derived
    assert scanned == derived


def test_the_derivation_finds_a_subpackage_module_reading_the_vocabulary_by_attribute(
    tmp_path,
):
    """The derivation's own positive control, on a tree written for it.

    The module sits in a subpackage and never imports the name: it imports
    the vocabulary's module whole and reads the name off it.
    """
    nested = tmp_path / "folds" / "deeper"
    nested.mkdir(parents=True)
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "folds" / "__init__.py").write_text("", encoding="utf-8")
    (nested / "__init__.py").write_text("", encoding="utf-8")
    (nested / "quiet.py").write_text("VALUE = 1\n", encoding="utf-8")
    (nested / "attribute_fold.py").write_text(
        "from kodezart.types.domain import run_alarm\n"
        "\n"
        "SIGNAL = run_alarm.AlarmSignal.TALLY_UNMOVED\n",
        encoding="utf-8",
    )

    assert _modules_naming_an_alarm_signal(tmp_path, "synthetic") == {
        "synthetic.folds.deeper.attribute_fold"
    }
