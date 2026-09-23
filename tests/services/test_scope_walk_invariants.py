"""What one walk observation states about the criteria the gap does not carry.

Both readings are made at the walker itself, over a board the shipped
``read_scope_ready`` reads, because both are about what an OBSERVATION says:
the keys a criterion's own state set aside, and the keys the scope's filter
cannot address in their own right. A test written against the ready set alone
could not tell either of them from a field nobody fills.

The walk is driven exactly one observation deep. Nothing here needs a fire, a
clone or an origin, so the lane graph is inert and every other collaborator the
walker could reach refuses: a subject that went further would fail loudly.
"""

import ast
import asyncio
import inspect
import sys
from types import ModuleType
from typing import NoReturn

import structlog.testing

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.composition.scope_runtime import build_scope_runtime
from kodezart.config.app import AppConfig
from kodezart.domain.fire_plateau import fire_plateaued
from kodezart.domain.fire_spec import criterion_field_bodies
from kodezart.services.lane_entry import LaneEntryReader
from kodezart.services.scope_runtime import PLATEAU_BOUND, ScopeWorkflowEngine
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.dispatch import ExclusionClause
from kodezart.types.domain.operation import RepoEntry, ScopeLabel
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import GapMeasurement, ScopeWalkEvent
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind
from tests.chains.test_scope_gap_membership import PHANTOM_CHECKLIST
from tests.fakes import FakeDeliveryProbe, FakeTrackerPort, make_tracker_issue
from tests.services.test_scope_runtime import (
    LANE_STATE,
    SCOPE,
    STAGED,
    URL,
    WALK_BOUND_SECONDS,
    criterion_row,
    engine,
    scope_board,
)


class InertLane:
    """A lane graph that answers ``delivers`` and refuses everything else.

    Selection asks a lane graph one question before it offers anything, so a
    walk driven to its first observation has to be handed one. ``delivers`` is
    False because a delivery-only turn is a different reading; every other
    attribute raises, so a walk that fired would say so here.
    """

    delivers = False

    def __getattr__(self, name: str) -> NoReturn:
        raise AssertionError(f"the walk reached the lane graph: {name}")


class FireNotUnderTestError(Exception):
    """What a lane's fire answers here: its preparation is not what is read."""


class UnfiredPreparation:
    """A fire whose preparation refuses, so the lane's own boundary rests it."""

    def prepare(self, **_facts: object) -> NoReturn:
        raise FireNotUnderTestError


class RestingLane:
    """A lane graph whose every turn ends inside the lane's own boundary.

    The refusal is the lane's, so the walk rests that lane and takes its next
    tick with the board unchanged. That is what lets one walk put the same
    ready lanes in front of several observations without a fire, a clone or
    an origin.
    """

    delivers = False
    fire = UnfiredPreparation()


def walk_over(port: FakeTrackerPort, lane: object):
    """The walker's ``run`` over *port*, with every lane answered by *lane*."""
    return engine(
        port,
        lane_for=lambda _: lane,
        probe_for=lambda _: FakeDeliveryProbe(),
        repositories=(RepoEntry(url=URL, trunk="main"),),
    ).run(
        prompt="Request prose is not the native subject",
        repo_path="/fixture/repo",
        repo_url=URL,
        base_spec=trunk_base("unused-request-default"),
        scope=SCOPE,
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=[],
        cache_key="fixture-job",
    )


async def first_observation(port: FakeTrackerPort):
    """The walk's first ``scope_walk`` observation over *port*, and nothing more."""
    walk = walk_over(port, InertLane())
    try:
        event = await anext(walk)
    finally:
        await walk.aclose()
    assert isinstance(event, ScopeWalkEvent)
    return event.observation


async def test_the_walk_lists_the_excluded_criteria_beside_each_gap() -> None:
    """A criterion set aside on its own state is named, never dropped in silence.

    Two lanes, so the claim is scope-wide and not one lane's: A owes one check
    and had another canceled under it, and B's only criterion is a duplicate,
    which leaves B owing nothing at all. A reader of this observation can tell
    B-owes-nothing from B-was-never-read, which is the whole point of naming
    the keys beside the gap rather than shortening the gap silently.
    """
    port = scope_board(
        criterion_row("A/check"),
        criterion_row(
            "A/dropped",
            state_name="Canceled",
            state_kind=WorkflowStateKind.CANCELED,
        ),
        criterion_row(
            "B/twin",
            parent="B",
            state_name="Duplicate",
            state_kind=WorkflowStateKind.DUPLICATE,
        ),
        lanes=("A", "B"),
    )

    observation = await first_observation(port)

    assert observation.excluded_criteria == ("A/dropped", "B/twin")
    assert observation.unresolved_criteria == ("A/check",)
    assert observation.ready == ("A",)

    assert observation.gaps == (
        GapMeasurement(lane_key="A", criterion_keys=("A/check",)),
    )

    ready = await read_scope_ready(ref=SCOPE, tracker=port)
    assert [issue.issue_key for issue in ready.closed] == ["B"]
    assert ready.excluded == ("A/dropped", "B/twin")


#: The project B1's criterion sits in, which is not the addressed scope: the
#: reason the filter gives for it is this identity, where A1's criterion,
#: belonging to no project, is given a sentence saying so.
ELSEWHERE = "another-project"


def out_of_reach_board() -> FakeTrackerPort:
    """Two lanes, a deliverable child under each, and a criterion under each child.

    The child is the shape a container filter misses: it is nobody's direct
    criterion child, so no member read resolves it, while the criterion it
    carries is squarely inside the lane's subtree and squarely the lane's work.
    Two lanes so every ready lane is shown to be read, and two different
    reasons so each is shown to be the one its own criterion is given.

    A1 also carries a completed and a canceled criterion beside its open
    one. They are out of reach too, but they are nobody's obligation, so
    naming them would call discharged or excluded work unreachable: only the
    OPEN criteria the filter cannot reach are named.
    """
    return scope_board(
        criterion_row("A/check"),
        criterion_row("A1/check", parent="A1"),
        criterion_row("A1/done", parent="A1", closed=True),
        criterion_row(
            "A1/dropped",
            parent="A1",
            state_name="Canceled",
            state_kind=WorkflowStateKind.CANCELED,
        ),
        criterion_row("B/check", parent="B"),
        criterion_row("B1/check", parent="B1").model_copy(
            update={"project_id": ELSEWHERE}
        ),
        lanes=("A", "B"),
        children=tuple(
            make_tracker_issue(
                child,
                parent_key=lane,
                issue_labels=frozenset({STAGED}),
                state_name=LANE_STATE,
                state_kind=WorkflowStateKind.STARTED,
            )
            for lane, child in (("A", "A1"), ("B", "B1"))
        ),
    )


#: Both out-of-reach criteria, with the reason the filter gives each, in the
#: order their lanes are ready.
OUT_OF_REACH = [
    ("A1/check", ExclusionClause.OUT_OF_SCOPE, "the issue belongs to no project"),
    ("B1/check", ExclusionClause.OUT_OF_SCOPE, ELSEWHERE),
]


def out_of_scope(observation) -> list[tuple[str, ExclusionClause, str]]:
    """The observation's out-of-scope exclusions, as key, clause and reason."""
    return [
        (item.issue_key, item.clause, item.detail)
        for item in observation.exclusions
        if item.clause is ExclusionClause.OUT_OF_SCOPE
    ]


async def test_an_open_criterion_the_filter_cannot_reach_is_named_with_its_reason() -> (
    None
):
    """Unreachable is stated, with the reason the filter itself is stated in.

    A reader that saw neither the criterion nor a statement about it could not
    tell an obligation the scope cannot address from none at all, so the walk
    says which key and why. The lane is still fired for it: the key is on the
    lane's own gap measurement beside the criterion the filter does reach.
    """
    observation = await first_observation(out_of_reach_board())

    assert out_of_scope(observation) == OUT_OF_REACH
    assert "A/check" not in [key for key, _, _ in out_of_scope(observation)]
    assert observation.gaps == (
        GapMeasurement(lane_key="A", criterion_keys=("A/check", "A1/check")),
        GapMeasurement(lane_key="B", criterion_keys=("B/check", "B1/check")),
    )


async def test_every_tick_names_the_out_of_reach_criteria_of_every_ready_lane() -> None:
    """The naming is the walk's on every tick, not the first observation's.

    Each lane's turn ends inside its own boundary, so the board never changes
    and both lanes stay ready while the walk rests them one tick at a time.
    Every observation therefore owes both statements, whichever lane the
    tick goes on to offer.
    """
    walk = walk_over(out_of_reach_board(), RestingLane())
    async with asyncio.timeout(WALK_BOUND_SECONDS):
        observations = [
            event.observation
            async for event in walk
            if isinstance(event, ScopeWalkEvent)
        ]

    assert [observation.tick for observation in observations] == [1, 2, 3]
    assert [observation.rested_lanes for observation in observations] == [
        (),
        ("A",),
        ("A", "B"),
    ]
    assert [
        failure.issue_key
        for failure in observations[-1].failed_lanes
        if failure.error.error_kind == FireNotUnderTestError.__name__
    ] == ["A", "B"]
    for observation in observations:
        assert observation.ready == ("A", "B")
        assert out_of_scope(observation) == OUT_OF_REACH


async def test_the_same_shape_inside_the_filter_names_no_out_of_scope_entry() -> None:
    """The control arm: the filter resolves the child, so nothing is out of reach.

    Same tree, same criteria, one difference — the child is a scope member, so
    the member read resolves its criterion in its own right. An exclusion that
    appeared here would be naming reachable work unreachable.
    """
    port = out_of_reach_board()
    port.scope_memberships[SCOPE] = ("A", "A1", "B", "B1")
    for child in ("A1", "B1"):
        port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key=child)] = frozenset(
            {ScopeLabel.APPROVED}
        )

    observation = await first_observation(port)

    assert out_of_scope(observation) == []
    assert observation.gaps == (
        GapMeasurement(lane_key="A", criterion_keys=("A1/check", "A/check")),
        GapMeasurement(lane_key="A1", criterion_keys=("A1/check",)),
        GapMeasurement(lane_key="B", criterion_keys=("B1/check", "B/check")),
        GapMeasurement(lane_key="B1", criterion_keys=("B1/check",)),
    )


def trap_lane_bodies(monkeypatch) -> tuple[list[str], list[str]]:
    """Record every ``body`` read of a non-criterion row until a lane's entry.

    Returns the list the trap records body reads into and the list of lane
    keys whose entry was read. The trap is installed now and lifted when a
    lane's entry is read, which is where a turn is launched from; the fire's
    own subject prompt is read after that, and is the fire's input rather
    than a resolution of which lane to fire.

    The trap sees attribute reads of ``body`` on a ``TrackerIssue``; a read
    through ``__dict__``, ``vars()`` or ``model_dump()`` is outside it.
    """
    body_reads: list[str] = []
    launched: list[str] = []
    original = TrackerIssue.__getattribute__

    def trapped(issue: TrackerIssue, name: str) -> object:
        if name == "body" and "criterion" not in original(issue, "issue_labels"):
            body_reads.append(original(issue, "issue_key"))
        return original(issue, name)

    read_entry = LaneEntryReader.read

    async def lifting(self: LaneEntryReader, **facts: object):
        launched.append(str(facts["issue_key"]))
        monkeypatch.setattr(TrackerIssue, "__getattribute__", original)
        return await read_entry(self, **facts)

    monkeypatch.setattr(LaneEntryReader, "read", lifting)
    monkeypatch.setattr(TrackerIssue, "__getattribute__", trapped)
    return body_reads, launched


async def test_dispatch_target_resolution_reads_no_lane_body(monkeypatch) -> None:
    """The walk picks its lane from typed rows; the lane's prose is not read.

    The lane's description is a checklist in the live template grammar, so a
    selection that consulted it — to skip the lane, to mint a target, to rank
    it — would have text to act on. A recording trap on ``body`` for every
    row that is not a criterion is installed over one tick: the ready read,
    the selection, the turn built from the chosen row, and the readmission
    the turn asks before its entry. It is lifted when the lane's entry is
    read (see ``trap_lane_bodies``).
    """
    assert criterion_field_bodies(PHANTOM_CHECKLIST, field="Check") != ()
    port = scope_board(criterion_row("A/check"))
    port.issues["A"] = port.issues["A"].model_copy(update={"body": PHANTOM_CHECKLIST})
    body_reads, launched = trap_lane_bodies(monkeypatch)
    walk = walk_over(port, RestingLane())
    async with asyncio.timeout(WALK_BOUND_SECONDS):
        observations = [
            event.observation
            async for event in walk
            if isinstance(event, ScopeWalkEvent)
        ]

    assert body_reads == []
    # The tick really reached the launch: the lane was selected, readmitted
    # and entered, and its turn ended in the fire's own preparation.
    assert launched == ["A"]
    assert observations[0].ready == ("A",)
    assert [
        failure.issue_key
        for failure in observations[-1].failed_lanes
        if failure.error.error_kind == FireNotUnderTestError.__name__
    ] == ["A"]


class DeliveringLane:
    """A lane graph whose origin can deliver, so a finished lane is offered.

    Its turn never gets as far as a fire here: the finished lane has nothing
    recorded to deliver, so its entry reading leaves it nothing to do.
    """

    delivers = True
    fire = UnfiredPreparation()


async def test_the_delivery_only_turn_is_chosen_without_reading_a_lane_body(
    monkeypatch,
) -> None:
    """A finished lane is offered for its delivery from typed rows alone.

    The lane owes nothing, so the ready read files it closed, and the origin
    can deliver, so selection's delivery arm is what offers it. Its
    description is a checklist in the live template grammar, with a Check row
    and an Evidence row to act on. The same trap as the ready arm's is held
    over the tick until the delivery-only turn's entry is read.
    """
    assert criterion_field_bodies(PHANTOM_CHECKLIST, field="Check") != ()
    assert criterion_field_bodies(PHANTOM_CHECKLIST, field="Evidence") != ()
    port = scope_board(criterion_row("A/check", closed=True))
    port.issues["A"] = port.issues["A"].model_copy(update={"body": PHANTOM_CHECKLIST})
    assert [
        issue.issue_key
        for issue in (await read_scope_ready(ref=SCOPE, tracker=port)).closed
    ] == ["A"]
    body_reads, launched = trap_lane_bodies(monkeypatch)
    with structlog.testing.capture_logs() as logs:
        walk = walk_over(port, DeliveringLane())
        async with asyncio.timeout(WALK_BOUND_SECONDS):
            observations = [
                event.observation
                async for event in walk
                if isinstance(event, ScopeWalkEvent)
            ]

    assert body_reads == []
    # The tick really reached the delivery-only turn's entry: the finished
    # lane was selected, readmitted as still finished and entered, and the
    # entry left it nothing to do.
    assert launched == ["A"]
    assert observations[0].ready == ()
    assert [
        event["lane"] for event in logs if event["event"] == "scope_lane_nothing_to_do"
    ] == ["A"]


def module_of(value: object) -> ModuleType:
    """The module an object was defined in, located through the object itself."""
    return sys.modules[value.__module__]


def config_reads(module: ModuleType) -> tuple[set[str], set[str]]:
    """What *module* imports from ``kodezart.config``, and the settings it names.

    The second set is every attribute name the module spells that is a field
    of ``AppConfig``, read from the parse.
    """
    tree = ast.parse(inspect.getsource(module))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").split(".")[:2] == ["kodezart", "config"]
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name.split(".")[:2] == ["kodezart", "config"]
    }
    named = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in AppConfig.model_fields
    }
    return imported, named


def test_the_walker_and_its_plateau_read_no_setting() -> None:
    """How many barren fires rest a lane is a constant, never an operator's knob.

    The walker module that holds the bound and the module that reads the
    plateau import nothing from the configuration package and name no
    application setting. The composition that builds the walker reads exactly
    the settings it reads now; one more there is a setting reaching the walk,
    and it has to arrive in this literal with the consumer that reads it.
    """
    engine = module_of(ScopeWorkflowEngine)
    # The bound is the engine module's own constant, read where it is used.
    assert engine.PLATEAU_BOUND == PLATEAU_BOUND
    for module in (engine, module_of(fire_plateaued)):
        assert config_reads(module) == (set(), set()), module.__name__
    assert config_reads(module_of(build_scope_runtime))[1] == {
        "git",
        "union_check_step_timeout_seconds",
    }
