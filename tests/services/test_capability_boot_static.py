"""The scan-capability probe is a boot question, asked once and never handled.

A pass gate whose scan the credential cannot answer reports "nothing moved"
on every tick, which is what a quiet board reports too (KOD-706).  So the
answer is taken once, at boot, and the refusal is a typed abort: no consumer
asks again at tick time, no consumer names the refusal type, and no handler
turns either into a log line the deployment then runs past.

Everything the walk keys on is derived: the probe is the port member's own
name, the abort is the error type's own name, the preflight is the function
the composition root calls, and the scanned tree is the package that function
is packaged in.  Nothing is listed by hand except the modules a capability
vocabulary must live in — the port, the error, the adapters that implement
the port, and the preflight itself — and each of those is derived from the
production object that defines it.

The handler check is "no handler lets it continue", not "no ``try`` encloses
it", because the composition root's lifespan has one failure path around
every boot act (``main.py``'s outer ``try`` and its ``except BaseException``):
it binds the failure, unwinds what boot acquired, and raises the failure
again, which
``tests/test_lifespan_cleanup.py::test_partial_startup_releases_every_resource_it_acquired[preflight]``
shows for a failing preflight.  So a handler around the preflight is allowed
exactly when it carries the failure out, on Python's own rule: ``except …
as name`` unbinds ``name`` when the handler ends, so the handler carries the
failure only if its last statement is an unconditional ``raise`` (bare, or
of the bound name or an alias of it), or if it unconditionally assigns the
exception to another name that a ``raise`` in the enclosing function names.
A ``raise`` under a condition, a loop or a ``try`` inside the handler is a
path that does not raise.  ``except*`` is a handler too, and a ``with``
over ``suppress`` is a handler that never raises.

The walk is textual and executes nothing, which is what lets it speak for the
whole tree.  Its blind spots, which review has to read from the code instead:
a probe reached through ``getattr`` with a computed name, and a refusal
mapping passed through a variable into another function that branches on it
without naming either the probe or the error.
"""

import ast
import contextlib
import sys
from pathlib import Path

import pytest

from kodezart.composition.passes import verify_pass_preflight
from kodezart.core.errors import PassGateCapabilityError
from kodezart.core.protocols import TrackerPort

#: The question, the refusal, and the boot act that asks it.
ASK = TrackerPort.verify_scan_capability.__name__
ABORT = PassGateCapabilityError.__name__
PREFLIGHT = verify_pass_preflight.__name__

#: The package the preflight is packaged in, and so the tree this speaks for.
PREFLIGHT_FILE = Path(sys.modules[verify_pass_preflight.__module__].__file__ or "")
SOURCE_ROOT = PREFLIGHT_FILE.resolve().parents[1]
PREFLIGHT_MODULE = PREFLIGHT_FILE.resolve().relative_to(SOURCE_ROOT).as_posix()

#: Where a capability vocabulary belongs: the port that declares the question,
#: the error that carries the refusal, the adapters that answer it, and the
#: boot act that asks.  Each is read off its own production object.
PORT_MODULE = (
    Path(sys.modules[TrackerPort.__module__].__file__ or "")
    .resolve()
    .relative_to(SOURCE_ROOT)
    .as_posix()
)
ERROR_MODULE = (
    Path(sys.modules[PassGateCapabilityError.__module__].__file__ or "")
    .resolve()
    .relative_to(SOURCE_ROOT)
    .as_posix()
)
#: The adapter package: the implementers, which must name the port's member to
#: implement it at all.
ADAPTERS = "adapters/"
#: The composition root, where the boot act is called.
ROOT_MODULE = "main.py"

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
#: A ``try`` and an ``except*`` both hold handlers.
TRIES = (ast.Try, ast.TryStar)
WITHS = (ast.With, ast.AsyncWith)
#: The context manager that swallows what it names, by its own name.
SUPPRESS = contextlib.suppress.__name__

Guard = ast.Try | ast.TryStar | ast.With | ast.AsyncWith


def called_name(node: ast.Call) -> str | None:
    """The name this call calls, attribute or bare."""
    called = node.func
    if isinstance(called, ast.Attribute):
        return called.attr
    return called.id if isinstance(called, ast.Name) else None


def call_sites(tree: ast.AST, name: str) -> frozenset[str]:
    """Each scope that calls *name*, as the qualified name of the scope."""
    found: set[str] = set()

    def visit(scope: ast.AST, label: str | None) -> None:
        stack: list[ast.AST] = [scope]
        while stack:
            current = stack.pop()
            for child in ast.iter_child_nodes(current):
                if isinstance(child, DEFINITIONS):
                    visit(
                        child, child.name if label is None else f"{label}.{child.name}"
                    )
                    continue
                if isinstance(child, ast.Call) and called_name(child) == name:
                    found.add(label if label is not None else f"line {child.lineno}")
                stack.append(child)

    visit(tree, None)
    return frozenset(found)


def names(tree: ast.AST) -> frozenset[str]:
    """Every name this module spells, in any form a use takes."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.asname or node.name.rpartition(".")[2])
        elif isinstance(node, DEFINITIONS):
            found.add(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
    return frozenset(found)


def suppresses(item: ast.withitem) -> bool:
    """Whether this ``with`` item is a call to ``suppress``, bare or qualified."""
    return (
        isinstance(item.context_expr, ast.Call)
        and called_name(item.context_expr) == SUPPRESS
    )


def handled_calls(tree: ast.AST, name: str) -> list[Guard]:
    """Every handler-holding ``try``, ``except*`` or ``suppress`` around *name*.

    Only the guarded body counts: a call in a handler, an ``else`` or a
    ``finally`` is not one the guard can swallow.
    """
    enclosing: list[Guard] = []
    for node in ast.walk(tree):
        if isinstance(node, TRIES) and node.handlers:
            guarded: list[ast.stmt] = node.body
        elif isinstance(node, WITHS) and any(suppresses(item) for item in node.items):
            guarded = node.body
        else:
            continue
        if any(
            isinstance(inner, ast.Call) and called_name(inner) == name
            for statement in guarded
            for inner in ast.walk(statement)
        ):
            enclosing.append(node)
    return enclosing


def enclosing_function(tree: ast.AST, node: ast.AST) -> ast.AST:
    """The innermost function holding *node*, or the module when none does."""
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    current = parents.get(id(node))
    while current is not None and not isinstance(current, FUNCTIONS):
        current = parents.get(id(current))
    return tree if current is None else current


def carries(handler: ast.ExceptHandler, function: ast.AST) -> bool:
    """Whether every path out of *handler* raises the failure it caught.

    Its last statement is an unconditional ``raise`` of nothing, the bound
    name or an alias of it; or one of its own statements assigns the
    exception to another name, which a ``raise`` in *function* (nested
    functions included, the handler's own conditional raises excluded)
    names.  The bound name itself never counts past the handler, because
    Python unbinds it when the handler ends.
    """
    bound = {handler.name} if handler.name is not None else set()
    aliases: set[str] = set()
    for statement in handler.body:
        value = statement.value if isinstance(statement, ast.Assign) else None
        if isinstance(statement, ast.AnnAssign):
            value = statement.value
        if isinstance(value, ast.Name) and value.id in bound | aliases:
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
                if isinstance(statement, ast.AnnAssign)
                else []
            )
            aliases.update(
                target.id for target in targets if isinstance(target, ast.Name)
            )
    last = handler.body[-1]
    if isinstance(last, ast.Raise) and (
        last.exc is None
        or (isinstance(last.exc, ast.Name) and last.exc.id in bound | aliases)
    ):
        return True
    inside = {id(node) for node in ast.walk(handler)}
    return any(
        isinstance(node, ast.Raise)
        and id(node) not in inside
        and isinstance(node.exc, ast.Name)
        and node.exc.id in aliases
        for node in ast.walk(function)
    )


def continuing(tree: ast.AST, name: str) -> list[str]:
    """Every guard around *name* that lets the run go on, by line.

    A ``suppress`` always does; a ``try`` or ``except*`` does through any
    handler that does not carry the failure out.
    """
    found: list[str] = []
    for guard in handled_calls(tree, name):
        if isinstance(guard, WITHS):
            found.append(f"line {guard.lineno}: {SUPPRESS}")
            continue
        function = enclosing_function(tree, guard)
        found.extend(
            f"line {handler.lineno}: {ast.unparse(handler).splitlines()[0]}"
            for handler in guard.handlers
            if not carries(handler, function)
        )
    return found


def module_trees() -> dict[str, ast.Module]:
    """Every shipped module, parsed once, keyed by its path in the package."""
    return {
        path.relative_to(SOURCE_ROOT).as_posix(): ast.parse(path.read_text())
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
    }


def test_the_capability_probe_is_called_from_the_boot_preflight_alone():
    """One site asks, and the boot act is what reaches it.

    The probe is a round trip whose answer is a deployment fact; a second
    caller would be a second time the answer could be taken, and a tick-time
    caller would be the silent gate itself.
    """
    sites = {
        f"{module}::{site}"
        for module, tree in module_trees().items()
        for site in call_sites(tree, ASK)
    }
    assert len(sites) == 1, sorted(sites)
    [site] = sites
    module, _, holder = site.partition("::")
    assert module == PREFLIGHT_MODULE
    #: The boot act itself calls the body that asks.
    preflight = next(
        node
        for node in ast.walk(module_trees()[PREFLIGHT_MODULE])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == PREFLIGHT
    )
    assert call_sites(preflight, holder.rpartition(".")[2])


def test_no_consumer_names_the_capability_answer():
    """Outside the port, the error, the adapters and the boot act: nobody.

    A consumer that names either has somewhere to put a runtime branch on
    capability, which is the degradation this criterion refuses.
    """
    naming = {
        module for module, tree in module_trees().items() if {ASK, ABORT} & names(tree)
    }
    permitted = {PORT_MODULE, ERROR_MODULE, PREFLIGHT_MODULE}
    consumers = {
        module
        for module in naming
        if module not in permitted and not module.startswith(ADAPTERS)
    }
    assert naming
    assert consumers == set(), sorted(consumers)


def test_no_handler_lets_the_probe_or_the_boot_refusal_continue():
    """Neither the question nor the abort is caught and run past.

    The probe call is under no handler at all. The boot act is called inside
    the lifespan's one failure path, which binds the failure and raises it
    after it has unwound its resources — so the refusal still ends the boot,
    and a handler that merely logged it would be reported here.
    """
    trees = module_trees()
    assert handled_calls(trees[PREFLIGHT_MODULE], ASK) == []
    root = trees[ROOT_MODULE]
    assert handled_calls(root, PREFLIGHT)
    assert continuing(root, PREFLIGHT) == []


#: The lifespan's own shape around a planted guard: one failure path that
#: binds the failure, and a nested unwind that raises it again.
LIFESPAN = (
    "async def lifespan(app, log, config):\n"
    "    failure = None\n"
    "    try:\n"
    "{guarded}"
    "        yield\n"
    "    except BaseException as exc:\n"
    "        failure = exc\n"
    "    async def unwind():\n"
    "        if failure is not None:\n"
    "            raise failure\n"
    "    await unwind()\n"
)


def lifespan_around(guarded: str) -> str:
    """A lifespan whose outer failure path encloses *guarded*."""
    return LIFESPAN.format(guarded=guarded)


@pytest.mark.parametrize(
    "body,detector",
    [
        pytest.param(
            "class Gate:\n"
            "    async def tick(self, tracker, signals):\n"
            f"        refusals = await tracker.{ASK}(signals=signals)\n"
            "        if refusals:\n"
            "            return []\n"
            "        return await self.scan()\n",
            "call",
            id="a-second-caller-branching-on-the-answer",
        ),
        pytest.param(
            f"from kodezart.core.errors import {ABORT}\n"
            "async def tick(gate):\n"
            "    try:\n"
            "        return await gate.scan()\n"
            f"    except {ABORT}:\n"
            "        return []\n",
            "name",
            id="a-consumer-naming-the-abort",
        ),
        pytest.param(
            "async def preflight(tracker, log, signals):\n"
            "    try:\n"
            f"        await tracker.{ASK}(signals=signals)\n"
            "    except Exception as exc:\n"
            "        await log.awarning('capability_probe_failed', error=str(exc))\n",
            "probe",
            id="a-probe-that-logs-and-continues",
        ),
        pytest.param(
            "import contextlib\n"
            "async def preflight(tracker, signals):\n"
            "    with contextlib.suppress(Exception):\n"
            f"        await tracker.{ASK}(signals=signals)\n",
            "probe",
            id="a-probe-under-suppress",
        ),
        pytest.param(
            "async def preflight(tracker, signals):\n"
            "    try:\n"
            f"        await tracker.{ASK}(signals=signals)\n"
            "    except* Exception:\n"
            "        pass\n",
            "probe",
            id="a-probe-under-except-star",
        ),
        pytest.param(
            "async def lifespan(app, log):\n"
            "    try:\n"
            f"        await {PREFLIGHT}(config=app.config)\n"
            "    except BaseException as exc:\n"
            "        await log.aerror('boot_failed', error=str(exc))\n"
            "    yield\n",
            "boot",
            id="a-boot-that-logs-its-refusal-and-serves",
        ),
        pytest.param(
            lifespan_around(
                "        try:\n"
                f"            await {PREFLIGHT}(config=config)\n"
                "        except Exception as exc:\n"
                "            if not getattr(exc, 'refusals', None):\n"
                "                raise\n"
                "            await log.aerror('capability_refused', error=str(exc))\n"
            ),
            "boot",
            id="a-boot-that-raises-all-but-the-refusal",
        ),
        pytest.param(
            lifespan_around(
                "        try:\n"
                f"            await {PREFLIGHT}(config=config)\n"
                "        except Exception as exc:\n"
                "            if not config.http.debug:\n"
                "                raise\n"
                "            await log.awarning('preflight_refused', error=str(exc))\n"
            ),
            "boot",
            id="a-boot-that-raises-only-outside-debug",
        ),
        pytest.param(
            lifespan_around(
                "        try:\n"
                f"            await {PREFLIGHT}(config=config)\n"
                "        except Exception as failure:\n"
                "            await log.awarning('refused', error=str(failure))\n"
            ),
            "boot",
            id="an-inner-handler-binding-the-raised-name-that-only-logs",
        ),
        pytest.param(
            lifespan_around(
                "        with contextlib.suppress(Exception):\n"
                f"            await {PREFLIGHT}(config=config)\n"
            ),
            "boot",
            id="a-boot-under-suppress",
        ),
        pytest.param(
            lifespan_around(
                "        try:\n"
                f"            await {PREFLIGHT}(config=config)\n"
                "        except* Exception:\n"
                "            pass\n"
            ),
            "boot",
            id="a-boot-under-except-star",
        ),
    ],
)
def test_each_detector_reports_a_planted_site(body, detector):
    tree = ast.parse(body)
    if detector == "call":
        assert call_sites(tree, ASK)
    elif detector == "name":
        assert {ASK, ABORT} & names(tree)
    elif detector == "probe":
        assert handled_calls(tree, ASK)
        assert continuing(tree, ASK)
    else:
        assert continuing(tree, PREFLIGHT)


@pytest.mark.parametrize(
    "guarded",
    [
        pytest.param(f"        await {PREFLIGHT}(config=config)\n", id="unguarded"),
        pytest.param(
            "        try:\n"
            f"            await {PREFLIGHT}(config=config)\n"
            "        except Exception as exc:\n"
            "            await log.aerror('preflight_failed', error=str(exc))\n"
            "            raise\n",
            id="logs-then-re-raises",
        ),
        pytest.param(
            "        try:\n"
            f"            await {PREFLIGHT}(config=config)\n"
            "        except Exception as exc:\n"
            "            refused = exc\n"
            "            raise refused\n",
            id="raises-an-alias",
        ),
    ],
)
def test_a_boot_that_carries_its_refusal_out_is_not_reported(guarded):
    """The lifespan's own shape passes: a handler that carries the failure.

    The outer failure path binds the failure to a name its nested unwind
    raises, and an inner handler that ends in an unconditional raise carries
    it too; neither is a boot that runs past its refusal.
    """
    tree = ast.parse(lifespan_around(guarded))
    assert handled_calls(tree, PREFLIGHT)
    assert continuing(tree, PREFLIGHT) == []
