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

The walk is textual and executes nothing, which is what lets it speak for the
whole tree.  Its blind spots, which review has to read from the code instead:
a probe reached through ``getattr`` with a computed name, and a refusal
mapping passed through a variable into another function that branches on it
without naming either the probe or the error.
"""

import ast
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


def handled_calls(tree: ast.AST, name: str) -> list[ast.Try]:
    """Every ``try`` with handlers that encloses a call to *name*."""
    enclosing: list[ast.Try] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not node.handlers:
            continue
        if any(
            isinstance(inner, ast.Call) and called_name(inner) == name
            for guarded in node.body
            for inner in ast.walk(guarded)
        ):
            enclosing.append(node)
    return enclosing


def continues_past(handler: ast.ExceptHandler, scope: ast.AST) -> bool:
    """Whether this handler lets the run go on with the failure unraised.

    The failure is carried if the handler re-raises, or if what it binds the
    exception to reaches a ``raise`` anywhere in the same scope — a boot that
    records its failure and raises it after it has unwound is still a boot
    that dies of it.  Anything else logs and continues.
    """
    carried = {handler.name} if handler.name is not None else set()
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return False
        if isinstance(node, ast.Assign) and any(
            isinstance(value, ast.Name) and value.id in carried
            for value in ast.walk(node.value)
        ):
            carried.update(
                target.id
                for target in ast.walk(node)
                if isinstance(target, ast.Name) and target.ctx.__class__ is ast.Store
            )
    return not any(
        isinstance(node, ast.Raise)
        and any(
            isinstance(raised, ast.Name) and raised.id in carried
            for raised in ast.walk(node)
        )
        for node in ast.walk(scope)
    )


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
    enclosing = handled_calls(root, PREFLIGHT)
    assert enclosing
    for guard in enclosing:
        for handler in guard.handlers:
            assert not continues_past(handler, root), ast.dump(handler)


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
            "handler",
            id="a-probe-that-logs-and-continues",
        ),
        pytest.param(
            "async def lifespan(app, log):\n"
            "    try:\n"
            f"        await {PREFLIGHT}(config=app.config)\n"
            "    except BaseException as exc:\n"
            "        await log.aerror('boot_failed', error=str(exc))\n"
            "    yield\n",
            "continues",
            id="a-boot-that-logs-its-refusal-and-serves",
        ),
    ],
)
def test_each_detector_reports_a_planted_site(body, detector):
    tree = ast.parse(body)
    if detector == "call":
        assert call_sites(tree, ASK)
    elif detector == "name":
        assert {ASK, ABORT} & names(tree)
    elif detector == "handler":
        assert handled_calls(tree, ASK)
    else:
        [guard] = handled_calls(tree, PREFLIGHT)
        assert any(continues_past(handler, tree) for handler in guard.handlers)
