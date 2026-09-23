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
from kodezart.core.protocols import (
    EscalationAgeingReader,
    EscalationResolutionReader,
    RunAlarmTracker,
)

SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src"
#: Where the scan starts. The composition module is one of them because it is
#: the module the integration tick actually runs and the one place in this slice
#: where a collaborator is constructed — an adapter reached only from there
#: would otherwise be outside every assertion below.
ENTRY_POINTS = (
    "kodezart.services.supervisor_pass",
    "kodezart.services.tally_supervisor",
    "kodezart.services.escalation_ageing_supervisor",
    "kodezart.services.run_alarm_recorder",
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
#: The observation's own modules, including the ones that compute an open
#: question's age (KOD-507, KOD-851): no signal reads wall-clock time. The
#: clock rules below are scoped to these rather than to the whole closure,
#: because the scheduler that drives the tick legitimately holds a clock and an
#: event loop; what is refused is a second one, of the observation's own.
OWN_MODULES = (
    "kodezart.services.supervisor_pass",
    "kodezart.services.tally_supervisor",
    "kodezart.services.escalation_ageing_supervisor",
    "kodezart.services.run_alarm_recorder",
    "kodezart.services.escalation_signals",
    "kodezart.services.escalation_records",
    "kodezart.domain.escalation_age_record",
    "kodezart.composition.supervisor",
)
#: Waiting, scheduling and reading the time are the scheduler's, so a module of
#: the observation importing one of these is taking a second opinion on when.
CLOCK_MODULES = frozenset({"time", "asyncio", "threading", "sched"})
#: Waiting and arming a timer, whichever module it came from. Reading the time
#: off ``datetime`` is not listed here: that one admissible import is refused as
#: a whole below, so ``now``, ``utcnow``, ``today``, ``fromtimestamp`` and
#: ``strptime`` are refused at once rather than named one at a time.
CLOCK_CALLS = frozenset(
    {
        "sleep",
        "monotonic",
        "perf_counter",
        "call_later",
        "call_at",
        "Timer",
    }
)
#: The one clock-carrying name these modules may import: ``datetime`` is the
#: type of the stamp the tick is handed, so the import stands and every call
#: THROUGH it is refused.
CLOCK_CARRIER = "datetime"
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
    *carrier_calls* carries every attribute called on the clock-carrying name,
    so the rule about it is "nothing through this name" rather than a list of
    the attributes somebody thought of.
    """

    modules: frozenset[str]
    names: frozenset[str]
    plain: frozenset[str]
    calls: frozenset[str]
    carrier_calls: frozenset[str]


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
    carrier_calls: set[str] = set()
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
                target = node.func.value
                if isinstance(target, ast.Name) and target.id == CLOCK_CARRIER:
                    carrier_calls.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                calls.add(node.func.id)
    return Scanned(
        modules=frozenset(modules),
        names=frozenset(names),
        plain=frozenset(plain),
        calls=frozenset(calls),
        carrier_calls=frozenset(carrier_calls),
    )


def closure(
    entry_points: tuple[str, ...] = ENTRY_POINTS,
    *,
    root: pathlib.Path = SOURCE_ROOT,
    first_party: str = "kodezart",
    leaves: tuple[str, ...] = (),
) -> dict[str, Scanned]:
    """The transitive first-party import closure of *entry_points*.

    A source-tree walk: nothing here is imported at runtime, so *root* and
    *first_party* are all it takes to point the same walker at a tree written
    for a test.

    *leaves* are modules the walk reaches and does not descend into. That is
    what makes "reached ONLY through this module" derivable: the same walk with
    a module as a leaf reaches everything the entry points reach by any other
    route, so what that walk misses is exactly what this one alone leads to.
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
        if module in leaves:
            continue
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
    # Every rule below is a disjointness, which an empty closure satisfies, so
    # the walk is required to have gone past the points it started from. The
    # bound is derived from those points rather than written as a count the tree
    # would then have to keep agreeing with.
    assert len(scanned) > len(ENTRY_POINTS), sorted(scanned)
    # Where the whole port may be held: the composition root, which narrows it
    # by design; the walker's read path, which this slice left holding it; and
    # the modules the supervisor reaches ONLY through that read path. The last
    # set is derived — the walker's own closure, less everything the same walk
    # reaches with the walker treated as a leaf — so a module the supervisor
    # imports itself is outside the allowance however deep the walker goes into
    # it as well.
    reached_without_the_walker = set(closure(ENTRY_POINTS, leaves=WALKER_READ_PATH))
    port_allowed = (
        {PORT_HOLDING_ROOT}
        | set(WALKER_READ_PATH)
        | (set(closure(WALKER_READ_PATH)) - reached_without_the_walker)
    )

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

    Both import forms are covered, and the guarded tree's own form is one of
    them: a control shaped only like ``import probe.inner`` would stay green for
    a walker that followed plain imports and nothing else, while the tree this
    guards reaches every one of its modules through ``from ... import ...`` and
    none through a plain import at all.
    """
    package = tmp_path / "probe"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "entry.py").write_text(
        "from probe import inner\nimport probe.plain\n", encoding="utf-8"
    )
    for reached in ("inner", "plain"):
        (package / f"{reached}.py").write_text(
            "from kodezart.adapters.linear import tracker\n", encoding="utf-8"
        )

    scanned = closure(("probe.entry",), root=tmp_path, first_party="probe")

    for reached in ("probe.inner", "probe.plain"):
        assert reached in scanned, sorted(scanned)
        assert any(
            imported.startswith("kodezart.adapters")
            for imported in scanned[reached].modules
        )


def test_the_supervisor_keeps_no_sleep_timer_or_clock_of_its_own():
    """The tick waits for nothing and times nothing: the scheduler does both.

    A pass that slept, armed a timer, or read a clock of its own would have a
    cadence and a notion of elapsed time that no configuration names, and a
    short sleep is invisible to a bounded integration tick. The rule is scoped
    to the observation's own modules: the scheduler it is registered on
    holds the event loop and the one clock, which is where they belong.

    ``from datetime import datetime`` stays admissible — it is the type of the
    stamp the tick is handed — and the rule about it is derived from that one
    admission rather than listed: NO call through that name, so ``now``,
    ``utcnow``, ``today``, ``fromtimestamp`` and ``strptime`` are each refused
    without any of them having been thought of here. What is left listed is
    waiting and arming a timer through any other object.
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
        assert found.carrier_calls == frozenset(), (module, found.carrier_calls)


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


def test_the_ageing_roles_name_only_reads():
    """The roles an open question's age is read through hold no write at all.

    Both are narrowings of the port, so the port satisfies any widening of
    them; the member sets are pinned exactly for the reason the observation's
    own role is.
    """
    resolution = get_protocol_members(EscalationResolutionReader)
    ageing = get_protocol_members(EscalationAgeingReader)

    assert resolution == {"read_escalation_resolution"}
    assert ageing == {"list_comments", "read_escalation_resolution"}
    assert ageing.isdisjoint(STATE_MOVING_CALLS), ageing & STATE_MOVING_CALLS


def test_the_pass_factory_takes_no_runner_and_no_repository_collaborator():
    """The factory's own parameters: configuration, the operation, one port."""
    parameters = inspect.signature(build_supervisor_pass).parameters

    assert set(parameters) == {"config", "operation", "tracker"}
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in parameters.values()
    )
