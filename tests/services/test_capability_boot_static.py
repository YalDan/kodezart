"""The scan-capability probe is a boot question, asked once and never handled.

A pass gate whose scan the credential cannot answer reports "nothing moved"
on every tick, which is what a quiet board reports too (KOD-706).  So the
answer is taken once, at boot, and the refusal is a typed abort: no consumer
asks again at tick time, no consumer names the refusal type, and no handler
turns either into a log line the deployment then runs past.

What the walk keys on is read off production objects.  The probe is the port
member's own name, the abort is the error type's own name, and the
vocabulary a consumer would branch on grows from both: the error's own
instance attributes (read off an instance of it) and the backend's refusal
marker, by its value and by the name of the adapter constant that holds it.
The preflight is the function the composition root calls, the root is the
module the lifespan is defined in, and the scanned tree is the package the
preflight is packaged in.  The probe's holder is the one function the walk
finds calling it; its reach is exclusive: the preflight alone calls the
holder, and the lifespan alone calls the preflight.

Where the vocabulary may be named is permitted per function, not per module.
The port's and the error's modules are read off their objects; the
implementers are the modules that define a function under the probe's name,
found by the walk; and inside the preflight's module only its imports and
the bodies of the preflight and the holder may name any of it.

The handler check runs over every edge of the boot chain, and the chain is
derived rather than listed: from the probe, each callee's one caller, up to
the lifespan.  The probe's own call may sit under no handler at all.  Every
call above it, the preflight's call of the holder included, is checked as
"no handler lets it continue", not "no ``try`` encloses
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
without naming either the probe or the error.  A scope revoked after boot is
outside the boot check: the backend's refusal then arrives as a
``TrackerUnavailableError``, which the pass gate's tick-time transport arm
(``services/pass_gate.py``) handles as an outage.  The marker scan is what
keeps a consumer from branching on that refusal's text instead.
"""

import ast
import contextlib
import functools
import sys
from pathlib import Path

import pytest

from kodezart import main
from kodezart.adapters.linear import tracker as linear_tracker
from kodezart.composition.passes import verify_pass_preflight
from kodezart.core.errors import PassGateCapabilityError
from kodezart.core.protocols import TrackerPort

#: The question, the refusal, and the boot act that asks it.
ASK = TrackerPort.verify_scan_capability.__name__
ABORT = PassGateCapabilityError.__name__
PREFLIGHT = verify_pass_preflight.__name__

#: The refusal's own instance attributes: what a consumer holding the error
#: would read to branch on it.
ERROR_ATTRIBUTES = frozenset(vars(PassGateCapabilityError("", refusals=())))
#: The backend's refusal marker, and every adapter constant holding it.
MARKER = linear_tracker._SCOPE_REFUSAL_MARKER
MARKER_NAMES = frozenset(
    name for name, value in vars(linear_tracker).items() if value == MARKER
)
#: Every name a consumer of the capability answer would have to spell.
VOCABULARY = frozenset({ASK, ABORT, MARKER}) | ERROR_ATTRIBUTES | MARKER_NAMES

#: The package the preflight is packaged in, and so the tree this speaks for.
PREFLIGHT_FILE = Path(sys.modules[verify_pass_preflight.__module__].__file__ or "")
SOURCE_ROOT = PREFLIGHT_FILE.resolve().parents[1]
PREFLIGHT_MODULE = PREFLIGHT_FILE.resolve().relative_to(SOURCE_ROOT).as_posix()

#: Where a capability vocabulary belongs: the port that declares the question
#: and the error that carries the refusal, each read off its own production
#: object; the implementers and the boot act are found by the walk below.
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
#: The composition root, where the boot act is called, and the one function
#: in it that calls the boot act.
ROOT_MODULE = (
    Path(sys.modules[main.lifespan.__module__].__file__ or "")
    .resolve()
    .relative_to(SOURCE_ROOT)
    .as_posix()
)
ROOT_FUNCTION = main.lifespan.__name__

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


@functools.cache
def parsed_tree() -> tuple[tuple[str, ast.Module], ...]:
    """Every shipped module, parsed once for the whole module."""
    return tuple(
        (path.relative_to(SOURCE_ROOT).as_posix(), ast.parse(path.read_text()))
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
    )


def module_trees() -> dict[str, ast.Module]:
    """Every shipped module keyed by its path in the package, a fresh mapping."""
    return dict(parsed_tree())


def callers(trees: dict[str, ast.Module], name: str) -> frozenset[str]:
    """Every ``module::function`` that calls *name*."""
    return frozenset(
        f"{module}::{site}"
        for module, tree in trees.items()
        for site in call_sites(tree, name)
    )


def holder_of(trees: dict[str, ast.Module]) -> str:
    """The one function that calls the probe, by its own name."""
    [site] = callers(trees, ASK)
    return site.partition("::")[2].rpartition(".")[2]


def unreached(trees: dict[str, ast.Module], holder: str) -> list[str]:
    """Every way the probe's holder or the boot act is reached from elsewhere.

    The preflight alone calls the holder (unless the preflight is the holder,
    with the probe inlined), and the lifespan alone calls the preflight.
    """
    found: list[str] = []
    booting = f"{PREFLIGHT_MODULE}::{PREFLIGHT}"
    if holder != PREFLIGHT and (reaching := callers(trees, holder)) != {booting}:
        found.append(f"{holder} is called by {sorted(reaching)}")
    root = f"{ROOT_MODULE}::{ROOT_FUNCTION}"
    if (reaching := callers(trees, PREFLIGHT)) != {root}:
        found.append(f"{PREFLIGHT} is called by {sorted(reaching)}")
    return found


def boot_chain(trees: dict[str, ast.Module]) -> list[tuple[str, str, str]]:
    """Each call edge from the probe up to the lifespan: module, caller, callee.

    Derived with ``callers()``: the probe's one caller, that caller's one
    caller, and so on until the lifespan.  The walk stops where a callee has
    no single caller (the reach test reports that), and is bounded by the
    number of functions in the tree, so a cycle ends it too.
    """
    limit = sum(
        isinstance(node, FUNCTIONS)
        for tree in trees.values()
        for node in ast.walk(tree)
    )
    chain: list[tuple[str, str, str]] = []
    callee = ASK
    for _ in range(limit):
        sites = callers(trees, callee)
        if len(sites) != 1:
            break
        [site] = sites
        module, _, scope = site.partition("::")
        caller = scope.rpartition(".")[2]
        chain.append((module, caller, callee))
        if (module, caller) == (ROOT_MODULE, ROOT_FUNCTION):
            break
        callee = caller
    return chain


def run_past_edges(
    trees: dict[str, ast.Module], chain: list[tuple[str, str, str]]
) -> list[str]:
    """Every edge of *chain* whose callee's result is swallowed or run past.

    The probe edge may sit under no handler at all; every other edge may sit
    only under a handler that carries the failure out.
    """
    found: list[str] = []
    for module, caller, callee in chain:
        tree = trees[module]
        reported = (
            [f"line {guard.lineno}" for guard in handled_calls(tree, callee)]
            if callee == ASK
            else continuing(tree, callee)
        )
        found.extend(f"{module}::{caller} -> {callee}: {item}" for item in reported)
    return found


def implementers(trees: dict[str, ast.Module]) -> frozenset[str]:
    """The modules that define a function under the probe's own name."""
    return frozenset(
        module
        for module, tree in trees.items()
        if any(
            isinstance(node, FUNCTIONS) and node.name == ASK for node in ast.walk(tree)
        )
    )


def naming(trees: dict[str, ast.Module]) -> frozenset[str]:
    """Every module that names any of the capability vocabulary."""
    return frozenset(
        module for module, tree in trees.items() if VOCABULARY & names(tree)
    )


def consumers(trees: dict[str, ast.Module], holder: str) -> frozenset[str]:
    """Every module, or preflight-module statement, naming the vocabulary.

    The port's and the error's modules and the implementers may name it.  In
    the preflight's module only the imports and the bodies of the preflight
    and the holder may; any other top-level statement there that names it is
    reported by its own name.
    """
    permitted = {PORT_MODULE, ERROR_MODULE} | implementers(trees)
    allowed = {PREFLIGHT, holder}
    found: set[str] = set()
    for module in naming(trees):
        if module in permitted:
            continue
        if module != PREFLIGHT_MODULE:
            found.add(module)
            continue
        for statement in trees[module].body:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(statement, FUNCTIONS) and statement.name in allowed:
                continue
            if VOCABULARY & names(statement):
                label = getattr(statement, "name", f"line {statement.lineno}")
                found.add(f"{module}::{label}")
    return frozenset(found)


def test_the_capability_probe_is_called_from_the_boot_preflight_alone():
    """One site asks, and the boot act is what reaches it.

    The probe is a round trip whose answer is a deployment fact; a second
    caller would be a second time the answer could be taken, and a tick-time
    caller would be the silent gate itself.
    """
    trees = module_trees()
    sites = callers(trees, ASK)
    assert len(sites) == 1, sorted(sites)
    [site] = sites
    assert site.partition("::")[0] == PREFLIGHT_MODULE
    # The preflight alone reaches the holder, and the lifespan alone reaches
    # the preflight: no second path to the answer, handled or not.
    assert unreached(trees, holder_of(trees)) == []


def test_no_consumer_names_the_capability_answer():
    """Outside the port, the error, the implementers and the boot act: nobody.

    A consumer that names the probe, the error, the error's refusals or the
    backend's refusal marker has somewhere to put a runtime branch on
    capability, which is the degradation this criterion refuses.
    """
    trees = module_trees()
    assert {PREFLIGHT_MODULE, ERROR_MODULE} <= naming(trees)
    found = consumers(trees, holder_of(trees))
    assert found == frozenset(), sorted(found)


def test_no_handler_lets_the_probe_or_the_boot_refusal_continue():
    """Neither the question nor the abort is caught and run past.

    Every edge of the boot chain is checked, derived from the probe up to the
    lifespan rather than listed: the probe call is under no handler at all,
    and each call above it (the holder in the preflight, the preflight in the
    lifespan) is under no handler that lets the run go on. The boot act is
    called inside the lifespan's one failure path, which binds the failure and
    raises it after it has unwound its resources — so the refusal still ends
    the boot, and a handler that merely logged it would be reported here.
    """
    trees = module_trees()
    chain = boot_chain(trees)
    holder = holder_of(trees)
    assert chain[0][1:] == (holder, ASK), chain
    assert chain[-1] == (ROOT_MODULE, ROOT_FUNCTION, PREFLIGHT), chain
    assert run_past_edges(trees, chain) == []
    assert handled_calls(trees[ROOT_MODULE], PREFLIGHT)


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
            "async def tick(gate, log):\n"
            "    try:\n"
            "        return await gate.scan()\n"
            "    except Exception as exc:\n"
            f"        if hasattr(exc, {sorted(ERROR_ATTRIBUTES)[0]!r}):\n"
            "            return []\n"
            "        raise\n",
            "name",
            id="a-consumer-testing-the-errors-attribute",
        ),
        pytest.param(
            "async def tick(gate):\n"
            "    try:\n"
            "        return await gate.scan()\n"
            "    except Exception as exc:\n"
            f"        if {MARKER!r} in str(exc):\n"
            "            return []\n"
            "        raise\n",
            "name",
            id="a-consumer-testing-the-refusal-marker",
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
        assert len(callers({**module_trees(), PLANTED: tree}, ASK)) == 2
    elif detector == "name":
        trees = {**module_trees(), PLANTED: tree}
        assert PLANTED in consumers(trees, holder_of(module_trees()))
    elif detector == "probe":
        assert handled_calls(tree, ASK)
        assert continuing(tree, ASK)
    else:
        assert continuing(tree, PREFLIGHT)


#: Where a planted consumer sits in the package: a services-shaped module
#: outside every permitted one.
PLANTED = "services/planted.py"


def with_preflight_statement(body: str) -> dict[str, ast.Module]:
    """The shipped tree, with *body* appended to the preflight's own module."""
    trees = module_trees()
    source = (SOURCE_ROOT / PREFLIGHT_MODULE).read_text()
    trees[PREFLIGHT_MODULE] = ast.parse(source + "\n\n" + body)
    return trees


def test_a_second_handled_caller_of_the_holder_is_reported():
    """A tick-time closure that asks through the holder is a second path."""
    holder = holder_of(module_trees())
    trees = with_preflight_statement(
        "def report_builder():\n"
        "    async def report(config):\n"
        "        try:\n"
        f"            await {holder}(config=config)\n"
        f"        except {ABORT}:\n"
        "            pass\n"
        "    return report\n"
    )
    assert unreached(trees, holder)
    assert f"{PREFLIGHT_MODULE}::report_builder" in consumers(trees, holder)


def test_a_second_caller_of_the_boot_act_is_reported():
    """The lifespan is the one caller of the preflight."""
    trees = {
        **module_trees(),
        PLANTED: ast.parse(f"async def tick(config):\n    await {PREFLIGHT}(config)\n"),
    }
    assert unreached(trees, holder_of(module_trees()))


def test_a_probe_inlined_into_the_preflight_satisfies_the_reach():
    """The holder may be the preflight itself: then nothing else reaches it."""
    trees = {
        PREFLIGHT_MODULE: ast.parse(
            f"async def {PREFLIGHT}(tracker, signals):\n"
            f"    await tracker.{ASK}(signals=signals)\n"
        ),
        ROOT_MODULE: ast.parse(
            f"async def {ROOT_FUNCTION}(app):\n    await {PREFLIGHT}(app.tracker, [])\n"
        ),
    }
    assert holder_of(trees) == PREFLIGHT
    assert unreached(trees, PREFLIGHT) == []


#: The call on each edge of a boot chain, unguarded: the holder's probe, the
#: preflight's call of the holder, and the lifespan's call of the preflight.
PROBE_CALL = f"await tracker.{ASK}(signals=signals)\n"
HOLDER_CALL = "await {holder}(config=config, tracker=tracker)\n"
PREFLIGHT_CALL = f"await {PREFLIGHT}(config=app.config, tracker=app.tracker)\n"


def indented(statement: str, depth: int) -> str:
    """*statement* indented by *depth* levels of four spaces."""
    return "".join(
        f"{' ' * 4 * depth}{line}\n" for line in statement.splitlines() if line
    )


def planted_chain(*, probe: str, holding: str, booting: str) -> dict[str, ast.Module]:
    """A boot chain of three edges, each call replaced by the body given for it.

    The holder and the preflight sit in the preflight's module and the
    lifespan in the root module, under the shipped names, so the chain walk
    derives the same three edges it derives from the shipped tree.
    """
    holder = holder_of(module_trees())
    return {
        PREFLIGHT_MODULE: ast.parse(
            f"async def {holder}(*, config, tracker):\n"
            "    signals = config.signals\n"
            f"{indented(probe, 1)}"
            f"async def {PREFLIGHT}(*, config, tracker):\n"
            f"{indented(holding.format(holder=holder), 1)}"
        ),
        ROOT_MODULE: ast.parse(
            f"async def {ROOT_FUNCTION}(app, log):\n{indented(booting, 1)}    yield\n"
        ),
    }


def test_the_boot_chain_derived_from_a_clean_chain_reports_nothing():
    """Non-vacuity for the planted edges below: unguarded calls pass."""
    trees = planted_chain(probe=PROBE_CALL, holding=HOLDER_CALL, booting=PREFLIGHT_CALL)
    holder = holder_of(module_trees())
    assert boot_chain(trees) == [
        (PREFLIGHT_MODULE, holder, ASK),
        (PREFLIGHT_MODULE, PREFLIGHT, holder),
        (ROOT_MODULE, ROOT_FUNCTION, PREFLIGHT),
    ]
    assert run_past_edges(trees, boot_chain(trees)) == []


@pytest.mark.parametrize(
    "edge",
    [
        pytest.param(
            {"probe": (f"try:\n    {PROBE_CALL}except Exception as exc:\n    raise\n")},
            id="the-probe-edge-under-any-handler",
        ),
        pytest.param(
            {
                "holding": (
                    "try:\n"
                    f"    {HOLDER_CALL}"
                    f"except {ABORT} as exc:\n"
                    "    if not config.http.debug:\n"
                    "        raise\n"
                    "    await log.awarning('pass_gate_capability_refused', "
                    "error=str(exc))\n"
                )
            },
            id="the-holder-edge-raising-only-outside-debug",
        ),
        pytest.param(
            {
                "booting": (
                    "try:\n"
                    f"    {PREFLIGHT_CALL}"
                    "except BaseException as exc:\n"
                    "    await log.aerror('boot_failed', error=str(exc))\n"
                )
            },
            id="the-root-edge-logging-and-serving",
        ),
    ],
)
def test_a_guard_on_any_edge_of_the_boot_chain_is_reported(edge):
    """The chain walk checks each edge, the middle one included."""
    calls = {"probe": PROBE_CALL, "holding": HOLDER_CALL, "booting": PREFLIGHT_CALL}
    trees = planted_chain(**{**calls, **edge})
    chain = boot_chain(trees)
    assert len(chain) == 3, chain
    [reported] = run_past_edges(trees, chain)
    [(module, caller, callee)] = [
        link
        for link, name in zip(chain, ("probe", "holding", "booting"), strict=True)
        if name in edge
    ]
    assert reported.startswith(f"{module}::{caller} -> {callee}: "), reported


def test_a_preflight_module_function_naming_the_abort_is_reported():
    """Inside the preflight's module the permission is per function."""
    holder = holder_of(module_trees())
    trees = with_preflight_statement(
        "async def tick(gate):\n"
        "    try:\n"
        "        return await gate.scan()\n"
        f"    except {ABORT}:\n"
        "        return None\n"
    )
    assert f"{PREFLIGHT_MODULE}::tick" in consumers(trees, holder)


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
