"""The logging port, and the four facts that keep it honest (KOD-124).

``LogEmitter`` exists so that 35 annotation sites and 123 call sites name a
port this codebase owns rather than a vendor class it does not.  Nothing
about the running system changes: ``get_logger`` returns what it always
returned, and the adapter is not a wrapper but an ASSERTION — structlog's
configured wrapper class already satisfies the port, and the test below is
what says so.

The set of methods is derived, not declared.  A reading of the call sites
is exactly the kind of thing that goes stale — the port's first census
counted four methods and missed ``aexception``'s five call sites — so the
census here is taken from the syntax tree every time the suite runs.
"""

import ast
import inspect
import pathlib

import structlog

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import LogEmitter
from tests.fakes import RecordingLogger

SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "kodezart"

#: The names this codebase binds a logger to.  A call on any other receiver
#: is not a logging call, and a logging call on any other receiver would be
#: a naming violation the census is entitled to miss.
LOGGER_RECEIVERS = frozenset({"log", "_log", "logger", "_logger"})

#: The one module permitted to name structlog.
STRUCTLOG_IMPORT_SITE = SRC_ROOT / "core" / "logging.py"


def _source_files() -> list[pathlib.Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _receiver_name(node: ast.expr) -> str | None:
    """The bound name a call is made on: ``x`` or the ``y`` of ``self.y``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _methods_called_on_loggers() -> set[str]:
    """Every method awaited on a logger anywhere in ``src``, from the AST."""
    called: set[str] = set()
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if _receiver_name(func.value) in LOGGER_RECEIVERS:
                called.add(func.attr)
    return called


def _protocol_methods() -> set[str]:
    """The port's own method set, read off the Protocol."""
    return {name for name in LogEmitter.__protocol_attrs__ if not name.startswith("__")}


def test_the_configured_wrapper_class_satisfies_the_port() -> None:
    """The adapter IS this assertion — there is no wrapper class to write.

    ``configure_logging`` installs ``structlog.stdlib.BoundLogger`` as the
    wrapper class, so that is the object every call site eventually awaits.
    Asserting it against the port is what makes ``get_logger``'s annotation
    a claim rather than a hope: if structlog ever renames or de-async-ifies
    one of the five, this fails instead of the production call site.
    """
    wrapper = structlog.stdlib.BoundLogger

    for name in sorted(_protocol_methods()):
        method = getattr(wrapper, name, None)
        assert method is not None, f"structlog's wrapper class has no {name}"
        assert inspect.iscoroutinefunction(method), f"{name} is not awaitable"

    # And an actual instance passes the runtime check, not merely the class.
    instance = wrapper(structlog.get_logger("port-probe"), [], {})
    assert isinstance(instance, LogEmitter)


def test_the_recording_double_satisfies_the_port() -> None:
    """The test double is bound by the same port as the real emitter."""
    assert isinstance(RecordingLogger(), LogEmitter)


def test_the_alias_denotes_the_port_and_not_the_vendor_class() -> None:
    """``BoundLogger`` is the port; the 35 annotations keep compiling."""
    assert BoundLogger is LogEmitter
    assert get_logger(__name__) is not None


def test_structlog_is_named_at_exactly_one_site_in_src() -> None:
    """One module owns the vendor; every other names only the port.

    This passes today.  It is pinned so that the next module to reach for
    ``structlog`` directly has to justify itself against a red test rather
    than slip in beside the annotations that already look like the port.
    """
    offenders: list[str] = []
    for path in _source_files():
        if path == STRUCTLOG_IMPORT_SITE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] == "structlog" for name in names):
                offenders.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno}")

    assert offenders == [], f"structlog imported outside the port's module: {offenders}"


def test_the_port_declares_exactly_the_methods_src_calls() -> None:
    """Declared and used are the same set, checked both directions.

    A method called but not declared means an annotation lies about what
    its logger can do.  A method declared but never called means the port
    is carrying a member nothing needs — which is how the original census
    came to claim four methods while five were in use.
    """
    declared = _protocol_methods()
    called = _methods_called_on_loggers()

    assert called - declared == set(), (
        f"awaited on a logger but absent from LogEmitter: {sorted(called - declared)}"
    )
    assert declared - called == set(), (
        f"declared on LogEmitter but never awaited: {sorted(declared - called)}"
    )


#: Stands in for a ``**`` spread whose contents the source cannot read.
#: Marked rather than dropped, so an unreadable spread can never make two
#: shapes look like one.
_SPREAD = "**"


def _literal_field_sets(scope: ast.AST) -> dict[str, frozenset[str]]:
    """Names bound to a dict literal of string keys inside one function.

    A field set assembled once and spread into two events is how two
    events are kept in step, and it is readable here: the keys are in the
    source, a few lines above the call.  What is NOT readable is a spread
    of somebody else's mapping, and those two must not be confused.
    """
    bound: dict[str, frozenset[str]] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not isinstance(value, ast.Dict):
            continue
        keys = {
            key.value
            for key in value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        if len(keys) != len(value.keys):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                bound[target.id] = frozenset(keys)
    return bound


def _fields_of(call: ast.Call, bound: dict[str, frozenset[str]]) -> frozenset[str]:
    """The field names one logging call carries, spreads resolved where they can be."""
    fields: set[str] = set()
    for keyword in call.keywords:
        if keyword.arg is not None:
            fields.add(keyword.arg)
        elif isinstance(keyword.value, ast.Name) and keyword.value.id in bound:
            fields |= bound[keyword.value.id]
        else:
            fields.add(_SPREAD)
    return frozenset(fields)


def _shapes_by_event() -> dict[str, dict[frozenset[str], list[str]]]:
    """Every log event in ``src``, by the field sets it is emitted with.

    Taken from the syntax tree for the reason the method census is: a
    reading of 168 event names goes stale, and what this asserts is a
    property of every emit site rather than of the ones somebody
    remembered.  Only the literal-named events are counted — an event
    whose name is computed has no single shape to compare.
    """
    shapes: dict[str, dict[frozenset[str], list[str]]] = {}
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scopes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for scope in [*scopes, tree]:
            bound = _literal_field_sets(scope)
            for node in ast.walk(scope):
                if not isinstance(node, ast.Call) or not isinstance(
                    node.func, ast.Attribute
                ):
                    continue
                if _receiver_name(node.func.value) not in LOGGER_RECEIVERS:
                    continue
                if not node.args or not isinstance(node.args[0], ast.Constant):
                    continue
                event = node.args[0].value
                if not isinstance(event, str):
                    continue
                sites = shapes.setdefault(event, {}).setdefault(
                    _fields_of(node, bound), []
                )
                site = f"{path.name}:{node.lineno}"
                if site not in sites:
                    sites.append(site)
    return shapes


def test_one_event_name_is_emitted_in_exactly_one_shape() -> None:
    """An event is a contract with whoever reads the log (KOD-192).

    Measured 2026-09-01 18:22: ``run_record_write_failed`` reached the log
    from two sites, one carrying a formatted traceback and one carrying
    nothing of the sort, and a reader filtering on the event name could
    not tell a field that was absent from a field that was empty.  Four
    events had two shapes between them on the day this was written.

    A site with a fact the other site does not have says so with the
    field present and ``None`` — which is a STATE of the event, the way
    every other three-state field in this codebase is — rather than by
    leaving the field out.
    """
    divergent = {
        event: shapes for event, shapes in _shapes_by_event().items() if len(shapes) > 1
    }

    assert divergent == {}, "\n".join(
        f"{event}: "
        + " vs ".join(
            f"{sorted(fields)} at {sites}" for fields, sites in sorted(shapes.items())
        )
        for event, shapes in sorted(divergent.items())
    )


def test_no_log_event_is_handed_its_field_set_at_runtime() -> None:
    """A spread hides the shape the census above exists to compare (KOD-192).

    Measured 2026-09-04: ``mcp_session_opened`` was emitted with
    ``**transport.describe()``, which is a url on one transport and a
    command on the other — two shapes for one event, and the census could
    not see either of them, because what a spread contributes is not in
    the source. The guard found four divergences and was structurally
    blind to the fifth, which the same batch had just introduced.

    So the fields are written down. A transport with something of its own
    to say says it as a VALUE under a field the event always carries.
    """
    spread = {
        event: sorted(
            {
                site
                for fields, sites in shapes.items()
                if _SPREAD in fields
                for site in sites
            }
        )
        for event, shapes in _shapes_by_event().items()
        if any(_SPREAD in fields for fields in shapes)
    }

    assert spread == {}, f"log events handed a field set at runtime: {spread}"
