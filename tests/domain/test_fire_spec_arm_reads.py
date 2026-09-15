"""No module renders an arm's text beside the one total formatter.

Every name the scan tracks is read off the code rather than typed here: the
arm types off the partition's own union, the arm fields and the neighbouring
renderer off the total formatter's source, and the places a spec is held --
a reader's return, a parameter, a model field, a state key -- off every
annotation in the package. The scan takes its sources as a map, so a module
that never reaches the tree can be injected as a control.
"""

import ast
import inspect
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import get_args

import kodezart
from kodezart.domain.ticket import format_fire_spec
from kodezart.types.domain import fire_spec as partition
from kodezart.types.domain.fire_spec import FireSpec

SOURCE_ROOT = Path(kodezart.__file__).parent
FORMATTER_SOURCE = Path(inspect.getsourcefile(format_fire_spec) or "")
FORMATTER = FORMATTER_SOURCE.relative_to(SOURCE_ROOT).as_posix()
ARMS = {arm.__name__: arm for arm in get_args(FireSpec)}
SPEC_TYPES = frozenset(
    set(ARMS) | {name for name, value in vars(partition).items() if value is FireSpec},
)


def _arm_text_fields() -> dict[str, object]:
    """Each arm field the one formatter turns into the fire's text, annotated."""
    fields: dict[str, object] = {}
    for node in ast.walk(ast.parse(inspect.getsource(format_fire_spec))):
        if not isinstance(node, ast.MatchClass) or not isinstance(node.cls, ast.Name):
            continue
        arm = ARMS.get(node.cls.id)
        if arm is None:
            continue
        for attr in node.kwd_attrs:
            fields[attr] = arm.model_fields[attr].annotation
    return fields


ARM_FIELDS = _arm_text_fields()
TEXT_FIELDS = frozenset(
    name
    for name, annotation in ARM_FIELDS.items()
    if isinstance(annotation, type) and issubclass(annotation, str)
)
PAYLOAD_FIELDS = frozenset(ARM_FIELDS) - TEXT_FIELDS


def _neighbouring_renderers() -> frozenset[str]:
    """The formatter module's other rendering functions, by defined name."""
    tree = ast.parse(FORMATTER_SOURCE.read_text(encoding="utf-8"))
    return frozenset(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name != format_fire_spec.__name__
        and isinstance(node.returns, ast.Name)
        and node.returns.id == "str"
    )


RENDERERS = _neighbouring_renderers()


def _mentions_spec(annotation: ast.expr | None) -> bool:
    """Whether an annotation names one of the partition's types anywhere."""
    if annotation is None:
        return False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id in SPEC_TYPES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in SPEC_TYPES:
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                quoted = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                continue
            if _mentions_spec(quoted):
                return True
    return False


def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[ast.arg]:
    arguments = node.args
    yield from arguments.posonlyargs
    yield from arguments.args
    yield from arguments.kwonlyargs
    for optional in (arguments.vararg, arguments.kwarg):
        if optional is not None:
            yield optional


def _called(node: ast.Call) -> str | None:
    """The bare, aliased or attribute-qualified name a call reaches."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


class _Module:
    """One module's declarations, read once for every pass over it."""

    def __init__(self, tree: ast.Module) -> None:
        self.functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        self.returns = [
            (function.name, node.value)
            for function in self.functions
            for node in ast.walk(function)
            if isinstance(node, ast.Return) and node.value is not None
        ]
        self.spec_parameters = {
            parameter.arg
            for function in self.functions
            for parameter in _parameters(function)
            if _mentions_spec(parameter.annotation)
        }
        self.annotated_fields = {
            node.target.id
            for owner in ast.walk(tree)
            if isinstance(owner, ast.ClassDef)
            for node in owner.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and _mentions_spec(node.annotation)
        }
        self.renderers = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name in RENDERERS
        }
        self.bindings = list(_bindings(tree))
        self.expressions = list(ast.walk(tree))


def _bindings(tree: ast.Module) -> Iterator[tuple[str, ast.expr]]:
    """Every local name a module binds to an expression, with that value."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    yield target.id, node.value
        if (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and isinstance(node.target, ast.Name)
        ):
            yield node.target.id, node.value
        if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            yield node.target.id, node.value


class _Package:
    """The roots a spec is reachable from, derived over a whole source map."""

    def __init__(self, sources: Mapping[str, str]) -> None:
        self.modules = {
            path: _Module(ast.parse(source)) for path, source in sources.items()
        }
        self.readers = {
            function.name
            for module in self.modules.values()
            for function in module.functions
            if _mentions_spec(function.returns)
        }
        self.fields = {
            field
            for module in self.modules.values()
            for field in module.annotated_fields
        }
        self.payload_readers: set[str] = set()
        while True:
            scopes = {path: self.scope(path) for path in self.modules}
            found = {
                name
                for path, module in self.modules.items()
                for name, value in module.returns
                if scopes[path].is_payload(value)
            }
            if found <= self.payload_readers:
                return
            self.payload_readers |= found

    def scope(self, path: str) -> "_Scope":
        return _Scope(self, self.modules[path])


class _Scope:
    """The names one module binds to a spec or to an arm's payload."""

    def __init__(self, package: _Package, module: _Module) -> None:
        self.package = package
        self.module = module
        self.specs = set(module.spec_parameters)
        self.payloads: set[str] = set()
        while True:
            before = set(self.specs), set(self.payloads)
            for target, value in module.bindings:
                if self.is_spec(value):
                    self.specs.add(target)
                elif self.is_payload(value):
                    self.payloads.add(target)
            if before == (self.specs, self.payloads):
                return

    def is_spec(self, node: ast.expr) -> bool:
        """Whether the expression yields one of the partition's arms."""
        if isinstance(node, ast.Name):
            return node.id in self.specs
        if isinstance(node, ast.Await):
            return self.is_spec(node.value)
        if isinstance(node, ast.IfExp):
            return self.is_spec(node.body) or self.is_spec(node.orelse)
        if isinstance(node, ast.Call):
            return _called(node) in self.package.readers | SPEC_TYPES
        if isinstance(node, ast.Attribute):
            return node.attr in self.package.fields
        if isinstance(node, ast.Subscript):
            return (
                isinstance(node.slice, ast.Constant)
                and node.slice.value in self.package.fields
            )
        return False

    def is_payload(self, node: ast.expr) -> bool:
        """Whether the expression yields an arm field the formatter renders."""
        if isinstance(node, ast.Name):
            return node.id in self.payloads
        if isinstance(node, ast.Await):
            return self.is_payload(node.value)
        if isinstance(node, ast.IfExp):
            return self.is_payload(node.body) or self.is_payload(node.orelse)
        if isinstance(node, ast.Call):
            return _called(node) in self.package.payload_readers
        if isinstance(node, ast.Attribute):
            return node.attr in PAYLOAD_FIELDS and self.is_spec(node.value)
        return False

    def renders(self, node: ast.AST) -> bool:
        """Whether this site turns an arm into text without the formatter."""
        if isinstance(node, ast.Attribute):
            return (
                node.attr in TEXT_FIELDS
                and isinstance(node.ctx, ast.Load)
                and self.is_spec(node.value)
            )
        if isinstance(node, ast.FormattedValue):
            return self.is_payload(node.value)
        if isinstance(node, ast.Call):
            if _called(node) not in self.module.renderers | RENDERERS | {str.__name__}:
                return False
            given = list(node.args) + [keyword.value for keyword in node.keywords]
            return any(self.is_payload(one) or self.is_spec(one) for one in given)
        return False

    def render_sites(self) -> list[ast.AST]:
        """Every site in this module that renders an arm beside the formatter."""
        return [node for node in self.module.expressions if self.renders(node)]


def _offenders(sources: Mapping[str, str]) -> dict[str, int]:
    """The modules beside the formatter that render an arm, by site count."""
    package = _Package(sources)
    counted = {
        path: len(package.scope(path).render_sites())
        for path in sources
        if path != FORMATTER
    }
    return {path: sites for path, sites in counted.items() if sites}


def _package_sources() -> dict[str, str]:
    return {
        path.relative_to(SOURCE_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
    }


PACKAGE = _package_sources()


def _control_sites(source: str) -> int:
    """The render sites a control module adds to the scan over the package."""
    return _offenders({**PACKAGE, "control.py": source}).get("control.py", 0)


def test_no_module_beside_the_formatter_renders_an_arm_itself():
    assert _offenders(PACKAGE) == {}


def test_the_fire_spec_readers_are_actually_reached():
    package = _Package(PACKAGE)
    holders = {path for path in PACKAGE if package.scope(path).specs}
    assert holders


def test_the_partition_is_reached_by_every_kind_of_root():
    package = _Package(PACKAGE)
    held_by_parameter = {
        path for path in PACKAGE if package.modules[path].spec_parameters
    }
    assert package.readers
    assert package.fields
    assert package.payload_readers
    assert held_by_parameter


def test_the_scan_catches_a_spec_held_by_a_parameter():
    control = (
        "from kodezart.types.domain.fire_spec import TrackerSpec\n"
        "\n"
        "def subject_text(spec: TrackerSpec) -> str:\n"
        "    return spec.body\n"
    )
    assert _control_sites(control) == 1


def test_the_scan_catches_a_spec_held_by_a_model_field():
    control = "def subject_text(request):\n    return request.original_spec.body\n"
    assert _control_sites(control) == 1


def test_the_scan_catches_a_spec_held_by_a_state_key_or_a_port_read():
    control = (
        "async def node(state, tracker):\n"
        '    held = state["fire_spec"]\n'
        '    fetched = await tracker.read_fire_spec(issue_key="KOD-1")\n'
        "    return held.body, fetched.body\n"
    )
    assert _control_sites(control) == 2


def test_the_boundary_catches_a_module_rendering_an_arm_itself():
    control = (
        "def node(state):\n"
        "    spec = current_fire_spec(state)\n"
        "    return format_ticket_as_task(spec.ticket), spec.body\n"
    )
    assert _control_sites(control) == 2


def test_the_scan_catches_the_renderer_reached_under_another_name():
    control = (
        "from kodezart.domain import ticket\n"
        "from kodezart.domain.ticket import format_ticket_as_task as render\n"
        "\n"
        "def node(state):\n"
        "    return render(current_ticket(state)), ticket.format_ticket_as_task(\n"
        "        current_ticket(state),\n"
        "    )\n"
    )
    assert _control_sites(control) == 2
