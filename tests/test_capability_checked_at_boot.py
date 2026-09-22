"""Capability and totality are asked once, at boot, and never branched on.

The scanned surface is derived: every module under the packaged source tree,
walked from the package's own path, and every field of the two configuration
models, walked recursively. Each scan keys on something that cannot be
respelled — the port method's name, the two refusal types, and the signal
vocabulary's own type — and each has a synthetic control proving it finds
what it looks for.
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
    """A caught refusal is a degraded supervisor: the tick would run blind."""
    assert handlers_of(packaged_sources(), REFUSALS) == []


def test_no_configuration_field_can_name_an_alarm_signal():
    """No knob can switch a signal off, because no knob can name one."""
    assert fields_naming(AppConfig, AlarmSignal) == []
    assert fields_naming(OperationConfig, AlarmSignal) == []


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


def test_a_field_naming_a_signal_is_found_through_nesting():
    class Inner(BaseModel):
        disabled: tuple[AlarmSignal, ...] = ()

    class Knobs(BaseModel):
        plain: int = 0
        direct: AlarmSignal | None = None
        nested: Inner = Inner()
        mapped: dict[str, list[AlarmSignal]] = {}

    assert fields_naming(Knobs, AlarmSignal) == ["direct", "mapped", "nested"]
