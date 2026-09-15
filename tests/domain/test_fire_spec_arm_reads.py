"""No module renders an arm's text beside the one total formatter."""

import ast
from pathlib import Path

import kodezart

SOURCE_ROOT = Path(kodezart.__file__).parent
FORMATTER = SOURCE_ROOT / "domain" / "ticket.py"
SPEC_READERS = frozenset(
    {"current_fire_spec", "original_fire_spec", "current_ticket"},
)
ARM_TEXT = "body"


def _derived(node: ast.expr, names: set[str]) -> bool:
    """Whether the expression yields a value read out of a fire spec."""
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Call):
        return isinstance(node.func, ast.Name) and node.func.id in SPEC_READERS
    if isinstance(node, ast.Attribute):
        return node.attr in {"ticket", ARM_TEXT} and _derived(node.value, names)
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value == "fire_spec"
    return False


def _spec_bound_names(tree: ast.Module) -> set[str]:
    """The local names a module binds to a value read out of a fire spec."""
    names: set[str] = set()
    for _ in range(len(SPEC_READERS) + 1):
        before = set(names)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and _derived(node.value, names):
                names.update(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )
            if (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and isinstance(node.target, ast.Name)
                and _derived(node.value, names)
            ):
                names.add(node.target.id)
        if names == before:
            break
    return names


def _renders_arm_text(tree: ast.Module) -> list[ast.expr]:
    """Every site that turns a fire spec's arm into text on its own."""
    names = _spec_bound_names(tree)
    sites: list[ast.expr] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "format_ticket_as_task"
            and node.args
            and _derived(node.args[0], names)
        ):
            sites.append(node)
        if (
            isinstance(node, ast.Attribute)
            and node.attr == ARM_TEXT
            and isinstance(node.ctx, ast.Load)
            and _derived(node.value, names)
        ):
            sites.append(node)
    return sites


def _modules() -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if path != FORMATTER
    ]


def test_no_module_beside_the_formatter_renders_an_arm_itself():
    offenders = {
        path.relative_to(SOURCE_ROOT).as_posix()
        for path, tree in _modules()
        if _renders_arm_text(tree)
    }
    assert offenders == set()


def test_the_fire_spec_readers_are_actually_reached():
    readers = {
        path.relative_to(SOURCE_ROOT).as_posix()
        for path, tree in _modules()
        if _spec_bound_names(tree)
    }
    assert readers


def test_the_boundary_catches_a_module_rendering_an_arm_itself():
    rendered = ast.parse(
        "def node(state):\n"
        "    spec = current_fire_spec(state)\n"
        "    return format_ticket_as_task(spec.ticket), spec.body\n",
    )
    assert len(_renders_arm_text(rendered)) == 2
