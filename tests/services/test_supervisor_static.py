"""What the supervisor can reach at all, read off its own import closure.

The surface is derived rather than listed: every ``kodezart`` module the two
supervisor modules reach transitively is scanned, so a collaborator added
behind one more import is inside the assertion the moment it lands.
"""

import ast
import inspect
import pathlib
from dataclasses import dataclass

from typing_extensions import get_protocol_members

from kodezart.composition.supervisor import build_supervisor_pass
from kodezart.core.protocols import RunAlarmTracker

SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src"
#: Where the scan starts. The composition module is one of them because it is
#: the module the integration tick actually runs and the one place in this slice
#: where a collaborator is constructed — an adapter reached only from there
#: would otherwise be outside every assertion below.
ENTRY_POINTS = (
    "kodezart.services.supervisor_pass",
    "kodezart.services.tally_supervisor",
    "kodezart.composition.supervisor",
)
#: The one place the whole port is held on purpose, by design: the composition
#: root narrows it into the roles below it. The walker's read path is the other,
#: and it is derived from the walker's own closure rather than listed, so a
#: module that starts holding the port has to be reached from the walker or be
#: this one.
PORT_HOLDING_ROOT = "kodezart.composition.supervisor"
WALKER_READ_PATH = ("kodezart.chains.scope_walker",)
#: Starting a process, reading or writing a file, importing by name. Asserted
#: over call targets rather than over module names, because the module that
#: carries most of these (``asyncio``) is held legitimately elsewhere in the
#: closure — the scheduler's event loop and the owned-task settle.
IO_CALLS = frozenset(
    {
        "create_subprocess_exec",
        "create_subprocess_shell",
        "Popen",
        "system",
        "check_output",
        "open",
        "read_text",
        "read_bytes",
        "write_text",
        "import_module",
        "__import__",
    }
)
#: A role whose holder could dispatch a session, read a repository, prepare a
#: tree, push a change, merge a branch, or write anything the tracker offers.
FORBIDDEN_ROLES = frozenset(
    {
        "AgentRunner",
        "AgentExecutor",
        "GitService",
        "WorkspaceProvider",
        "ChangePersister",
        "BranchMerger",
        "TrackerPort",
    }
)
FORBIDDEN_MODULES = frozenset({"subprocess", "os", "os.path", "shutil", "socket"})
#: Exactly what the observation's role names: three lease calls, one keyed
#: record read and write, one lane stream read and one append to it.
SUPERVISOR_ROLE_MEMBERS = frozenset(
    {
        "acquire_surfaces",
        "renew_surfaces",
        "release_surfaces",
        "read_run_alarm",
        "record_run_alarm",
        "lane_run_events",
        "post_run_event",
    }
)
#: The slice's own three modules. The clock rules below are scoped to these
#: rather than to the whole closure, because the scheduler that drives the tick
#: legitimately holds a clock and an event loop; what is refused is a second
#: one, of the observation's own.
OWN_MODULES = (
    "kodezart.services.supervisor_pass",
    "kodezart.services.tally_supervisor",
    "kodezart.composition.supervisor",
)
#: Waiting, scheduling and reading the time are the scheduler's, so a module of
#: the observation importing one of these is taking a second opinion on when.
CLOCK_MODULES = frozenset({"time", "asyncio", "threading", "sched"})
#: Reading a clock or arming a timer, whichever module it came from.
CLOCK_CALLS = frozenset(
    {
        "sleep",
        "monotonic",
        "perf_counter",
        "now",
        "utcnow",
        "call_later",
        "call_at",
        "Timer",
    }
)
#: The calls that move a run's state. The role is narrowed out of the port, so
#: the port satisfies any widening of it and neither mypy nor a behavioural
#: test would notice one of these arriving.
STATE_MOVING_CALLS = frozenset(
    {
        "set_workflow_state",
        "restore_workflow_state",
        "set_queue_state",
        "reset_criterion_pending",
        "edit_description",
    }
)


@dataclass(frozen=True)
class Scanned:
    """What one module's source says it reaches.

    *modules* carries each imported module and, for a first-party ``from``
    import, the dotted name of every alias too, so ``from kodezart import
    adapters`` is seen as ``kodezart.adapters`` rather than as ``kodezart``.
    *plain* carries the modules imported as whole modules, which is how a role
    can be reached without its name ever appearing in an import.
    """

    modules: frozenset[str]
    names: frozenset[str]
    plain: frozenset[str]
    calls: frozenset[str]


def _module_path(module: str, *, root: pathlib.Path) -> pathlib.Path | None:
    single = root / (module.replace(".", "/") + ".py")
    if single.exists():
        return single
    package = root / module.replace(".", "/") / "__init__.py"
    return package if package.exists() else None


def _scan(path: pathlib.Path, *, first_party: str) -> Scanned:
    """Every module and name this file imports, and every call target in it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    names: set[str] = set()
    plain: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
            names.update(alias.name for alias in node.names)
            if node.module.startswith(first_party):
                modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
            plain.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                calls.add(node.func.id)
    return Scanned(
        modules=frozenset(modules),
        names=frozenset(names),
        plain=frozenset(plain),
        calls=frozenset(calls),
    )


def closure(
    entry_points: tuple[str, ...] = ENTRY_POINTS,
    *,
    root: pathlib.Path = SOURCE_ROOT,
    first_party: str = "kodezart",
) -> dict[str, Scanned]:
    """The transitive first-party import closure of *entry_points*.

    A source-tree walk: nothing here is imported at runtime, so *root* and
    *first_party* are all it takes to point the same walker at a tree written
    for a test.
    """
    scanned: dict[str, Scanned] = {}
    pending = list(entry_points)
    while pending:
        module = pending.pop()
        if module in scanned:
            continue
        path = _module_path(module, root=root)
        if path is None:
            continue
        found = _scan(path, first_party=first_party)
        scanned[module] = found
        pending.extend(
            imported for imported in found.modules if imported.startswith(first_party)
        )
    return scanned


def test_the_supervisor_reaches_no_adapter_no_process_and_no_repository_role():
    """Everything the tick can reach at all, read off the source tree.

    Five rules over the same closure. The whole port is allowed in the
    composition root, where it is narrowed by design, and on the walker's read
    path, which this slice left on the whole port; that allowance is derived
    from the walker's own closure rather than listed, so it cannot quietly
    cover a module nothing reaches from there. A plain import of a first-party
    module is refused outright, because it puts every name in that module
    within reach while the import names none of them. And the process and file
    calls are asserted as call targets rather than as forbidden modules,
    because the module that carries most of them is held legitimately by the
    scheduler and the owned-task settle inside this closure.
    """
    scanned = closure()
    assert set(ENTRY_POINTS) <= set(scanned)
    port_allowed = {PORT_HOLDING_ROOT} | set(closure(WALKER_READ_PATH))

    for module, found in scanned.items():
        assert not module.startswith("kodezart.adapters"), module
        assert not any(
            imported.startswith("kodezart.adapters") for imported in found.modules
        ), module
        assert found.modules.isdisjoint(FORBIDDEN_MODULES), (module, found.modules)
        assert not any(name.startswith("kodezart") for name in found.plain), (
            module,
            found.plain,
        )
        assert found.calls.isdisjoint(IO_CALLS), (module, found.calls & IO_CALLS)
        roles = FORBIDDEN_ROLES - (
            {"TrackerPort"} if module in port_allowed else frozenset()
        )
        assert found.names.isdisjoint(roles), (module, found.names & roles)


def test_the_closure_walker_flags_an_adapter_reached_through_one_more_import(tmp_path):
    """The walker's own positive control: a scan that finds nothing looks green.

    Every assertion above is a disjointness, so a walker that stopped at its
    entry points would pass all of them on an empty closure. This points the
    same walker at a tree written to hold an adapter one import deeper than the
    entry point and requires it to arrive there. Nothing here is imported; it
    is read as source, which is why the probe may name an adapter freely.
    """
    package = tmp_path / "probe"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "entry.py").write_text("import probe.inner\n", encoding="utf-8")
    (package / "inner.py").write_text(
        "from kodezart.adapters.linear import tracker\n", encoding="utf-8"
    )

    scanned = closure(("probe.entry",), root=tmp_path, first_party="probe")

    assert "probe.inner" in scanned, sorted(scanned)
    assert any(
        imported.startswith("kodezart.adapters")
        for imported in scanned["probe.inner"].modules
    )


def test_the_supervisor_keeps_no_sleep_timer_or_clock_of_its_own():
    """The tick waits for nothing and times nothing: the scheduler does both.

    A pass that slept, armed a timer, or read a clock of its own would have a
    cadence and a notion of elapsed time that no configuration names, and a
    short sleep is invisible to a bounded integration tick. The rule is scoped
    to the observation's own three modules: the scheduler it is registered on
    holds the event loop and the one clock, which is where they belong.

    ``from datetime import datetime`` stays admissible — it is the type of the
    stamp the tick is handed — while ``datetime.now()`` is an attribute call
    named among the clock reads and is refused.
    """
    for module in OWN_MODULES:
        path = _module_path(module, root=SOURCE_ROOT)
        assert path is not None, module
        found = _scan(path, first_party="kodezart")

        assert found.modules.isdisjoint(CLOCK_MODULES), (
            module,
            found.modules & CLOCK_MODULES,
        )
        assert found.calls.isdisjoint(CLOCK_CALLS), (module, found.calls & CLOCK_CALLS)


def test_the_supervisor_role_names_no_state_moving_method():
    """Moving no state is structural: the role has no method that could.

    The role is a narrowing of the port, so the port satisfies it however wide
    it grows — a state writer appended to it type-checks, and no behavioural
    test sees anything until something calls it. The member set is therefore
    pinned exactly, and separately named as disjoint from the state movers so
    a widening says which one arrived.
    """
    members = get_protocol_members(RunAlarmTracker)

    assert members == SUPERVISOR_ROLE_MEMBERS
    assert members.isdisjoint(STATE_MOVING_CALLS), members & STATE_MOVING_CALLS


def test_the_pass_factory_takes_no_runner_and_no_repository_collaborator():
    """The factory's own parameters: configuration, the operation, one port."""
    parameters = inspect.signature(build_supervisor_pass).parameters

    assert set(parameters) == {"config", "operation", "tracker"}
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in parameters.values()
    )
