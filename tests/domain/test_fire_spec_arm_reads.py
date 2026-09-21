"""No module reads an arm's text beside the one total formatter (KOD-410).

Every name the scan tracks is read off the code rather than typed here: the
arm types off the partition's own union, the arm fields and the neighbouring
renderer off the total formatter's source, the digest function's name and its
home module off the function itself, and the places a spec is held — a
reader's return, a model field, a state key, an annotated parameter — off
every annotation in the package.  The scan takes its sources as a map, so a
module that never reaches the tree can be injected as a control.

A spec held by an *unannotated* parameter is found too, by following the call
that hands it over: a helper written beside a consumer is scanned as the
consumer.  The call edge is re-walked over the grown scopes to a fixed point,
so a spec handed on through a chain of unannotated helpers is followed to the
helper that finally reads it.

The one thing a module may do with an arm's text besides hand it to the
formatter is hand it to the one digest function, which hashes the bytes and
renders nothing.  Those positions are counted apart and pinned exactly.

Stated blind spots: scopes are module-wide, so a word bound to a spec
anywhere in a module is a spec wherever that module reads it; a tuple-unpack
target and a starred argument bind nothing; a spec handed to a lambda's
parameter — inline, bound by an assignment, or handed as a sort key — or bound
into a ``functools.partial`` lands on no parameter, because only a ``def`` is
a definition to the walk that follows a handed value; and a call reached
through a receiver that spells no module is resolved to every method of that
name, which over-includes on the red side.

A reflective read is not a text read to this walk: ``getattr(spec, "body")``,
``spec.model_dump()["body"]`` and ``repr(spec)`` report nothing, because the
field is never spelled as an attribute of a spec.  ``str(spec)``, an f-string
over a spec and a slice of ``.body`` are read, because they are.
"""

import ast
import inspect
from collections.abc import Mapping
from pathlib import Path
from typing import get_args

import pytest

from kodezart.domain.fire_spec import body_digest
from kodezart.domain.ticket import format_fire_spec
from kodezart.types.domain import fire_spec as partition
from kodezart.types.domain.fire_spec import FireSpec
from tests.name_resolution import (
    SOURCE_ROOT,
    annotated_parameters,
    bound_names,
    call_sites,
    definitions,
    parameters_receiving,
    parsed,
    resolve,
    source_tree,
)

FORMATTER_SOURCE = Path(inspect.getsourcefile(format_fire_spec) or "")
FORMATTER = FORMATTER_SOURCE.relative_to(SOURCE_ROOT).as_posix()
ARMS = {arm.__name__: arm for arm in get_args(FireSpec)}
SPEC_TYPES = frozenset(
    set(ARMS) | {name for name, value in vars(partition).items() if value is FireSpec},
)
DIGEST = body_digest.__name__
DIGEST_HOME = body_digest.__module__


def _arm_fields() -> dict[str, object]:
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


ARM_FIELDS = _arm_fields()
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


def _spells(node: ast.expr) -> str | None:
    """The bare or attribute-qualified word an expression spells."""
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else None


class _Module:
    """One module's declarations, read once for every pass over it."""

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
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
        self.spec_parameters = set(annotated_parameters(tree, mentioning=SPEC_TYPES))
        self.annotated_fields = {
            node.target.id
            for owner in ast.walk(tree)
            if isinstance(owner, ast.ClassDef)
            for node in owner.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and _mentions_spec(node.annotation)
        }
        self.renderers = resolve(tree, names=RENDERERS)
        self.digest = resolve(tree, names={DIGEST}, from_module=DIGEST_HOME)
        self.where = definitions(tree)
        self.expressions = list(ast.walk(tree))
        self.calls = [node for node in self.expressions if isinstance(node, ast.Call)]


class _Package:
    """The roots a spec is reachable from, derived over a whole source map."""

    def __init__(self, sources: Mapping[str, str]) -> None:
        self.modules = {
            path: _Module(tree) for path, tree in parsed(dict(sources)).items()
        }
        self.trees = {path: module.tree for path, module in self.modules.items()}
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
            grown = self._grow_payload_readers(scopes) or self._grow_handed(scopes)
            if not grown:
                return

    def _grow_payload_readers(self, scopes: "dict[str, _Scope]") -> bool:
        found = {
            name
            for path, module in self.modules.items()
            for name, value in module.returns
            if scopes[path].is_payload(value)
        }
        if found <= self.payload_readers:
            return False
        self.payload_readers |= found
        return True

    def _grow_handed(self, scopes: "dict[str, _Scope]") -> bool:
        """Seed each definition with the parameters a call hands a spec to."""
        handed = parameters_receiving(
            self.trees, yields=lambda path, argument: scopes[path].is_spec(argument)
        )
        grown = False
        for (path, _function), parameters in handed.items():
            module = self.modules.get(path)
            if module is None or parameters <= module.spec_parameters:
                continue
            module.spec_parameters |= parameters
            grown = True
        return grown

    def scope(self, path: str) -> "_Scope":
        return _Scope(self, self.modules[path])

    def handed_parameters(self) -> dict[tuple[str, str], frozenset[str]]:
        scopes = {path: self.scope(path) for path in self.modules}
        return parameters_receiving(
            self.trees, yields=lambda path, argument: scopes[path].is_spec(argument)
        )


class _Scope:
    """The names one module binds to a spec or to an arm's payload."""

    def __init__(self, package: _Package, module: _Module) -> None:
        self.package = package
        self.module = module
        self.specs = set(module.spec_parameters)
        self.payloads: set[str] = set()
        while True:
            before = set(self.specs), set(self.payloads)
            self.specs |= bound_names(
                module.tree,
                yields=lambda value, _names: self.is_spec(value),
                seeds=self.specs,
            )
            self.payloads |= bound_names(
                module.tree,
                yields=lambda value, _names: self.is_payload(value),
                seeds=self.payloads,
            )
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
            return _spells(node.func) in self.package.readers | SPEC_TYPES
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
            return _spells(node.func) in self.package.payload_readers
        if isinstance(node, ast.Attribute):
            return node.attr in PAYLOAD_FIELDS and self.is_spec(node.value)
        return False

    def reads_text(self, node: ast.AST) -> bool:
        """Whether this site loads an arm's own text off a spec."""
        return (
            isinstance(node, ast.Attribute)
            and node.attr in TEXT_FIELDS
            and isinstance(node.ctx, ast.Load)
            and self.is_spec(node.value)
        )

    def renders(self, node: ast.AST) -> bool:
        """Whether this site turns an arm into text without the formatter."""
        if isinstance(node, ast.Attribute):
            return self.reads_text(node)
        if isinstance(node, ast.FormattedValue):
            return self.is_payload(node.value) or self.is_spec(node.value)
        if isinstance(node, ast.Call):
            reached = self.module.renderers.denotes(node.func) in RENDERERS or _spells(
                node.func
            ) in RENDERERS | {str.__name__}
            if not reached:
                return False
            given = [*node.args, *(keyword.value for keyword in node.keywords)]
            return any(self.is_payload(one) or self.is_spec(one) for one in given)
        return False

    def is_digest(self, node: ast.AST) -> bool:
        """Whether this call hands an arm's text to the one digest function.

        Resolved through the digest's own home module, so a local definition
        of the same word is not it; and by shape, so a second argument, a
        keyword or anything but a bare text read is a reading of the bytes
        rather than a hashing of them.
        """
        return (
            isinstance(node, ast.Call)
            and self.module.digest.denotes(node.func) == DIGEST
            and len(node.args) == 1
            and not node.keywords
            and self.reads_text(node.args[0])
        )

    def digest_sites(self) -> list[str]:
        """The definitions in this module that hash an arm's text."""
        return [
            self.module.where[id(call)]
            for call in self.module.calls
            if self.is_digest(call)
        ]

    def render_sites(self) -> list[str]:
        """The definitions in this module that read an arm's text themselves."""
        hashed = {
            id(call.args[0]) for call in self.module.calls if self.is_digest(call)
        }
        return [
            self.module.where[id(node)]
            for node in self.module.expressions
            if self.renders(node) and id(node) not in hashed
        ]


def _report(sources: Mapping[str, str]) -> dict[str, dict[str, tuple[str, ...]]]:
    """Where an arm's text is read, and where it is only hashed.

    ``{"read": {module: definitions}, "digested": {module: definitions}}``,
    the formatter's own module excluded and a module with neither omitted.
    One entry per site, so a definition holding two sites is named twice.
    """
    package = _Package(sources)
    found: dict[str, dict[str, tuple[str, ...]]] = {"read": {}, "digested": {}}
    for path in sources:
        if path == FORMATTER:
            continue
        scope = package.scope(path)
        for kind, sites in (
            ("read", scope.render_sites()),
            ("digested", scope.digest_sites()),
        ):
            if sites:
                found[kind][path] = tuple(sorted(sites))
    return found


PACKAGE = source_tree()
PARSED = parsed(PACKAGE)

#: The two positions where an arm's text leaves the formatter's reach without
#: being rendered: a lane's record pins the subject it entered on, and the
#: entry compares that pin with the subject it just read.  A digest of the
#: bytes renders nothing, so it is counted apart from a read.  A third digest
#: position is a decision recorded here, not a convenience, and nothing is
#: re-routed through the formatter because that would change a record
#: digest's bytes.
DIGEST_POSITIONS = {
    "chains/ralph_loop.py": ("RalphLoop._lane_binding",),
    "domain/lane_entry.py": ("require_unamended_subject",),
}


def _control(source: str) -> tuple[int, int]:
    """How many reads and digests a control module adds to the scan."""
    report = _report({**PACKAGE, "control.py": source})
    return (
        len(report["read"].get("control.py", ())),
        len(report["digested"].get("control.py", ())),
    )


def test_no_module_beside_the_formatter_reads_an_arm_itself():
    """Every consumer reaches the arm through the formatter, or only hashes it.

    The roots are derived: a function whose return annotation names a spec
    type, a class field annotated with one, a state key of that name, a
    parameter annotated with one, and a parameter some call in the package
    hands such a value to.  From each, the names a module binds grow to a
    fixed point, and every read of the arm's own text off one of them is a
    site.
    """
    assert _report(PACKAGE) == {"read": {}, "digested": DIGEST_POSITIONS}


def test_the_partition_is_reached_by_every_kind_of_root():
    """The scan is not vacuous: each kind of root is populated at head.

    The call edge is walked at head and finds parameters, but every one of
    them is already annotated with a spec type, so it seeds nothing the
    annotations did not already seed.  That is why the probe this root exists
    for had to be planted: an unannotated parameter handed a spec is a shape
    the package does not currently write, and the control below is the only
    place the root does work on its own.
    """
    package = _Package(PACKAGE)
    handed = package.handed_parameters()
    beyond_annotations = {
        key: parameters
        - set(annotated_parameters(package.modules[key[0]].tree, mentioning=SPEC_TYPES))
        for key, parameters in handed.items()
        if key[0] in package.modules
    }
    annotated = {path for path in PACKAGE if package.modules[path].spec_parameters}
    keyed = {
        path
        for path in PACKAGE
        if any(
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in package.fields
            for node in package.modules[path].expressions
        )
    }

    assert package.readers
    assert package.fields
    assert package.payload_readers
    assert annotated
    assert keyed
    assert handed
    assert {key: extra for key, extra in beyond_annotations.items() if extra} == {}


def test_the_formatter_is_the_render_every_holder_reaches():
    """The formatter is called, so an empty read map is a fact about the tree."""
    reached = {
        site.module for site in call_sites(PARSED, names={format_fire_spec.__name__})
    }

    assert reached >= {"chains/fire_implementation.py", "services/fire_time_rulings.py"}
    assert FORMATTER not in reached


def test_the_scan_catches_a_spec_held_by_an_annotated_parameter():
    control = (
        "from kodezart.types.domain.fire_spec import TrackerSpec\n"
        "\n"
        "def subject_text(spec: TrackerSpec) -> str:\n"
        "    return spec.body\n"
    )
    assert _control(control) == (1, 0)


def test_the_scan_catches_a_spec_held_by_a_model_field():
    control = "def subject_text(request):\n    return request.original_spec.body\n"
    assert _control(control) == (1, 0)


def test_the_scan_catches_a_spec_held_by_a_state_key_or_a_port_read():
    control = (
        "async def node(state, tracker):\n"
        '    held = state["fire_spec"]\n'
        '    fetched = await tracker.read_fire_spec(issue_key="KOD-1")\n'
        "    return held.body, fetched.body\n"
    )
    assert _control(control) == (2, 0)


def test_the_boundary_catches_a_module_rendering_an_arm_itself():
    control = (
        "def node(state):\n"
        "    spec = current_fire_spec(state)\n"
        "    return format_ticket_as_task(spec.ticket), spec.body\n"
    )
    assert _control(control) == (2, 0)


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
    assert _control(control) == (2, 0)


PROBE = (
    "def _own_text(value):\n"
    "    return (\n"
    "        value.body\n"
    "        if isinstance(value, TrackerSpec)\n"
    "        else format_fire_spec(value)\n"
    "    )\n"
)
IMPLEMENTATION = "chains/fire_implementation.py"
IMPLEMENTATION_ANCHOR = "        task_md = format_fire_spec(spec)\n"


def test_the_scan_catches_a_spec_handed_to_an_unannotated_parameter_in_the_tree():
    """The probe that walked past the annotation-only roots, in a real module.

    The helper is written beside the consumer and takes its spec from the
    consumer's own call, so the consumer is no longer the site: the helper
    is, and the report names it.
    """
    sources = dict(PACKAGE)
    assert sources[IMPLEMENTATION].count(IMPLEMENTATION_ANCHOR) == 1
    sources[IMPLEMENTATION] = (
        sources[IMPLEMENTATION].replace(
            IMPLEMENTATION_ANCHOR, "        task_md = _own_text(spec)\n"
        )
        + "\n\n"
        + PROBE
    )

    report = _report(sources)

    assert report["read"] == {IMPLEMENTATION: ("_own_text",)}
    assert report["digested"] == DIGEST_POSITIONS


CONTROL_IMPORT = "from kodezart.control import _own_text"


@pytest.mark.parametrize(
    ("form", "imported", "call", "probe", "reported"),
    [
        ("positional", CONTROL_IMPORT, "_own_text(spec)", PROBE, "_own_text"),
        ("keyword", CONTROL_IMPORT, "_own_text(value=spec)", PROBE, "_own_text"),
        (
            "through_a_receiver",
            CONTROL_IMPORT,
            "self._own_text(spec)",
            "class Reader:\n"
            "    def _own_text(self, value):\n"
            "        return value.body\n",
            "Reader._own_text",
        ),
        (
            "aliased_import",
            "from kodezart.control import _own_text as h",
            "h(spec)",
            PROBE,
            "_own_text",
        ),
        (
            "module_alias",
            "import kodezart.control as helpers",
            "helpers._own_text(spec)",
            PROBE,
            "_own_text",
        ),
        (
            "submodule_import",
            "from kodezart import control",
            "control._own_text(spec)",
            PROBE,
            "_own_text",
        ),
    ],
)
def test_the_scan_catches_a_spec_handed_to_an_unannotated_parameter(
    form, imported, call, probe, reported
):
    """The helper is scanned wherever it lives and however it is reached."""
    caller = (
        f"{imported}\n"
        "\n"
        "def node(state):\n"
        "    spec = current_fire_spec(state)\n"
        f"    return {call}\n"
    )
    report = _report({**PACKAGE, "caller.py": caller, "control.py": probe})

    assert report["read"] == {"control.py": (reported,)}


def test_a_spec_handed_on_through_two_unannotated_helpers_is_followed():
    """The chain is walked to the helper that reads, not stopped at the first.

    ``helper1`` takes the spec from the consumer's call and hands it on
    without reading it; ``helper2`` reads the arm's text.  The re-walk over
    the grown scopes is what seeds ``helper2``, so it is the only site.
    """
    caller = (
        "from kodezart.control import helper1\n"
        "\n"
        "def node(state):\n"
        "    spec = current_fire_spec(state)\n"
        "    return helper1(spec)\n"
    )
    control = (
        "def helper1(first):\n"
        "    return helper2(first)\n"
        "\n"
        "def helper2(second):\n"
        "    return second.body\n"
    )
    report = _report({**PACKAGE, "caller.py": caller, "control.py": control})

    assert report["read"] == {"control.py": ("helper2",)}


@pytest.mark.parametrize(
    ("shape", "body", "expected"),
    [
        ("digest_alone", f"    return {DIGEST}(spec.body)\n", (0, 1)),
        (
            "digest_beside_a_read",
            f"    return {DIGEST}(spec.body) + spec.body\n",
            (1, 1),
        ),
        ("length", "    return len(spec.body)\n", (1, 0)),
        ("digest_with_a_salt", f"    return {DIGEST}(spec.body, salt)\n", (1, 0)),
    ],
)
def test_a_digest_of_the_arm_text_is_a_digest_and_anything_else_is_a_read(
    shape, body, expected
):
    """Only the digest's own one-argument shape hashes; the rest is a read."""
    control = (
        f"from {DIGEST_HOME} import {DIGEST}\n"
        "from kodezart.types.domain.fire_spec import TrackerSpec\n"
        "\n"
        "def pin(spec: TrackerSpec, salt):\n"
        f"{body}"
    )
    assert _control(control) == expected


@pytest.mark.parametrize(
    ("origin", "control", "expected"),
    [
        (
            "aliased_import",
            f"from {DIGEST_HOME} import {DIGEST} as pin\n"
            "from kodezart.types.domain.fire_spec import TrackerSpec\n"
            "\n"
            "def held(spec: TrackerSpec):\n"
            "    return pin(spec.body)\n",
            (0, 1),
        ),
        (
            "local_definition",
            "from kodezart.types.domain.fire_spec import TrackerSpec\n"
            "\n"
            f"def {DIGEST}(body):\n"
            "    return body\n"
            "\n"
            "def held(spec: TrackerSpec):\n"
            f"    return {DIGEST}(spec.body)\n",
            (1, 0),
        ),
    ],
)
def test_a_digest_under_another_name_is_still_a_digest_and_a_local_one_is_not(
    origin, control, expected
):
    """The exemption keys on the function, not on the word that spells it."""
    assert _control(control) == expected


def test_a_spec_field_read_that_is_not_the_arm_text_is_no_site():
    control = (
        "from kodezart.types.domain.fire_spec import TrackerSpec\n"
        "\n"
        "def addressed(spec: TrackerSpec):\n"
        "    return spec.subject, spec.criteria\n"
    )
    assert _control(control) == (0, 0)
