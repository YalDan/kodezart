"""No call site of the gap arithmetic reads the tracker's change stamp.

The recording executor and workspace, and the result event builder, are
shared doubles other modules import from here.

The gap guard below, from ``GAP_ARITHMETIC`` on, holds that no call site of
the gap arithmetic reads the tracker's change stamp.  What it reads, by
object after import: every name, attribute and callee of a definition is
what the module's namespace after import, an import anywhere in it or an
assignment inside the definition binds it to — an aliased import, an import
inside the function, a relative import, a module-level rebinding, a
``functools.partial``, a static method, a walrus, a closure over its
enclosing function's locals — and literal names count wherever they appear:
``getattr(x, "name")``, ``operator.attrgetter("name")``, ``vars(x)["name"]``,
``x.__dict__["name"]``, a ``module:attr`` or ``module.attr`` string, and
``pkgutil.resolve_name`` or ``importlib.import_module`` handed a literal.
Outside the reach: a value handed across a function boundary, where the
other function is not resolved at this site (returned from a helper, stored
on an object and read elsewhere, or passed through a container built
elsewhere); a name built at run time; and a binding made only when a
function runs (``setattr`` or ``globals()`` inside a function body).  Each
shape of the limit is held unseen by
``test_a_shape_outside_the_reach_is_no_call_site``; the trap runs every
function of a gap home, and every call site the fixtures can run, with the
stamp trapped, so a read hidden behind such a shape inside one of them
still reds.
"""

import ast
import functools
import inspect
import sys
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta

import pytest

from kodezart.domain.errors import ScopeReadError
from kodezart.domain.gap import (
    compute_gap,
    gap_membership,
    open_state_kind,
    state_membership,
)
from kodezart.domain.issue_tree import SubtreeClosure
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import AgentEvent, ResultEvent
from kodezart.types.domain.gap import CriterionGap, GapMembership
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope_address import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode, SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionPolicy,
)
from kodezart.types.domain.tracker import (
    IssuePriority,
    IssueQuery,
    ReviewQuery,
    TrackerIssue,
    TrackerIssueRevision,
    WorkflowStateKind,
)
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeLinearMcpServer,
    FakeMcpIssue,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    seed_fake_issue,
    seed_server_issue,
)
from tests.name_resolution import (
    defining_module,
    loaded_values,
    module_namespace,
    modules_reaching,
    referencing_definitions,
    source_tree,
)
from tests.tracker.test_linear_mcp_tracker import tracker_over

SUBJECT = "subject/42"


def issue(key: str, body: str, **changes: object) -> TrackerIssue:
    return TrackerIssue.model_validate(
        {
            "issue_key": key,
            "title": f"Title for {key}",
            "body": body,
            "priority": IssuePriority.NONE,
            "state_name": "Todo",
            "state_kind": WorkflowStateKind.UNSTARTED,
            "queue_states": [],
            "team_key": "engineering",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
            "url": f"https://tracker.invalid/{key}",
            **changes,
        }
    )


def result(**changes: object) -> ResultEvent:
    return ResultEvent.model_validate(
        {
            "result": "Author rationale must never be forwarded.",
            "session_id": "previous-agent-session",
            "subtype": "result",
            "duration_ms": 1,
            "duration_api_ms": 1,
            "is_error": False,
            "num_turns": 1,
            "structured_output": {
                "issue_id": SUBJECT,
                "verdict": "buildable",
                "evidence": "Concrete repository evidence.",
            },
            **changes,
        }
    )


class RecordingExecutor:
    """The real service reaches this strict executor with all permissions visible."""

    def __init__(self, events: Sequence[AgentEvent]) -> None:
        self.events = events
        self.calls: list[dict[str, object]] = []

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: PermissionMode,
        allowed_tools: list[str],
        skills: SkillsSelection,
        session_type: SessionType,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
        run_identity: RunIdentity | None = None,
    ) -> AsyncIterator[AgentEvent]:
        self.calls.append(
            {
                "prompt": prompt,
                "cwd": cwd,
                "permission_mode": permission_mode,
                "allowed_tools": allowed_tools,
                "skills": skills,
                "session_type": session_type,
                "agents": tuple(agents),
                "session_policy": session_policy,
                "session_id": session_id,
                "output_format": output_format,
                "run_identity": run_identity,
            }
        )
        for event in self.events:
            yield event


class RecordingWorkspace(FakeWorkspaceProvider):
    def __init__(self) -> None:
        super().__init__()
        self.arguments: list[dict[str, object]] = []

    async def acquire(self, **kwargs):
        self.arguments.append(kwargs)
        return await super().acquire(**kwargs)


async def test_session_type_is_required_by_the_actual_runner_call():
    runner = AgentService(
        executor=RecordingExecutor([]),
        workspace=RecordingWorkspace(),
        git_base_url="https://example.invalid",
    )
    with pytest.raises(TypeError, match="session_type"):
        runner.stream_in_workspace(
            prompt="fixture",
            workspace_path="/tmp/fixture",
            permission_mode=PermissionMode.PLAN,
            allowed_tools=[],
            skills=SUPPRESS_ALL_SKILLS,
        )


BODY_MARKER = "body_ready"


def gap_revision(key, **changes):
    return TrackerIssueRevision(
        issue=issue(key, f"Body for {key}", **changes),
        body_digest=f"opaque:{key}",
    )


def organized_family(key=SUBJECT):
    return (
        gap_revision(key, issue_labels=[BODY_MARKER]),
        gap_revision(
            f"{key}/check",
            parent_key=key,
            issue_labels=["criterion"],
            state_kind=WorkflowStateKind.COMPLETED,
            state_name="Done",
        ),
    )


@pytest.fixture(params=["fake", "linear"])
def organized_port(request):
    # *stamp_reads* makes every further read of this port move the change
    # stamp, whichever double is underneath. The UNCHANGED replay below writes
    # nothing, so a stamp that only a write moves cannot tell a digest taken
    # from the body alone from one that folds the stamp into it; a stamp that
    # moves on the read can. It is a switch rather than a constructor value
    # because an admission session compares the whole issue across its
    # context read and its revision read, so no session may run under it.
    revisions = (*organized_family(), *organized_family("other/17"))
    keys = tuple(revision.issue.issue_key for revision in revisions)
    if request.param == "fake":
        source = FakeTrackerPort(issues=[revision.issue for revision in revisions])

        def stamp_reads() -> None:
            source.stamp_moves_on_read = True

        def seed_issue(*, issue_key: str, body: str) -> None:
            seed_fake_issue(source, issue_key=issue_key, body=body)
    else:
        server = FakeLinearMcpServer(
            issues=[
                FakeMcpIssue(
                    id=revision.issue.issue_key,
                    description=revision.issue.body,
                    parent_id=revision.issue.parent_key,
                    status=revision.issue.state_name,
                    status_type=revision.issue.state_kind.value,
                    labels=["acceptance-condition"]
                    if "criterion" in revision.issue.issue_labels
                    else ["body-phase-finished"],
                )
                for revision in revisions
            ],
            state_types={"Todo": "unstarted", "Done": "completed"},
        )
        source = tracker_over(
            server,
            issue_labels={
                "criterion": "acceptance-condition",
                BODY_MARKER: "body-phase-finished",
            },
        )

        def stamp_reads() -> None:
            server.stamp_moves_on_read = True

        def seed_issue(*, issue_key: str, body: str) -> None:
            seed_server_issue(server, issue_key=issue_key, body=body)

    return source, keys, stamp_reads, seed_issue


async def test_the_organized_port_moves_its_stamp_on_read_and_not_its_body_revision(
    organized_port,
):
    """Under the switch a read moves the stamp and the body revision holds.

    Stated on both arms and positively, so the replay case below cannot go
    vacuous: a revision that folded the stamp into its digest would answer two
    reads of one unwritten body with two digests, and the digest holding still
    across those reads is the prohibition itself.
    """
    source, keys, stamp_reads, _seed = organized_port
    stamp_reads()
    first = await source.read_issue(issue_key=keys[1])
    second = await source.read_issue(issue_key=keys[1])
    assert second.updated_at > first.updated_at
    assert second.body == first.body
    one = await source.read_issue_revision(issue_key=keys[1])
    two = await source.read_issue_revision(issue_key=keys[1])
    assert two.issue.updated_at > one.issue.updated_at
    assert two.body_digest == one.body_digest


CHANGE_STAMP_FIELDS = frozenset(
    {"updated_at", "updatedAt", "updated_since", "updatedSince"}
)
#: The gap arithmetic, named by the object rather than by its word: the
#: subtree gap.  Only the module defining it is read, so every definition
#: beside it is inside the arithmetic whether it is named here or not —
#: ``gap_membership`` sits beside ``compute_gap`` — and a module that builds
#: on it, ``SubtreeClosure``'s among them, reaches it by its imports.
GAP_ARITHMETIC = (compute_gap,)


@functools.cache
def gap_home_modules():
    """The gap homes as module objects, read off the arithmetic's own ``__module__``."""
    return tuple(
        sys.modules[name]
        for name in sorted({value.__module__ for value in GAP_ARITHMETIC})
    )


@functools.cache
def gap_home_functions():
    """Every function a gap home defines, read off the module objects.

    The homes are the modules defining ``GAP_ARITHMETIC``; every function
    whose ``__module__`` is one of them is inside the arithmetic — ``gap_membership``
    and ``state_membership`` beside it — so a definition
    referring to any one of them is a call site of the arithmetic, and every
    one of them is an entry point the trap below runs.
    """
    return tuple(
        value
        for home in gap_home_modules()
        for value in vars(home).values()
        if inspect.isfunction(value) and value.__module__ == home.__name__
    )


def gap_home_objects():
    """What a call site refers to: the home modules themselves and their functions.

    A definition that denotes a home module — bound by an import, by a local
    assigned from ``pkgutil.resolve_name`` or ``importlib.import_module``,
    or read as ``getattr(gap, "compute_gap")`` — refers to the arithmetic by
    object as surely as one that denotes a function of it.
    """
    return (*gap_home_modules(), *gap_home_functions())


#: Every supplied module the reach below finds, re-measured off the tree
#: rather than chosen.  A change to it is a change to where the gap can be
#: computed, which is the surface this guard speaks for.
GAP_COMPUTATION_MODULES = frozenset(
    {
        "chains/audit_sweep.py",
        "chains/authored_delivery.py",
        "chains/authored_publication.py",
        "chains/criteria.py",
        "chains/delivery_coordinator.py",
        "chains/fire_consolidation.py",
        "chains/fire_implementation.py",
        "chains/fire_remediation.py",
        "chains/fire_review.py",
        "chains/fire_specification.py",
        "chains/lane_delivery.py",
        "chains/native_delivery.py",
        "chains/ralph_loop.py",
        "chains/ralph_workflow.py",
        "chains/remediation.py",
        "chains/scope_walker.py",
        "composition/audit.py",
        "composition/delivery.py",
        "composition/engine.py",
        "composition/passes.py",
        "composition/supervisor.py",
        "domain/gap.py",
        "domain/issue_tree.py",
        "domain/lane_alarms.py",
        "domain/run_alarm_table.py",
        "domain/stream_signals.py",
        "domain/tally_record.py",
        "main.py",
        "services/alarm_supervisor.py",
        "services/audit_runtime.py",
        "services/audit_terminal.py",
        "services/barren_record_signals.py",
        "services/escalation_ageing_supervisor.py",
        "services/escalation_signals.py",
        "services/mandate_graph.py",
        "services/run_alarm_recorder.py",
        "services/run_shape.py",
        "services/scope_dispatcher.py",
        "services/supervisor_pass.py",
    }
)
#: Every module of the tree that spells the change stamp, and the reason it
#: may.  None of them is in the gap's reach, and none holds a call site of the
#: arithmetic found by object.  A module that newly spells the stamp arrives
#: here as a decision with its reason written down, or reds.  The register is
#: kept per module, so a new read inside a module already here is not seen by
#: it: the trap below holds it when a function of a gap home, or a call site
#: the fixtures can run, runs it; and the call-site scan holds it when the
#: reading function refers to a gap home or a function of one by object, by
#: the stamp's spelling or by the value a name it loads is bound to.
CHANGE_STAMP_READERS = {
    "adapters/linear/tracker.py": "The adapter: it reads the stamp off the wire "
    "and exposes it, and scans by recency, which the Check allows.",
    "adapters/linear/wire.py": "The wire models the adapter parses the stamp into.",
    "domain/criterion_amendment.py": "Leaves the stamp out when it compares a "
    "criterion with the record an amendment expected.",
    "domain/fire_spec.py": "Stamps a captured fire spec with the subject version "
    "it was read at.",
    "services/audit_expectation.py": "Carries the observed stamp onto the record "
    "an audit write expects back.",
    "services/fire_dispatcher.py": "The dispatcher's exclusion memory: a lane "
    "issue stays excluded until its own stamp moves.",
    "services/native_amendments.py": "Leaves the stamp out when it compares a "
    "native write with the record it expected.",
    "services/pass_gate.py": "The pass gate's recency cursor over issue and "
    "review scans.",
    "services/tracker_artifacts.py": "Leaves the stamp out of a tracker "
    "artifact's serialised child.",
    "types/domain/dispatch.py": "The self-write ledger: the stamp its own last "
    "write left on an issue.",
    "types/domain/self_writes.py": "An issue movement snapshot, kept separate "
    "from its scan stamp.",
    "types/domain/tracker.py": "The domain models that declare the stamp and the "
    "recency parameter.",
}


def change_stamp_reads(tree):
    """Every spelling of the tracker's change-timestamp field in parsed source.

    As an attribute, a name, a keyword, a class pattern's keyword and a
    string constant.
    """
    reads = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in CHANGE_STAMP_FIELDS:
            reads.add(node.attr)
        elif isinstance(node, ast.MatchClass):
            reads.update(CHANGE_STAMP_FIELDS.intersection(node.kwd_attrs))
        elif isinstance(node, ast.Name) and node.id in CHANGE_STAMP_FIELDS:
            reads.add(node.id)
        elif isinstance(node, ast.keyword) and node.arg in CHANGE_STAMP_FIELDS:
            reads.add(node.arg)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in CHANGE_STAMP_FIELDS
        ):
            reads.add(node.value)
    return reads


def gap_homes():
    """The modules that define the gap arithmetic, read off the objects."""
    return frozenset(defining_module(value) for value in GAP_ARITHMETIC)


def gap_computation_sites(sources):
    """Every supplied module that can compute the gap.

    A module computes the gap by reaching the arithmetic, and the only static
    way to reach a module is to import it.  So a gap site is any module whose
    imports lead, at any depth, to a module that defines the arithmetic —
    read through ``imported_modules``, which takes an import at the top, in a
    function, in a class or under ``TYPE_CHECKING``, a relative import, a
    submodule imported from its package, a dotted route through an imported
    package, and a string constant naming a module.

    Nothing here follows the gap's answer: a module that imports a helper
    beside the arithmetic is in the reach through that import, whatever shape
    the helper hands the answer on in.

    Wider than the Check, and stated so a red is read right: a module that
    imports the arithmetic's module for any reason, a type among them, is
    counted as able to compute the gap.  Not in the reach: a module handed the
    arithmetic or its answer at run time — by argument, attribute or
    callback — without importing either.  Not seen: a module name assembled
    at run time, a relative name handed to ``importlib.import_module`` with
    its package, and ``eval`` or ``exec``.
    """
    trees = {relative: _parsed_once(source) for relative, source in sources.items()}
    return {
        relative: trees[relative]
        for relative in sorted(_gap_reach(frozenset(sources.items())))
    }


@functools.cache
def _parsed_once(source):
    """One module's syntax tree, parsed once per text.

    The guards below read the same unchanged tree many times over and a
    planted case changes one or two modules, so each text is parsed once.
    """
    return ast.parse(source)


@functools.cache
def _gap_reach(sources):
    """The reach over one frozen snapshot of the supplied modules."""
    trees = {relative: _parsed_once(source) for relative, source in sources}
    return modules_reaching(trees, homes=gap_homes())


def _stamp_reads_of(source):
    """What one module's text reads of the change stamp."""
    return change_stamp_reads(_parsed_once(source))


def change_stamp_readers(sources):
    """Every supplied module that reads the change stamp, with what it reads."""
    return {
        relative: _stamp_reads_of(source)
        for relative, source in sources.items()
        if _stamp_reads_of(source)
    }


def gap_sites_reading_the_change_stamp(sources):
    """The discovered gap sites that reach the tracker's change-timestamp field."""
    return {
        relative: _stamp_reads_of(sources[relative])
        for relative in gap_computation_sites(sources)
        if _stamp_reads_of(sources[relative])
    }


def test_no_gap_computation_call_site_reads_the_tracker_change_timestamp():
    homes = gap_homes()
    discovered = gap_computation_sites(source_tree())
    assert homes
    assert discovered
    assert homes <= discovered.keys()
    assert discovered.keys() <= GAP_COMPUTATION_MODULES
    assert gap_sites_reading_the_change_stamp(source_tree()) == {}


def test_the_discovered_gap_sites_are_the_upper_bound_exactly():
    """The derived surface is the whole bound, not merely inside it.

    The guard above bounds the discovered set from above, so a reach that
    stopped following an import could take gap sites off the scanned surface
    while it still holds.  Equality is what reds then.
    """
    assert gap_computation_sites(source_tree()).keys() == GAP_COMPUTATION_MODULES


def test_every_module_that_reads_the_change_stamp_is_registered_with_its_reason():
    """The stamp's readers are keyed on the stamp, not on the gap's answer.

    Every module that spells it is read off the tree and must be a row of the
    register, and every row must still spell it: the adapter that exposes it,
    the models that declare it, and the few services that keep it for a
    purpose of their own, none of them in the gap's reach.  A module that
    starts to spell the stamp reds here until its reason is written down
    beside the others.
    """
    readers = change_stamp_readers(source_tree())

    assert readers
    assert readers.keys() == CHANGE_STAMP_READERS.keys()
    assert readers.keys().isdisjoint(gap_computation_sites(source_tree()))


def change_stamp_values(relative, tree, namespace, node):
    """The stamp fields a definition reads by value: a name bound to one.

    Every loaded name and attribute inside *node*, resolved through the
    module's globals and its imports (``loaded_values``); one bound to a
    string that is a change-stamp field is a read of it, however the name is
    spelled — a constant in the module, or one imported from another.
    """
    return {
        value
        for value in loaded_values(relative, tree, namespace, node)
        if isinstance(value, str) and value in CHANGE_STAMP_FIELDS
    }


@functools.cache
def _arithmetic_sites_of(relative, source):
    """One module's call sites of the arithmetic, with what each reads."""
    tree = _parsed_once(source)
    namespace = module_namespace(relative, source)
    return tuple(
        (
            name,
            frozenset(
                change_stamp_reads(node)
                | change_stamp_values(relative, tree, namespace, node)
            ),
        )
        for name, node in referencing_definitions(
            relative, tree, namespace, wanted=gap_home_objects()
        )
    )


def arithmetic_call_sites(sources):
    """``(module, definition)`` -> what it reads of the stamp, per call site.

    A call site is a definition whose text refers to a gap home, or to a
    function of one, by object (``gap_home_objects``), called or not: under
    an aliased import, an import inside the function, a relative import, a
    module-level rebinding, a ``functools.partial``, a static method, loaded
    as a value and handed on, named by a string constant as ``module:attr``
    or ``module.attr``, taken as an attribute off a call handed a string
    naming its module or off a local assigned from one, or read as a literal
    name off the module through ``getattr``, ``operator.attrgetter``,
    ``vars`` or ``__dict__`` (``referencing_definitions``).  Each is scanned
    whole for the stamp's spelling (``change_stamp_reads``) and for a loaded
    name bound to a stamp field, in its module or through an import
    (``change_stamp_values``).  Outside the reach: a value handed across a
    function boundary, where the other function is not resolved at this site
    (returned from a helper, stored on an object and read elsewhere, or
    passed through a container built elsewhere); a name built at run time;
    and a binding made only when a function runs (``setattr`` or
    ``globals()`` inside a function body).  A read through a model property
    or method is one only running the site shows; the call sites the
    fixtures can run are run under the trap below.
    """
    return {
        (relative, name): reads
        for relative, source in sorted(sources.items())
        for name, reads in _arithmetic_sites_of(relative, source)
    }


def registered_readers_holding_a_call_site(sources):
    """The registered stamp readers that refer to the arithmetic by object."""
    return sorted(
        {module for module, _name in arithmetic_call_sites(sources)}
        & CHANGE_STAMP_READERS.keys()
    )


def test_no_call_site_of_the_arithmetic_found_by_object_reads_the_change_stamp():
    """The arithmetic's call sites are found by the objects, and read no stamp.

    Every call site is inside the gap's reach, which ties the two readings
    together: a site the reach misses reds here.  A string naming a function
    of a gap home, or its module, is an import edge of the reach as well as a
    reference, so the two readings see it alike.  No registered
    reader of the stamp is a call site: a module kept on the register for a
    reason of its own that starts to refer to the arithmetic reds, whatever
    it reads.
    """
    sites = arithmetic_call_sites(source_tree())

    assert len(gap_home_modules()) == len(gap_homes()) > 0
    assert set(gap_home_functions()) > set(GAP_ARITHMETIC)
    assert sites
    assert {site for site, reads in sites.items() if reads} == set()
    assert {module for module, _name in sites} <= GAP_COMPUTATION_MODULES
    assert registered_readers_holding_a_call_site(source_tree()) == []


#: Each way a definition refers to the arithmetic by object, as the module
#: text it arrives as.  ``{READ}`` is where a change-stamp read goes.
CALL_SITE_ROUTES = {
    "aliased_import": "from kodezart.domain.gap import compute_gap as gap_of\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return [c for c in gap_of(criteria){READ}]\n",
    "import_inside_the_function": "def plan(criteria, since):\n"
    "    from kodezart.domain.gap import compute_gap as _g\n"
    "\n"
    "    return [c for c in _g(criteria){READ}]\n",
    "module_alias_inside_the_function": "def plan(criteria, since):\n"
    "    import kodezart.domain.gap as gap_module\n"
    "\n"
    "    window = gap_module.compute_gap\n"
    "    return [c for c in window(criteria){READ}]\n",
    "dotted_route_inside_the_function": "def plan(criteria, since):\n"
    "    import kodezart.domain.gap\n"
    "\n"
    "    window = kodezart.domain.gap.compute_gap\n"
    "    return [c for c in window(criteria){READ}]\n",
    "relative_import_inside_the_function": "def plan(criteria, since):\n"
    "    from ..domain.gap import compute_gap as window\n"
    "\n"
    "    return [c for c in window(criteria){READ}]\n",
    "loaded_as_a_value_and_handed_on": "from kodezart.domain import gap\n"
    "\n"
    "def plan(criteria, since, run):\n"
    "    return [c for c in run(gap.compute_gap, criteria){READ}]\n",
    "module_level_rebinding": "from kodezart.domain.gap import compute_gap\n"
    "\n"
    "_ARITHMETIC = compute_gap\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return [c for c in _ARITHMETIC(criteria){READ}]\n",
    "module_level_partial": "import functools\n"
    "\n"
    "from kodezart.domain.gap import compute_gap\n"
    "\n"
    "_WINDOW = functools.partial(compute_gap)\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return [c for c in _WINDOW(criteria){READ}]\n",
    "static_method": "from kodezart.domain.gap import compute_gap\n"
    "\n"
    "class Arithmetic:\n"
    "    window = staticmethod(compute_gap)\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return [c for c in Arithmetic.window(criteria){READ}]\n",
    "named_as_module_colon_attr": "import pkgutil\n"
    "\n"
    "def plan(criteria, since):\n"
    "    window = pkgutil.resolve_name('kodezart.domain.gap:compute_gap')\n"
    "    return [c for c in window(criteria){READ}]\n",
    "named_as_module_dot_attr": "import pkgutil\n"
    "\n"
    "def plan(criteria, since):\n"
    "    window = pkgutil.resolve_name('kodezart.domain.gap.compute_gap')\n"
    "    return [c for c in window(criteria){READ}]\n",
    "predicate_named_as_module_colon_attr": "import pkgutil\n"
    "\n"
    "def plan(criteria, since):\n"
    "    still_open = pkgutil.resolve_name('kodezart.domain.gap:open_state_kind')\n"
    "    return [\n"
    "        c for c in criteria if still_open(c.state_kind){READ}\n"
    "    ]\n",
    "attribute_off_the_resolving_call": "import pkgutil\n"
    "\n"
    "def plan(criteria, since):\n"
    "    window = pkgutil.resolve_name('kodezart.domain:gap').compute_gap\n"
    "    return [c for c in window(criteria){READ}]\n",
    "membership_named_as_module_dot_attr": "import pkgutil\n"
    "\n"
    "def plan(criteria, since):\n"
    "    owed = pkgutil.resolve_name('kodezart.domain.gap.gap_membership')\n"
    "    return [c for c in criteria if owed(c){READ}]\n",
    "local_bound_from_resolve_name": "import pkgutil\n"
    "\n"
    "def plan(criteria, since):\n"
    "    arithmetic = pkgutil.resolve_name('kodezart.domain:gap')\n"
    "    return [\n"
    "        c for c in arithmetic.compute_gap(criteria){READ}\n"
    "    ]\n",
    "local_bound_from_import_module": "import importlib\n"
    "\n"
    "def plan(criteria, since):\n"
    "    arithmetic = importlib.import_module('kodezart.domain.gap')\n"
    "    return [\n"
    "        c for c in arithmetic.compute_gap(criteria){READ}\n"
    "    ]\n",
    "getattr_on_the_home_module": "def plan(criteria, since):\n"
    "    from kodezart.domain import gap\n"
    "\n"
    "    window = getattr(gap, 'compute_gap')\n"
    "    return [c for c in window(criteria){READ}]\n",
    "attrgetter_applied_to_the_home_module": "import operator\n"
    "\n"
    "from kodezart.domain import gap\n"
    "\n"
    "def plan(criteria, since):\n"
    "    window = operator.attrgetter('compute_gap')(gap)\n"
    "    return [c for c in window(criteria){READ}]\n",
    "vars_of_the_home_module": "from kodezart.domain import gap\n"
    "\n"
    "def plan(criteria, since):\n"
    "    window = vars(gap)['compute_gap']\n"
    "    return [c for c in window(criteria){READ}]\n",
    "dict_of_the_home_module": "from kodezart.domain import gap\n"
    "\n"
    "def plan(criteria, since):\n"
    "    window = gap.__dict__['compute_gap']\n"
    "    return [c for c in window(criteria){READ}]\n",
    "home_module_bound_to_a_local_and_handed_on": "from kodezart.domain import gap\n"
    "\n"
    "def plan(criteria, since, run):\n"
    "    arithmetic = gap\n"
    "    return [c for c in run(arithmetic, criteria){READ}]\n",
    "home_named_by_a_string_and_handed_on": "import pkgutil\n"
    "\n"
    "def plan(criteria, since, run):\n"
    "    home = pkgutil.resolve_name('kodezart.domain:gap')\n"
    "    return [c for c in run(home, criteria){READ}]\n",
}
#: The module every call-site route is planted into: inside the gap's reach
#: already and not on the stamp register, so the route alone is what makes
#: the planted definition a site, never a new import edge.
CALL_SITE_HOST = "services/run_shape.py"


@pytest.mark.parametrize("reads", [True, False])
@pytest.mark.parametrize("route", sorted(CALL_SITE_ROUTES))
def test_a_call_site_of_the_arithmetic_is_found_by_object_and_scanned(route, reads):
    """A definition referring to the arithmetic by any route is a scanned site.

    One row per route, each with and without the read, appended to a module
    already in the reach.  The read-free rows redden the moment resolution
    stops following that route, because the planted definition drops out of
    the sites; the reading rows redden the scan.  An import at the top of a
    module binds its name in the module's globals, which the module-level
    rebinding row reads; the rows importing inside the function are what
    reads an import the globals never hold, relative, aliased or dotted.  The
    partial and static-method rows are what unwraps a stand-in, the string
    rows what resolves a named object, the two local rows what follows a
    local assigned from a resolving call, the four literal-name rows what
    reads ``getattr``, ``attrgetter``, ``vars`` and ``__dict__`` off the
    home module, and the two handed-on rows what makes a home module itself
    a referent: they denote no function of it.
    """
    assert CALL_SITE_HOST in GAP_COMPUTATION_MODULES
    assert CALL_SITE_HOST not in CHANGE_STAMP_READERS
    planted = CALL_SITE_ROUTES[route].replace(
        "{READ}", " if c.updated_at > since" if reads else ""
    )
    sources = source_tree()
    sources[CALL_SITE_HOST] += "\n\n" + planted
    sites = arithmetic_call_sites(sources)

    assert sites[(CALL_SITE_HOST, "plan")] == (
        frozenset({"updated_at"}) if reads else frozenset()
    )


#: Each shape outside the reach, as the text it would arrive as in
#: ``CALL_SITE_HOST``, with the definitions in it that are call sites: a value
#: handed across a function boundary (an argument, a helper's answer, an
#: attribute of an instance, a container built elsewhere), a name built at run
#: time, and a binding made only when a function runs.  ``plan`` reaches the
#: arithmetic only across the boundary, so it is no site; the definition that
#: binds the arithmetic, where the text has one, is.
UNSEEN_CALL_SITE_SHAPES = {
    "an argument": (
        "def plan(criteria, since, window):\n"
        "    return [c for c in window(criteria){READ}]\n",
        (),
    ),
    "returned from a helper": (
        "from kodezart.domain import gap\n"
        "\n"
        "def arithmetic():\n"
        "    return gap.compute_gap\n"
        "\n"
        "def plan(criteria, since):\n"
        "    return [c for c in arithmetic()(criteria){READ}]\n",
        ("arithmetic",),
    ),
    "stored on an object and read elsewhere": (
        "from kodezart.domain import gap\n"
        "\n"
        "class Holder:\n"
        "    def __init__(self):\n"
        "        self.window = gap.compute_gap\n"
        "\n"
        "def plan(criteria, since, holder):\n"
        "    return [c for c in holder.window(criteria){READ}]\n",
        ("Holder.__init__",),
    ),
    "passed through a container built elsewhere": (
        "from kodezart.domain import gap\n"
        "\n"
        "def table():\n"
        "    return {'window': gap.compute_gap}\n"
        "\n"
        "def plan(criteria, since):\n"
        "    return [\n"
        "        c for c in table()['window'](criteria){READ}\n"
        "    ]\n",
        ("table",),
    ),
    "a name built at run time": (
        "import pkgutil\n"
        "\n"
        "def plan(criteria, since):\n"
        "    home = pkgutil.resolve_name('kodezart.domain' + ':gap')\n"
        "    return [\n"
        "        c for c in home.compute_gap(criteria){READ}\n"
        "    ]\n",
        (),
    ),
    "globals() bound inside a function": (
        "from kodezart.domain import gap\n"
        "\n"
        "def bind():\n"
        "    globals()['window'] = gap.compute_gap\n"
        "\n"
        "def plan(criteria, since):\n"
        "    return [c for c in window(criteria){READ}]\n",
        ("bind",),
    ),
    "setattr inside a function": (
        "import sys\n"
        "\n"
        "from kodezart.domain import gap\n"
        "\n"
        "def bind():\n"
        "    setattr(sys.modules[__name__], 'window', gap.compute_gap)\n"
        "\n"
        "def plan(criteria, since):\n"
        "    return [c for c in window(criteria){READ}]\n",
        ("bind",),
    ),
}


@pytest.mark.parametrize("shape", sorted(UNSEEN_CALL_SITE_SHAPES))
def test_a_shape_outside_the_reach_is_no_call_site(shape):
    """The stated limit, held: ``plan`` is no call site in any shape of it.

    Planted with a stamp read, which the scan therefore never sees: this is
    the limit the module docstring states, and the trap below is where such
    a read dies when the function is one the trap runs.  Where the text
    binds the arithmetic elsewhere — in the helper, the holder, the table or
    the binder — that definition is the call site, so the plant is real and
    the boundary, not the module, is what hides ``plan``.
    """
    text, sites_in_it = UNSEEN_CALL_SITE_SHAPES[shape]
    planted = text.replace("{READ}", " if c.updated_at > since")
    sources = source_tree()
    before = {
        name
        for module, name in arithmetic_call_sites(sources)
        if module == CALL_SITE_HOST
    }
    sources[CALL_SITE_HOST] += "\n\n" + planted
    sites = arithmetic_call_sites(sources)

    assert (CALL_SITE_HOST, "plan") not in sites
    assert {name for module, name in sites if module == CALL_SITE_HOST} - before == set(
        sites_in_it
    )


#: Each way a registered reader can name a function of a gap home without
#: importing it: the text that binds ``window`` inside the planted function.
REGISTERED_READER_REFERENCES = {
    "compute_gap as module:attr": "pkgutil.resolve_name("
    "'kodezart.domain.gap:compute_gap')",
    "gap_membership as module:attr": "pkgutil.resolve_name("
    "'kodezart.domain.gap:gap_membership')",
    "state_membership as module.attr": "pkgutil.resolve_name("
    "'kodezart.domain.gap.state_membership')",
    "attribute off the call naming its module": "pkgutil.resolve_name("
    "'kodezart.domain:gap').compute_gap",
}


@pytest.mark.parametrize("reference", sorted(REGISTERED_READER_REFERENCES))
def test_a_registered_reader_that_refers_to_the_arithmetic_is_a_call_site(reference):
    """A module on the register reds the moment it refers to the arithmetic.

    The planted function names a function of a gap home by a string
    constant, so it imports nothing, and it sits in a module that already
    spells the stamp, so the register's keys do not move.  Found by object,
    it is a call site, which a registered reader may not hold; and the
    string names the home's module, so the reach takes the reader in too.
    """
    sources = source_tree()
    sources["services/pass_gate.py"] += (
        "\n\n"
        "def recent_gap(criteria, since):\n"
        "    import pkgutil\n"
        "\n"
        f"    window = {REGISTERED_READER_REFERENCES[reference]}\n"
        "    return [c for c in criteria if window and c.updated_at > since]\n"
    )

    assert registered_readers_holding_a_call_site(sources) == ["services/pass_gate.py"]
    assert arithmetic_call_sites(sources)[
        ("services/pass_gate.py", "recent_gap")
    ] == frozenset({"updated_at"})
    assert "services/pass_gate.py" in gap_computation_sites(sources)


#: Each way a call site reads a stamp field through a name bound to it, as
#: the definition text; ``{FIELD}`` is where a constant is named, and the
#: rows' second text is the same definition naming a field that is no stamp.
STAMP_VALUE_ROUTES = {
    "constant in the module": (
        "STAMP_FIELD = 'updated_at'\n"
        "\n"
        "def plan(criteria):\n"
        "    return sorted(\n"
        "        compute_gap(criteria),\n"
        "        key=lambda c: getattr(c, STAMP_FIELD),\n"
        "    )\n",
        "updated_at",
    ),
    "constant imported inside the function": (
        "def plan(criteria):\n"
        "    from kodezart.adapters.linear.tracker import _ORDER_BY_UPDATED_AT\n"
        "\n"
        "    return [\n"
        "        getattr(c, _ORDER_BY_UPDATED_AT)\n"
        "        for c in compute_gap(criteria)\n"
        "    ]\n",
        "updatedAt",
    ),
    "attribute of an imported module": (
        "import kodezart.adapters.linear.tracker as wire\n"
        "\n"
        "def plan(criteria):\n"
        "    return [\n"
        "        getattr(c, wire._ORDER_BY_UPDATED_AT)\n"
        "        for c in compute_gap(criteria)\n"
        "    ]\n",
        "updatedAt",
    ),
}


@pytest.mark.parametrize("route", sorted(STAMP_VALUE_ROUTES))
def test_a_call_site_reading_the_stamp_through_a_bound_name_reads_it(route):
    """A name bound to a stamp field is a read of the field, by its value.

    Planted beside an import of the arithmetic, so the definition is a call
    site; its own text never spells the stamp.  The same definition with
    the name bound to a field that is no stamp reads nothing.
    """
    text, field = STAMP_VALUE_ROUTES[route]
    header = "from kodezart.domain.gap import compute_gap\n\n"
    other = text.replace("'updated_at'", "'body_digest'").replace(
        "_ORDER_BY_UPDATED_AT", "_ORDER_BY_BODY"
    )
    planted = {**source_tree(), "services/planted.py": header + text}
    control = {**source_tree(), "services/planted.py": header + other}

    definition = next(
        node for node in ast.walk(ast.parse(text)) if isinstance(node, ast.FunctionDef)
    )
    assert change_stamp_reads(definition) == set()
    assert arithmetic_call_sites(planted)[("services/planted.py", "plan")] == {field}
    assert arithmetic_call_sites(control)[("services/planted.py", "plan")] == set()


class ChangeStampRead(BaseException):
    """An operation on a trapped change stamp: the arithmetic read it.

    Not an ``Exception``, so no ``except Exception`` in the arithmetic can
    turn a read into an answer.
    """


#: Every operation a value can be put to that could let it decide an answer.
TRAPPED_OPERATIONS = (
    "__eq__",
    "__ne__",
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "__hash__",
    "__bool__",
    "__str__",
    "__repr__",
    "__format__",
    "__add__",
    "__radd__",
    "__sub__",
    "__rsub__",
    "__int__",
    "__float__",
    "__index__",
    "__len__",
    "__iter__",
    "__contains__",
    "__getitem__",
    "__call__",
    "__getattribute__",
)


def change_stamp_trap(reads):
    """A change stamp whose every operation is recorded in *reads* and raises.

    Comparison, hashing, truth, text, arithmetic, attribute access: each one
    appends its name and raises ``ChangeStampRead``.  The record is kept even
    when something swallows the raise.
    """

    def sprung(operation):
        def operate(*_arguments):
            reads.append(operation)
            raise ChangeStampRead(operation)

        return operate

    trap = type(
        "ChangeStampTrap",
        (),
        {operation: sprung(operation) for operation in TRAPPED_OPERATIONS},
    )
    return trap()


def arithmetic_cases(stamp):
    """Each entry point of the arithmetic over boards reaching every arm it has.

    ``case -> (entry point, keyword arguments, records)``; every record's
    change stamp is ``stamp(n)`` for the n-th record the case builds, and an
    answer is read as the positions of its records among *records*, or as
    itself when it is a truth value, a membership or ``None``.  Every function of
    a gap home has a case here, so the trap runs each one on its own.

    The arms reached.  ``compute_gap`` and the ``gap_membership`` it runs:
    every workflow state kind, each plain and carrying a supersession note
    the arithmetic does not read, so an open criterion, a completed one, and
    a canceled and a duplicate one, each excluded on its state alone; an
    empty board; its two refusals — a criterion twice, a record that is no
    criterion; and a criterion carrying a blank supersession note, which
    refuses nothing because the arithmetic takes no supersession input.
    ``gap_membership`` on its own: an open criterion, a completed one, a
    canceled one with and without a note, its refusal of a record that is no
    criterion, and an open one carrying a blank note.  ``state_membership``
    takes a workflow kind and no record: every kind.
    ``open_state_kind`` takes a workflow kind and no record either: the kind
    of every criterion above, plain and carrying a note, which answers alike,
    and the kind of one carrying a blank note.
    """
    built = iter(range(1_000))

    def record(key, **changes):
        fixture = issue(key, f"Body for {key}", **changes)
        return fixture.model_copy(update={"updated_at": stamp(next(built))})

    def criterion(key, kind, parent=SUBJECT):
        return record(
            key,
            parent_key=parent,
            issue_labels=["criterion"],
            state_kind=kind,
            state_name=kind.value,
        )

    def noted(each, successor="superseding/1"):
        """*each* carrying a supersession note in its body and as a label."""
        return each.model_copy(
            update={
                "body": f"{each.body}\n\nSuperseded by {successor}.",
                "issue_labels": each.issue_labels | {"superseded"},
            }
        )

    kinds = [
        each
        for kind in WorkflowStateKind
        for each in (
            criterion(f"criterion/{kind.value}", kind),
            noted(criterion(f"criterion/{kind.value}/superseded", kind)),
        )
    ]
    blank_note = noted(criterion("criterion/blank-note", WorkflowStateKind.TRIAGE), " ")

    def subtree(criteria):
        return (compute_gap, {"criteria": criteria}, criteria)

    open_criterion = criterion("member/open", WorkflowStateKind.UNSTARTED)
    completed = criterion("member/completed", WorkflowStateKind.COMPLETED)
    canceled = criterion("member/canceled", WorkflowStateKind.CANCELED)
    plain = record("member/plain")

    def membership(each):
        return gap_membership, {"criterion": each}, (each,)

    return {
        "subtree gap: every state kind": subtree(kinds),
        "subtree gap: empty board": subtree(()),
        "subtree gap refuses a criterion twice": subtree((kinds[0], kinds[0])),
        "subtree gap refuses a record that is no criterion": subtree(
            (record("plain/1"),)
        ),
        "subtree gap takes no supersession input: a blank note refuses nothing": (
            subtree((blank_note,))
        ),
        "gap_membership: an open criterion": membership(open_criterion),
        "gap_membership: a completed criterion": membership(completed),
        "gap_membership: a canceled criterion superseded": membership(
            noted(canceled, "over/1")
        ),
        "gap_membership: a canceled criterion unsuperseded": membership(canceled),
        "gap_membership refuses a record that is no criterion": membership(plain),
        "gap_membership takes no supersession input: a blank note": membership(
            noted(open_criterion, " ")
        ),
        **{
            f"state_membership: {kind.value}": (
                state_membership,
                {"state_kind": kind},
                (),
            )
            for kind in WorkflowStateKind
        },
        **{
            "open_state_kind: "
            + each.state_kind.value
            + (" superseded" if "superseded" in each.issue_labels else ""): (
                open_state_kind,
                {"state_kind": each.state_kind},
                (each,),
            )
            for each in kinds
        },
        "open_state_kind takes no supersession input: a blank note": (
            open_state_kind,
            {"state_kind": blank_note.state_kind},
            (blank_note,),
        ),
    }


def arithmetic_outcome(entry, arguments, records):
    """What one entry point answers: its records by position, or its refusal.

    A record answered by its key is read at the position of the record
    carrying that key; a gap read is read as its two halves, the owed records
    and the keys set aside beside them; a truth value, a membership and
    ``None`` are read as themselves; a subtree read refuses as a scope
    read error.
    """
    try:
        answer = entry(**arguments)
    except (ValueError, ScopeReadError) as refusal:
        return ("refused", str(refusal))
    if isinstance(answer, bool | str) or answer is None:
        return ("answered", answer)

    def positions(items):
        return tuple(
            position
            for item in items
            for position, each in enumerate(records)
            if each is item or (isinstance(item, str) and each.issue_key == item)
        )

    if isinstance(answer, CriterionGap):
        return (
            "answered",
            {"owed": positions(answer.owed), "excluded": positions(answer.excluded)},
        )
    return ("answered", positions(answer))


#: The stamp every record carries in the baseline reading.
BASELINE_STAMP = datetime(2026, 1, 1, tzinfo=UTC)
#: What each case answers over records carrying ``BASELINE_STAMP``.  Written
#: out, so a board that stops reaching the arm it was built for reds.
ARITHMETIC_OUTCOMES = {
    "subtree gap: every state kind": (
        "answered",
        {"owed": (0, 1, 2, 3, 4, 5, 6, 7), "excluded": (10, 11, 12, 13)},
    ),
    "subtree gap: empty board": ("answered", {"owed": (), "excluded": ()}),
    "subtree gap refuses a criterion twice": (
        "refused",
        "a criterion identity appears more than once",
    ),
    "subtree gap refuses a record that is no criterion": (
        "refused",
        "gap membership requires a criterion sub-issue",
    ),
    "subtree gap takes no supersession input: a blank note refuses nothing": (
        "answered",
        {"owed": (0,), "excluded": ()},
    ),
    "gap_membership: an open criterion": ("answered", GapMembership.OWED),
    "gap_membership: a completed criterion": ("answered", GapMembership.DISCHARGED),
    "gap_membership: a canceled criterion superseded": (
        "answered",
        GapMembership.EXCLUDED,
    ),
    "gap_membership: a canceled criterion unsuperseded": (
        "answered",
        GapMembership.EXCLUDED,
    ),
    "gap_membership refuses a record that is no criterion": (
        "refused",
        "gap membership requires a criterion sub-issue",
    ),
    "gap_membership takes no supersession input: a blank note": (
        "answered",
        GapMembership.OWED,
    ),
    "state_membership: triage": ("answered", GapMembership.OWED),
    "state_membership: backlog": ("answered", GapMembership.OWED),
    "state_membership: unstarted": ("answered", GapMembership.OWED),
    "state_membership: started": ("answered", GapMembership.OWED),
    "state_membership: completed": ("answered", GapMembership.DISCHARGED),
    "state_membership: canceled": ("answered", GapMembership.EXCLUDED),
    "state_membership: duplicate": ("answered", GapMembership.EXCLUDED),
    "open_state_kind: triage": ("answered", True),
    "open_state_kind: triage superseded": ("answered", True),
    "open_state_kind: backlog": ("answered", True),
    "open_state_kind: backlog superseded": ("answered", True),
    "open_state_kind: unstarted": ("answered", True),
    "open_state_kind: unstarted superseded": ("answered", True),
    "open_state_kind: started": ("answered", True),
    "open_state_kind: started superseded": ("answered", True),
    "open_state_kind: completed": ("answered", False),
    "open_state_kind: completed superseded": ("answered", False),
    "open_state_kind: canceled": ("answered", False),
    "open_state_kind: canceled superseded": ("answered", False),
    "open_state_kind: duplicate": ("answered", False),
    "open_state_kind: duplicate superseded": ("answered", False),
    "open_state_kind takes no supersession input: a blank note": ("answered", True),
}
#: Each change stamp the records are read under besides the baseline: a trap
#: that raises on any operation, and two readings far apart whose order runs
#: opposite ways across the records, so a filter or a sort keyed on the stamp
#: answers differently under one of them.
STAMP_VARIANTS = ("trapped", "far past, ascending", "far future, descending")


def stamp_variant(variant, reads):
    """The stamp each record carries under *variant*, by the order it is built."""
    if variant == "trapped":
        trap = change_stamp_trap(reads)
        return lambda _position: trap
    if variant == "far past, ascending":
        return lambda position: datetime(1, 1, 1, tzinfo=UTC) + timedelta(days=position)
    return lambda position: (
        datetime(9999, 12, 31, tzinfo=UTC) - timedelta(days=position)
    )


def arithmetic_entry_points():
    """Every function of a gap home, and every definition there referring to one.

    Derived from ``gap_home_functions``, the way the call sites are, so a
    function added beside the arithmetic — one that hands on its answer, or
    one that reads a record on its own — is an
    entry point the trap has to run, whether or not the arithmetic runs it.
    """
    points = {id(value): value for value in gap_home_functions()}
    for home in sorted(gap_homes()):
        source = source_tree()[home]
        namespace = module_namespace(home, source)
        for name, _node in referencing_definitions(
            home, ast.parse(source), namespace, wanted=gap_home_functions()
        ):
            if name == "<module>":
                continue
            value = functools.reduce(
                getattr, name.split(".")[1:], namespace[name.split(".")[0]]
            )
            points[id(value)] = value
    return points


def test_the_trapped_boards_run_every_entry_point_and_reach_every_arm():
    """The boards the trap runs over are the ones the arithmetic answers.

    Every entry point is run, and nothing else is, so a function added to a
    gap home reds here until it has a case; each case answers what it was
    built for over the baseline stamp, so a board that stops reaching its
    arm — a refusal where an answer was meant, an empty gap where a member
    was — reds here rather than turning the trap into a vacuous pass.
    """
    points = arithmetic_entry_points()
    cases = arithmetic_cases(lambda _position: BASELINE_STAMP)

    assert points.keys() > {id(value) for value in GAP_ARITHMETIC}
    assert {id(entry) for entry, _arguments, _records in cases.values()} == (
        points.keys()
    )
    assert {
        case: arithmetic_outcome(*arguments) for case, arguments in cases.items()
    } == ARITHMETIC_OUTCOMES


@pytest.mark.parametrize("variant", STAMP_VARIANTS)
@pytest.mark.parametrize("case", sorted(ARITHMETIC_OUTCOMES))
def test_the_arithmetic_answers_alike_whatever_the_change_stamp_holds(case, variant):
    """The arithmetic runs with the change stamp trapped, and never reads it.

    The reach of the Check, shown by running it: every function of a gap
    home, together with everything it executes — a model method, a helper in
    a module it imports, a class pattern — answers every case the same with
    the stamp trapped, far in the past and far in the future, and no
    operation touches the trap.  An identity test against the trap (``is``)
    is no operation it can see, and cannot move an answer either.
    """
    reads = []
    entry, arguments, records = arithmetic_cases(stamp_variant(variant, reads))[case]

    assert arithmetic_outcome(entry, arguments, records) == ARITHMETIC_OUTCOMES[case]
    assert reads == []


def call_site_cases(stamp):
    """Each call site found by object that runs on the arithmetic's fixtures.

    ``case -> (call site, keyword arguments, records)``, read as
    ``arithmetic_cases`` is, every record's change stamp ``stamp(n)``.  The
    arms reached.  ``SubtreeClosure._walk``, the criterion leaf of the
    subtree gap, walking a subject over a criterion of every state kind, two
    of each so an order the stamp imposes shows.  ``SubtreeClosure.scope_gap``
    over open and completed criteria, over a subject with no criterion, over
    a criterion of every state kind, and over canceled and duplicate criteria
    alone, which are excluded on their state and named, never refused.
    """
    built = iter(range(1_000))

    def record(key, **changes):
        fixture = issue(key, f"Body for {key}", **changes)
        return fixture.model_copy(update={"updated_at": stamp(next(built))})

    kinds = tuple(
        record(
            f"criterion/{kind.value}/{copy}",
            parent_key=SUBJECT,
            issue_labels=["criterion"],
            state_kind=kind,
            state_name=kind.value,
        )
        for kind in WorkflowStateKind
        for copy in (1, 2)
    )
    settled = tuple(
        each
        for each in kinds
        if each.state_kind
        not in {WorkflowStateKind.CANCELED, WorkflowStateKind.DUPLICATE}
    )
    excluded = tuple(each for each in kinds if each not in settled)
    subject = record(SUBJECT)
    ref = ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT)

    def closure(criteria):
        return SubtreeClosure(
            facts={each.issue_key: each for each in (subject, *criteria)}, ref=ref
        )

    def scope_gap(criteria):
        return (
            SubtreeClosure.scope_gap,
            {"self": closure(criteria)},
            (subject, *criteria),
        )

    return {
        "SubtreeClosure._walk: every state kind": (
            SubtreeClosure._walk,
            {"self": closure(kinds), "key": SUBJECT},
            (subject, *kinds),
        ),
        "SubtreeClosure.scope_gap: open and completed criteria": scope_gap(settled),
        "SubtreeClosure.scope_gap: an empty family": scope_gap(()),
        "SubtreeClosure.scope_gap: canceled and duplicate criteria alone": (
            scope_gap(excluded)
        ),
        "SubtreeClosure.scope_gap: every state kind": scope_gap(kinds),
    }


#: What each call-site case answers over records carrying ``BASELINE_STAMP``,
#: written out as ``ARITHMETIC_OUTCOMES`` is.
CALL_SITE_OUTCOMES = {
    "SubtreeClosure._walk: every state kind": ("answered", None),
    "SubtreeClosure.scope_gap: open and completed criteria": (
        "answered",
        {"owed": (1, 2, 3, 4, 5, 6, 7, 8), "excluded": ()},
    ),
    "SubtreeClosure.scope_gap: an empty family": (
        "answered",
        {"owed": (), "excluded": ()},
    ),
    "SubtreeClosure.scope_gap: canceled and duplicate criteria alone": (
        "answered",
        {"owed": (), "excluded": (1, 2, 3, 4)},
    ),
    "SubtreeClosure.scope_gap: every state kind": (
        "answered",
        {"owed": (1, 2, 3, 4, 5, 6, 7, 8), "excluded": (11, 12, 13, 14)},
    ),
}
#: The call sites found by object that the arithmetic's fixtures cannot run,
#: each with why; the trap does not reach them, and the call-site scan above
#: is what reads them.
CALL_SITES_NOT_RUN = {
    ("chains/criteria.py", "TrackerCriteria._finished"): "A method of the "
    "criteria reader, run on a reading its tracker port took; it hands the "
    "family to compute_gap and each record to gap_membership, which the trap "
    "runs as entry points.",
    ("domain/stream_signals.py", "lapse_undischarged"): "Folds alarm "
    "readings, a workflow kind among them, and takes no tracker record, so no "
    "record's change stamp reaches it for the trap to hold.",
    ("services/mandate_graph.py", "observe_ruling_growth"): "Async; reads "
    "criteria and ruling projections through the tracker port.",
    ("services/audit_terminal.py", "AuditTerminalReader.observe"): "Async; a "
    "method of the audit terminal reader, reading the issue and its criterion "
    "family through the tracker port and the branch and pull request through "
    "the git and forge ports.",
    ("services/run_shape.py", "read_barren_tick"): "Async; reads criteria "
    "through the tracker port.",
}


def call_site_objects(sources):
    """Each call site found by object, as the object its module binds it to."""
    found = {}
    for module, name in arithmetic_call_sites(sources):
        head, *rest = name.split(".")
        found[(module, name)] = functools.reduce(
            lambda value, part: getattr(value, part, None),
            rest,
            module_namespace(module, sources[module]).get(head),
        )
    return found


def test_every_call_site_the_fixtures_can_run_is_run_under_the_trap():
    """The call sites outside the entry points are run where the fixtures can run them.

    Every call site found by object that is not an entry point the trap
    already runs is either a case of ``call_site_cases`` —
    ``SubtreeClosure._walk`` and ``SubtreeClosure.scope_gap`` — or named in
    ``CALL_SITES_NOT_RUN`` with why: the criteria reader's ``_finished``,
    ``observe_ruling_growth``, ``read_barren_tick`` and the audit terminal
    reader's ``observe``, each of which needs a tracker port, a service
    instance or the composition's wiring; and
    ``lapse_undischarged``, which takes alarm readings and no tracker
    record.  A new call site reds here until it is one or the other.  Each
    case answers what it was built for over the baseline stamp.
    """
    points = arithmetic_entry_points()
    cases = call_site_cases(lambda _position: BASELINE_STAMP)
    run = {id(entry) for entry, _arguments, _records in cases.values()}
    sites = call_site_objects(source_tree())
    outside = {site for site, value in sites.items() if id(value) not in points}

    assert run <= {id(sites[site]) for site in outside}
    assert {site for site in outside if id(sites[site]) not in run} == (
        CALL_SITES_NOT_RUN.keys()
    )
    assert {
        case: arithmetic_outcome(*arguments) for case, arguments in cases.items()
    } == CALL_SITE_OUTCOMES


@pytest.mark.parametrize("variant", STAMP_VARIANTS)
@pytest.mark.parametrize("case", sorted(CALL_SITE_OUTCOMES))
def test_a_call_site_answers_alike_whatever_the_change_stamp_holds(case, variant):
    """A call site the fixtures can run never reads the change stamp.

    Run the way the arithmetic is: with the stamp trapped, far in the past
    and far in the future, each answers the same and no operation touches
    the trap.
    """
    reads = []
    entry, arguments, records = call_site_cases(stamp_variant(variant, reads))[case]

    assert arithmetic_outcome(entry, arguments, records) == CALL_SITE_OUTCOMES[case]
    assert reads == []


@pytest.mark.parametrize(
    ("relative", "anchor", "planted"),
    [
        (
            "domain/gap.py",
            "    return CriterionGap(\n",
            "    if any(criterion.updated_at for criterion in criteria):\n"
            '        raise ValueError("a criterion changed")\n'
            "    return CriterionGap(\n",
        ),
        (
            "services/run_shape.py",
            "    open_keys = {criterion.issue_key",
            "    if any(criterion.updated_at for criterion in criteria):\n"
            '        raise ValueError("a criterion changed")\n'
            "    open_keys = {criterion.issue_key",
        ),
    ],
)
def test_the_guard_reddens_when_a_discovered_gap_site_reads_the_field(
    relative, anchor, planted
):
    sources = source_tree()
    assert sources[relative].count(anchor) == 1
    sources[relative] = sources[relative].replace(anchor, planted)
    assert gap_sites_reading_the_change_stamp(sources) == {relative: {"updated_at"}}


#: Each static way a module can reach the arithmetic's module, as the module
#: text it arrives as.  ``{READ}`` is where a change-stamp read goes.  The
#: two-hop row reaches it through a planted helper module that imports it,
#: so the reach is pinned as a closure and not as one import deep.
IMPORT_ROUTES = {
    "aliased_import": "from kodezart.domain.gap import compute_gap as gap_of\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return gap_of([c for c in criteria{READ}])\n",
    "module_attribute": "import kodezart.domain.gap as gap_module\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return gap_module.compute_gap([c for c in criteria{READ}])\n",
    "import_inside_the_function": "def plan(criteria, since):\n"
    "    from kodezart.domain.gap import compute_gap as _g\n"
    "\n"
    "    return _g([c for c in criteria{READ}])\n",
    "relative_import": "from ..domain.gap import compute_gap\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return compute_gap([c for c in criteria{READ}])\n",
    "submodule_from_its_package": "from kodezart.domain import gap\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return gap.compute_gap([c for c in criteria{READ}])\n",
    "route_through_the_package": "import kodezart\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return kodezart.domain.gap.compute_gap([c for c in criteria{READ}])\n",
    "route_through_an_imported_package": "from kodezart import domain\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return domain.gap.compute_gap([c for c in criteria{READ}])\n",
    "type_checking_import": "from typing import TYPE_CHECKING\n"
    "\n"
    "if TYPE_CHECKING:\n"
    "    from kodezart.domain.issue_tree import SubtreeClosure\n"
    "\n"
    "def plan(closure: 'SubtreeClosure', since):\n"
    "    return [c for c in closure.scope_gap().owed{READ}]\n",
    "module_named_by_a_string": "import importlib\n"
    "\n"
    "def plan(criteria, since):\n"
    "    gap = importlib.import_module('kodezart.domain.gap')\n"
    "    return gap.compute_gap([c for c in criteria{READ}])\n",
    "module_named_as_package_colon_module": "import pkgutil\n"
    "\n"
    "def plan(criteria, since):\n"
    "    arithmetic = pkgutil.resolve_name('kodezart.domain:gap')\n"
    "    return arithmetic.compute_gap([c for c in criteria{READ}])\n",
    "object_named_as_module_colon_attr": "import pkgutil\n"
    "\n"
    "def plan(criteria, since):\n"
    "    still_open = pkgutil.resolve_name('kodezart.domain.gap:open_state_kind')\n"
    "    return [c for c in criteria if still_open(c.state_kind){READ}]\n",
    "two_hops": "from kodezart.services.planted_helper import window\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return [c for c in window(criteria){READ}]\n",
    "relative_import_in_a_package_init": "from kodezart.domain.planted import window\n"
    "\n"
    "def plan(criteria, since):\n"
    "    return [c for c in window(criteria){READ}]\n",
}
#: The modules the routes above import that are not in the tree: the helper
#: the two-hop row imports, a module of its own that reaches the arithmetic
#: and reads nothing, and a package whose ``__init__`` re-exports the
#: arithmetic through a relative import, resolved against the package itself.
PLANTED_HELPERS = {
    "services/planted_helper.py": "from kodezart.domain.issue_tree import"
    " SubtreeClosure\n"
    "\n"
    "def window(closure: SubtreeClosure):\n"
    "    return closure.scope_gap().owed\n",
    "domain/planted/__init__.py": "from ..gap import compute_gap as window\n",
}


@pytest.mark.parametrize("reads", [True, False])
@pytest.mark.parametrize("route", sorted(IMPORT_ROUTES))
def test_a_gap_site_reached_under_another_spelling_is_discovered_and_scanned(
    route, reads
):
    """A module that reaches the arithmetic by any static route is a gap site.

    One row per route ``imported_modules`` reads, each with and without the
    read.  The read-free rows redden the moment the reach stops following
    that route, because the planted module drops out of the discovered set;
    the reading rows redden the guard itself.
    """
    planted = IMPORT_ROUTES[route].replace(
        "{READ}", " if c.updated_at > since" if reads else ""
    )
    sources = {**source_tree(), **PLANTED_HELPERS, "services/planted.py": planted}

    assert "services/planted.py" in gap_computation_sites(sources)
    assert gap_sites_reading_the_change_stamp(sources) == (
        {"services/planted.py": {"updated_at"}} if reads else {}
    )


def test_a_module_that_only_quotes_a_seed_name_is_not_a_gap_site():
    """A quoted arithmetic name is a value, and an import runs one way.

    What bounds the reach from above: a module whose only mention of the
    arithmetic is a quoted word — a vocabulary label, a log field, a
    serialised key — reaches no module by it, and a module the arithmetic
    itself imports is not thereby able to compute it.  The planted module
    does both: it quotes each arithmetic name and imports the tracker models
    ``domain/gap.py`` imports, and reads the stamp.  Planted rather than read
    off a module of the tree, so the negative holds whatever the tree's own
    prose happens to quote.
    """
    sources = source_tree()
    sources["services/planted.py"] = (
        "from kodezart.types.domain.tracker import TrackerIssue\n"
        "\n"
        "GAP_LABEL = 'gap_membership'\n"
        "COLUMNS = ('compute_gap', 'state_membership', 'SubtreeClosure')\n"
        "\n"
        "def label(row: TrackerIssue):\n"
        "    return {GAP_LABEL: row.updated_at} if GAP_LABEL in COLUMNS else {}\n"
    )

    assert "services/planted.py" not in gap_computation_sites(sources)
    assert gap_sites_reading_the_change_stamp(sources) == {}
    assert "services/planted.py" in change_stamp_readers(sources)


@pytest.mark.parametrize("field", sorted(CHANGE_STAMP_FIELDS))
@pytest.mark.parametrize(
    "form", ["attribute", "name", "keyword", "wire_key", "class_pattern"]
)
def test_change_stamp_detector_flags_a_gap_site_that_reads_the_field(field, form):
    snippet = {
        "attribute": f"def gap(rows, since):\n"
        f"    return [row for row in rows if row.{field} > since]\n",
        "name": f"def gap(rows, {field}):\n"
        f"    return [row for row in rows if row.body_digest != {field}]\n",
        "keyword": f"def gap(tracker):\n    return tracker.query({field}=MARK)\n",
        "wire_key": f"def gap(row):\n    return row['{field}']\n",
        "class_pattern": f"def gap(row, since):\n"
        f"    match row:\n"
        f"        case Row({field}=stamp):\n"
        f"            return stamp > since\n",
    }[form]
    assert change_stamp_reads(ast.parse(snippet)) == {field}
    assert change_stamp_reads(ast.parse(snippet.replace(field, "body_digest"))) == set()


#: Where the change stamp is stated: the domain issue's own field, and the
#: recency parameter both domain queries state it as.  Each is spelled twice —
#: under its field name and under the alias the model reads and writes it by.
CHANGE_STAMP_HOMES = (
    (TrackerIssue, "updated_at"),
    (IssueQuery, "updated_since"),
    (ReviewQuery, "updated_since"),
)


def test_the_change_stamp_surface_names_every_spelling_of_the_one_field():
    """The scanned spellings are exactly the ones the models state.

    The detector's rows above are parametrised over this set, so a spelling
    dropped out of it takes its own row away with it and nothing reds, and a
    spelling the models gain is never scanned.  The set is therefore derived
    from the models themselves — each home's field name, which a rename turns
    into a lookup that fails, and the alias the model carries it under — and
    the constant must equal that derivation, so it can neither lose a spelling
    nor miss one.
    """
    derived = {
        spelling
        for model, field in CHANGE_STAMP_HOMES
        for spelling in (field, model.model_fields[field].alias)
    }

    assert None not in derived
    assert CHANGE_STAMP_FIELDS == derived
