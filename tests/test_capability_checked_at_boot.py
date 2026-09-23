"""Capability and totality are asked once, at boot, and never branched on.

The scanned surface is derived: every module under the packaged source tree,
walked from the package's own path, and every field of the two configuration
models, walked recursively. What each scan matches, and nothing more:

- a call of the port's probe method, by its name;
- a call of either boot check, by its name;
- an ``except`` clause naming either refusal type, alone or in a tuple (a
  broad handler such as ``except Exception`` is not scanned);
- a membership test against the fold table, a ``.get`` on it, and a read of a
  row's ``scans``, which only the two boot checks may make (a subscript read
  of a row is allowed, because a stored record is replayed through it);
- a configuration field whose annotation names the signal vocabulary's type;
- a configuration field whose name contains ``alarm`` and whose annotation is
  anything but a number.

Each has a synthetic control proving it finds what it looks for.
"""

import ast
import types
import typing
from collections.abc import Iterator, Mapping
from pathlib import Path

from pydantic import BaseModel

import kodezart
from kodezart.config.app import AppConfig
from kodezart.core.errors import PassGateCapabilityError
from kodezart.domain.run_alarm_table import AlarmTableError, require_alarm_table
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_alarm import AlarmSignal

PACKAGE_ROOT = Path(kodezart.__path__[0])
#: The one function allowed to ask the credential what it can scan.
PROBE = "verify_scan_capability"
PROBE_SITE = ("composition/passes.py", "_verify_wired_gates")
#: The boot checks, and the one function allowed to call each of them.
BOOT_CHECKS = ("_verify_wired_gates", require_alarm_table.__name__)
BOOT_SITE = ("composition/passes.py", "verify_pass_preflight")
#: The two functions allowed to ask whether the fold table has a row or what
#: a row scans: the totality check and the probe that inverts the column.
TABLE_QUESTION_SITES = frozenset(
    {
        ("domain/run_alarm_table.py", require_alarm_table.__name__),
        PROBE_SITE,
    }
)
TABLE = "ALARM_TABLE"
REFUSALS = frozenset({PassGateCapabilityError.__name__, AlarmTableError.__name__})


def packaged_sources() -> dict[str, str]:
    """Every module of the package, by path relative to the package root."""
    return {
        path.relative_to(PACKAGE_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
    }


def _functions(tree: ast.Module) -> Iterator[tuple[str, ast.AST]]:
    """Every function in *tree* with its innermost enclosing function's name."""

    def walk(node: ast.AST, owner: str) -> Iterator[tuple[str, ast.AST]]:
        for child in ast.iter_child_nodes(node):
            name = owner
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            yield name, child
            yield from walk(child, name)

    yield from walk(tree, "<module>")


def call_sites(sources: Mapping[str, str], name: str) -> set[tuple[str, str]]:
    """Every ``(path, function)`` that calls *name*, as a name or an attribute."""
    sites: set[tuple[str, str]] = set()
    for path, text in sources.items():
        for owner, node in _functions(ast.parse(text)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else None
            )
            if called == name:
                sites.add((path, owner))
    return sites


def _named(node: ast.expr | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Tuple):
        return {name for element in node.elts for name in _named(element)}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Name):
        return {node.id}
    return set()


def handlers_of(sources: Mapping[str, str], names: frozenset[str]) -> list[str]:
    """Every except clause naming one of *names*, alone or in a tuple."""
    return sorted(
        f"{path}:{node.lineno}"
        for path, text in sources.items()
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.ExceptHandler) and _named(node.type) & names
    )


def _names_table(node: ast.expr) -> bool:
    return (isinstance(node, ast.Name) and node.id == TABLE) or (
        isinstance(node, ast.Attribute) and node.attr == TABLE
    )


def table_questions(sources: Mapping[str, str]) -> set[tuple[str, str]]:
    """Every ``(path, function)`` asking whether the fold table has a row.

    A membership test against the table, a ``.get`` on it, and a read of any
    row's ``scans``. A subscript read of a row is not a question: it assumes
    the row, which the boot has already guaranteed.
    """
    sites: set[tuple[str, str]] = set()
    for path, text in sources.items():
        for owner, node in _functions(ast.parse(text)):
            asked = (
                (
                    isinstance(node, ast.Compare)
                    and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
                    and any(
                        _names_table(side) for side in (node.left, *node.comparators)
                    )
                )
                or (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and _names_table(node.func.value)
                )
                or (isinstance(node, ast.Attribute) and node.attr == "scans")
            )
            if asked:
                sites.add((path, owner))
    return sites


def _numeric(annotation: object) -> bool:
    if typing.get_origin(annotation) is typing.Annotated:
        annotation = typing.get_args(annotation)[0]
    return annotation in (int, float)


def alarm_knobs(
    model: type[BaseModel], ancestors: frozenset[type] = frozenset()
) -> list[str]:
    """Every field, through nested models, named for alarms and not a number.

    A number is a bound; a string or a collection under an alarm's name is a
    way to spell which alarms to leave out. Each path to a nested model is
    walked, and only a model already on the current path is not re-entered.
    """
    path = ancestors | {model}
    found: list[str] = []
    for name, field in model.model_fields.items():
        if "alarm" in name and not _numeric(field.annotation):
            found.append(name)
        for nested in _models_in(field.annotation):
            if nested not in path:
                found.extend(f"{name}.{inner}" for inner in alarm_knobs(nested, path))
    return sorted(found)


def _models_in(annotation: object) -> Iterator[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for arg in typing.get_args(annotation):
        yield from _models_in(arg)


def _mentions(annotation: object, target: type, seen: set[type]) -> bool:
    """Whether *annotation* names *target*, through containers and models."""
    if annotation is target:
        return True
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return False
        seen.add(annotation)
        return any(
            _mentions(field.annotation, target, seen)
            for field in annotation.model_fields.values()
        )
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Annotated:
        args = args[:1]
    if isinstance(annotation, types.UnionType) or origin is not None:
        return any(_mentions(arg, target, seen) for arg in args)
    return False


def fields_naming(model: type[BaseModel], target: type) -> list[str]:
    """Every field of *model*, walked through nested models, naming *target*."""
    return sorted(
        name
        for name, field in model.model_fields.items()
        if _mentions(field.annotation, target, {model})
    )


# ---------------------------------------------------------------------------
# The tree.
# ---------------------------------------------------------------------------


def test_the_credential_is_asked_what_it_can_scan_in_one_place():
    assert call_sites(packaged_sources(), PROBE) == {PROBE_SITE}


def test_the_boot_checks_are_made_by_the_preflight_alone():
    sources = packaged_sources()
    for check in BOOT_CHECKS:
        assert call_sites(sources, check) == {BOOT_SITE}, check


def test_no_code_catches_a_boot_refusal_and_carries_on():
    """A caught refusal is a degraded supervisor: the tick would run blind.

    Matched by the two refusal types' names, alone or in a tuple. A broad
    handler is not scanned here.
    """
    assert handlers_of(packaged_sources(), REFUSALS) == []


def test_no_configuration_field_can_name_an_alarm_signal():
    """No knob can switch a signal off, because no knob can name one."""
    assert fields_naming(AppConfig, AlarmSignal) == []
    assert fields_naming(OperationConfig, AlarmSignal) == []


def test_no_alarm_knob_of_any_spelling_is_anything_but_a_bound():
    """A signal named by string is still a signal named: only numbers pass."""
    assert alarm_knobs(AppConfig) == []
    assert alarm_knobs(OperationConfig) == []


def test_only_the_boot_checks_ask_whether_the_fold_table_has_a_row():
    """No runtime branch on a fold's presence or on what a row scans."""
    assert table_questions(packaged_sources()) == TABLE_QUESTION_SITES


# ---------------------------------------------------------------------------
# The controls: each scan finds what it looks for.
# ---------------------------------------------------------------------------

CONTROL = {
    "composition/passes.py": (
        "async def _verify_wired_gates(tracker):\n"
        "    await tracker.verify_scan_capability(signals=[])\n"
        "async def verify_pass_preflight():\n"
        "    require_alarm_table()\n"
    ),
    "services/tick.py": (
        "async def observe(tracker):\n"
        "    try:\n"
        "        await tracker.verify_scan_capability(signals=[])\n"
        "    except (ValueError, errors.PassGateCapabilityError):\n"
        "        return\n"
        "    try:\n"
        "        require_alarm_table()\n"
        "    except AlarmTableError:\n"
        "        pass\n"
    ),
}


def test_a_probe_outside_the_boot_check_is_found():
    assert call_sites(CONTROL, PROBE) == {PROBE_SITE, ("services/tick.py", "observe")}
    assert call_sites(CONTROL, require_alarm_table.__name__) == {
        BOOT_SITE,
        ("services/tick.py", "observe"),
    }


def test_a_caught_refusal_is_found_alone_and_in_a_tuple():
    assert handlers_of(CONTROL, REFUSALS) == [
        "services/tick.py:4",
        "services/tick.py:8",
    ]


def test_a_question_to_the_fold_table_is_found_in_every_spelling():
    sources = {
        **CONTROL,
        "domain/run_alarm_table.py": (
            "def require_alarm_table():\n"
            "    return [s for s in AlarmSignal if s not in ALARM_TABLE]\n"
            "def alarm_raised(record):\n"
            "    if record.signal not in ALARM_TABLE:\n"
            "        return False\n"
            "    return ALARM_TABLE[record.signal].fold(record)\n"
        ),
        "services/tick.py": (
            "def observe(signal):\n"
            "    return run_alarm_table.ALARM_TABLE.get(signal)\n"
            "def scanned(signal):\n"
            "    return tables.ALARM_TABLE[signal].scans\n"
            "def replayed(signal, record):\n"
            "    return ALARM_TABLE[signal].fold(record)\n"
        ),
    }

    assert table_questions(sources) == {
        ("domain/run_alarm_table.py", "require_alarm_table"),
        ("domain/run_alarm_table.py", "alarm_raised"),
        ("services/tick.py", "observe"),
        ("services/tick.py", "scanned"),
    }


def test_an_alarm_knob_that_is_not_a_number_is_found_through_nesting():
    class Inner(BaseModel):
        disabled_alarm_signals: frozenset[str] = frozenset()
        run_alarm_bound: int = 1

    class Knobs(BaseModel):
        run_alarm_max_ticks: int = 1
        alarm_mode: str = ""
        nested: Inner = Inner()
        listed: list[Inner] = []

    assert alarm_knobs(Knobs) == [
        "alarm_mode",
        "listed.disabled_alarm_signals",
        "nested.disabled_alarm_signals",
    ]


def test_a_field_naming_a_signal_is_found_through_nesting():
    class Inner(BaseModel):
        disabled: tuple[AlarmSignal, ...] = ()

    class Knobs(BaseModel):
        plain: int = 0
        direct: AlarmSignal | None = None
        nested: Inner = Inner()
        mapped: dict[str, list[AlarmSignal]] = {}

    assert fields_naming(Knobs, AlarmSignal) == ["direct", "mapped", "nested"]
