"""The walker's own refusals and its plateau reading, asked of it directly.

The integration walk observes a refusal as a lane failure, and a lane failure
keeps only ``str(exc)``: the typed error itself, with the fields a caller reads
off it, is reachable nowhere on that path.  So the gate on an unrecorded
blocker (KOD-721) is driven here, over the shipped resolver and a delivery
double, and the exception is caught where it is raised.  The refusal of an
origin with no open-delivery reader at all is driven the same way, for the same
reason.

The plateau reading is driven here for a different reason.  The pure predicate
has its own tests over criterion identities, but the fire's turn actually ends
in ``ScopeWorkflowEngine._settle``, and that method can be rewritten to compare
gap SIZES, or to read the lane's own criterion children instead of its whole
subtree, with every walk still green: a lane that churned, lapsed or closed a
criterion under a deliverable child is then rested and its issue put back, and
nothing anywhere says otherwise.  So each of those shapes is a real board here,
read through the shipped ``read_scope_ready`` and handed to the shipped method.

Everything these tests touch is the shipped object: the resolver is a real
``BaseResolver`` over the fake port, so which blockers the gate asks about is
the production answer and not a list written here; the ready set is the
production reading of a board and not a value typed to suit a predicate; the
identities a fire is measured against come from the walker's own selection and
its own reading of what that turn owes.  The collaborators a test's own subject
must never reach are functions that refuse, so a subject that reached one would
fail loudly rather than quietly pass.
"""

import asyncio
from typing import NoReturn

import pytest
import structlog.testing

from kodezart.chains.native_delivery import NativeLaneWorkflow
from kodezart.chains.scope_walker import read_scope_ready
from kodezart.core.errors import TrackerUnavailableError
from kodezart.domain.errors import BaseResolutionError, ScopedExecutionUnavailableError
from kodezart.services import scope_runtime
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.lane_entry import LaneEntryReader
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.scope_entry import ScopeEntry
from kodezart.services.scope_runtime import (
    PLATEAU_BOUND,
    ScopeWorkflowEngine,
    _LastFire,
    _owed_identities,
)
from kodezart.services.scope_terminal import ScopeTerminal
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationConfig, RepoEntry, ScopeLabel
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import LaneFailure, ScopeWalkEvent
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import (
    FakeDeliveryProbe,
    FakeGitService,
    FakeJobQueue,
    FakeRepoCache,
    FakeScopeStatusWriter,
    FakeTrackerPort,
    PassThroughGate,
    make_tracker_issue,
)

LANE = "B"
BLOCKER = "A"
URL = "https://forge.invalid/fixture/repo"
REMOTE = "fixture-remote"
OPERATION = OperationConfig(
    operation_name="fixture",
    workspace="fixture",
    marker_prefixes={"run_state": "fixture-record"},
)


def board() -> FakeTrackerPort:
    """A lane blocked by a closed issue that recorded no branch at all.

    Exactly the set the resolver names as assumed landed, which is what makes
    the gate ask about it: the blocker is closed, so nothing is coming, and it
    recorded nothing, so there is no ref to stand on.
    """
    return FakeTrackerPort(
        issues=[
            make_tracker_issue(LANE, blocked_by=[BLOCKER]),
            make_tracker_issue(
                BLOCKER, state_name="Done", state_kind=WorkflowStateKind.COMPLETED
            ),
        ]
    )


class RefusingLane:
    """A lane graph nothing may touch, whichever attribute is asked for.

    The walker asks its composition for a lane graph before it asks for the
    origin's delivery reader, so a test about the reader's absence has to hand
    one over; a walk that then went on to USE it has passed the refusal this
    test is about, and says so here rather than failing somewhere downstream.
    """

    def __getattr__(self, name: str) -> NoReturn:
        raise AssertionError(f"the walk reached the lane graph: {name}")


async def no_union(request) -> None:
    """A scope whose repository declares no chain, which is every board here.

    Deliberately not a refusing double, unlike the two above: a refusal here
    is contained at the walk's own union boundary, so every case in this module
    would log a contained failure it is not about, while answering with nothing
    is what the composition answers for a board declaring no chain.
    """
    return None


def engine(
    port: FakeTrackerPort,
    *,
    lane_for=None,
    probe_for=None,
    repositories=(),
) -> ScopeWorkflowEngine:
    """The walker, wired as the composition wires it for everything under test.

    *lane_for* and *probe_for* default to functions that refuse: a subject that
    reached a collaborator it has no business reaching fails loudly.
    """

    def no_lane(url: str) -> NoReturn:
        raise AssertionError(f"the walker asked for a lane graph: {url}")

    def no_probe(url: str) -> NoReturn:
        raise AssertionError(f"the walker asked for an origin's probe: {url}")

    git = FakeGitService()
    records = LaneRecordReader(tracker=port, operation=OPERATION)
    return ScopeWorkflowEngine(
        tracker=port,
        lane_for=no_lane if lane_for is None else lane_for,
        probe_for=no_probe if probe_for is None else probe_for,
        union_for=no_union,
        resolver=BaseResolver(tracker=port, git=git, remote=REMOTE, refs=port),
        entries=LaneEntryReader(records=records, git=git, remote=REMOTE),
        terminal=ScopeTerminal(
            records=records,
            status=FakeScopeStatusWriter(),
            gate=PassThroughGate(),
        ),
        entry=ScopeEntry(
            approvals=port, stages_for=lambda _url: None, registry=FakeJobQueue()
        ),
        cache=FakeRepoCache(),
        repositories=repositories,
        git_base_url="https://forge.invalid",
        integration_workspace_dir="/fixture/integration",
    )


async def test_an_open_delivery_refusal_names_the_blocker() -> None:
    """The refusal says which blocker refused the lane, in both its statements.

    ``blocker_issue_ids`` is the field; the message text carries the key too,
    because the lane failure a consumer of the walk reads is built from
    ``str(exc)`` and nothing else.
    """
    port = board()
    probe = FakeDeliveryProbe(delivered=(BLOCKER,))

    with pytest.raises(BaseResolutionError) as caught:
        await engine(port)._gate_unrecorded_blockers(LANE, url=URL, probe=probe)

    assert caught.value.issue_id == LANE
    assert caught.value.blocker_issue_ids == (BLOCKER,)
    # The rendered tail, not the key anywhere in the text: a key of one
    # character is in half the sentences a rewording could produce, and the
    # assertion is about the message naming the blocker it refused for.
    assert str(caught.value).endswith(f"for the blocker {BLOCKER}")
    # The one read the carve-out allows, about the blocker and not the lane.
    assert probe.calls == [BLOCKER]
    assert probe.merge_state.calls == []


async def test_no_open_delivery_lets_the_lane_through() -> None:
    """The other answer raises nothing: resolution takes its unchanged arm."""
    probe = FakeDeliveryProbe()

    await engine(board())._gate_unrecorded_blockers(LANE, url=URL, probe=probe)

    assert probe.calls == [BLOCKER]
    assert probe.merge_state.calls == []


async def test_an_origin_with_no_open_delivery_reader_refuses_the_scope() -> None:
    """An origin the composition built no delivery reader for is not walked.

    That reader is what the gate above stands on, so an origin without one
    cannot answer the single question a lane's turn asks of a forge. The walk
    refuses the origin with its own typed error on the first event asked of it,
    rather than walking on with the gate quietly skipped: a lane would then be
    resolved against a trunk its blocker's work may not be on. Removing both
    candidate probes left this refusal exactly where it was (KOD-431).
    """
    walk = engine(
        board(),
        lane_for=lambda _: RefusingLane(),
        probe_for=lambda _: None,
        repositories=(RepoEntry(url=URL, trunk="main"),),
    ).run(
        prompt="Request prose is not the native subject",
        repo_path="/fixture/repo",
        repo_url=URL,
        base_spec=trunk_base("unused-request-default"),
        scope=ScopeRef(kind=ScopeKind.PROJECT, key="fixture-scope"),
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=[],
        cache_key="fixture-job",
    )

    # On the first event asked of it, because the body of an async generator
    # does not run until then: a walk that refused only later would already
    # have reported ticks a consumer could act on.
    with pytest.raises(ScopedExecutionUnavailableError) as caught:
        await anext(walk)

    assert "open-delivery reader" in str(caught.value)
    assert caught.value.ref == ScopeRef(kind=ScopeKind.PROJECT, key="fixture-scope")


# ---------------------------------------------------------------------------
# KOD-460 — the plateau reading at the walker, over real board readings.
# ---------------------------------------------------------------------------

SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="fixture-scope")
STAGED = "criteria-staged"
CRITERION = frozenset({"criterion"})
LANE_STATE = "In Progress"


def criterion_row(
    key: str,
    *,
    parent: str = "A",
    closed: bool = False,
    state_name: str | None = None,
    state_kind: WorkflowStateKind | None = None,
):
    """One criterion sub-issue, in the state the board holds it in.

    *state_name* is named apart from the kind because the put-back writes the
    NAME the board itself carries: a fixture that could only spell "Todo" could
    not tell the mechanism from a constant.
    """
    if closed:
        state_name, state_kind = "Done", WorkflowStateKind.COMPLETED
    return make_tracker_issue(
        key,
        parent_key=parent,
        issue_labels=CRITERION,
        state_name=state_name or "Todo",
        state_kind=state_kind or WorkflowStateKind.UNSTARTED,
        body=f"**Check:** {key} live Check  bytes\n**Evidence:** —",
    )


def scope_board(*rows, lanes=("A",), children=()):
    """A scope of *lanes*, the deliverable children under them, and *rows*.

    Each lane's own issue starts in a state the put-back would move it OUT of,
    because a restore onto the state an issue is already in writes nothing at
    all: on a board holding the lane where the put-back would put it, the write
    and its absence look the same.
    """
    return FakeTrackerPort(
        issues=[
            *(
                make_tracker_issue(
                    key,
                    issue_labels=frozenset({STAGED}),
                    state_name=LANE_STATE,
                    state_kind=WorkflowStateKind.STARTED,
                )
                for key in lanes
            ),
            *children,
            *rows,
        ],
        scope_memberships={SCOPE: tuple(lanes)},
        criteria_stage_label_key=STAGED,
        marker_prefixes=OPERATION.marker_prefixes,
        # The addressed scope is a container, and a run passes its entry's
        # approval question before any lane of it is read, so the container
        # and its own approval are seeded beside the per-member ones.
        scope_containers=[
            ScopeContainer(
                ref=SCOPE,
                name="fixture scope",
                description="",
                url="https://tracker.invalid/project/fixture-scope",
            )
        ],
        scope_label_members={
            SCOPE: frozenset({ScopeLabel.APPROVED}),
            **{
                ScopeRef(kind=ScopeKind.ISSUE, key=key): frozenset(
                    {ScopeLabel.APPROVED}
                )
                for key in lanes
            },
        },
    )


async def owed_by(port: FakeTrackerPort, walker: ScopeWorkflowEngine, *, lane: str):
    """What the walker would carry about *lane* after firing it on this board.

    Read through the walker's OWN selection and its own reading of what the
    selected turn owes, so a change to either — a turn offered with some other
    gap, identities narrowed to the lane's own criterion children — reaches
    these tests instead of being written out again here.
    """
    ready = await read_scope_ready(ref=SCOPE, tracker=port)
    selected = walker._select(ready=ready, delivers=False, rested=[])
    assert selected is not None and selected.issue.issue_key == lane
    return _LastFire(issue_key=lane, open_criteria=_owed_identities(selected))


async def settle_on(port: FakeTrackerPort, *, last: _LastFire):
    """One plateau reading over this board, as the walker makes it."""
    walker = engine(port)
    rested: list[str] = []
    failures: list[LaneFailure] = []
    await walker._settle(
        last=last,
        ready=await read_scope_ready(ref=SCOPE, tracker=port),
        rested=rested,
        failures=failures,
    )
    return rested, failures


def close(port: FakeTrackerPort, key: str) -> None:
    """Cross one criterion off, the way a fire's own evaluation crosses it off."""
    port.issues[key] = port.issues[key].model_copy(
        update={"state_name": "Done", "state_kind": WorkflowStateKind.COMPLETED}
    )


def reopen(port: FakeTrackerPort, key: str, *, state_name: str = "Todo") -> None:
    """Put one criterion back in an unstarted state, as an amendment does."""
    port.issues[key] = port.issues[key].model_copy(
        update={"state_name": state_name, "state_kind": WorkflowStateKind.UNSTARTED}
    )


def surface(port: FakeTrackerPort, key: str) -> None:
    """Add one criterion the board did not carry when the fire began."""
    port.issues[key] = criterion_row(key)


async def test_a_fire_that_closed_nothing_it_owed_rests_the_lane() -> None:
    """The positive: the board did not move, so an identical fire would not either.

    The other three cases below are the shapes whose CARDINALITY says the
    opposite of their identities, and each of them must read the other way; this
    one is what makes the three of them say something.
    """
    port = scope_board(criterion_row("A/check"))
    last = await owed_by(port, engine(port), lane="A")

    rested, failures = await settle_on(port, last=last)

    assert rested == ["A"]
    assert failures == []
    assert port.restored_states == [("A", "Todo")]
    assert port.issues["A"].state_name == "Todo"


async def test_the_bound_is_one_tick_and_the_walk_spends_that_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound's VALUE, and that the walk is what spends it (KOD-727).

    The reading the walk makes is one tick long, so the constant's value is the
    whole of the policy: at one, a fire that closed nothing of what its lane
    owed ends that lane's turn, which the test above drives; at two the window
    is never full and no fire ever ends a turn, so the arm is dead.  Both
    fixtures that prove a lane closing one previously-open reference per tick
    runs past the bound stay green at either value — a closure clears any
    window — which is why the value is pinned here rather than inferred from
    them.

    The same board is read twice, so what the assertion rests on is the
    constant and not the board: raising it is the only difference between a
    rested lane and an untouched one, which a call site spelling the quantity
    itself would not show.
    """
    assert PLATEAU_BOUND == 1
    port = scope_board(criterion_row("A/check"))
    last = await owed_by(port, engine(port), lane="A")

    monkeypatch.setattr(scope_runtime, "PLATEAU_BOUND", 2)
    rested, failures = await settle_on(port, last=last)

    assert (rested, failures, port.restored_states) == ([], [], [])


async def test_a_tick_that_closed_twelve_and_surfaced_twelve_is_not_a_plateau() -> None:
    """The gap is the size it was, and twelve of the identities it owed closed."""
    port = scope_board(*(criterion_row(f"A/check-{index}") for index in range(1, 13)))
    last = await owed_by(port, engine(port), lane="A")
    for index in range(1, 13):
        close(port, f"A/check-{index}")
    for index in range(13, 25):
        surface(port, f"A/check-{index}")

    rested, failures = await settle_on(port, last=last)

    # Not vacuous: the gap really did hold its size across the tick, which is
    # what a count-based reading would call a plateau.
    ready = await read_scope_ready(ref=SCOPE, tracker=port)
    assert len(ready.ready[0].gap) == len(last.open_criteria) == 12
    assert rested == []
    assert failures == []
    assert port.restored_states == []


async def test_a_tick_whose_gap_grew_through_a_lapse_is_not_a_plateau() -> None:
    """One closed and two lapsed back: the gap GREW and the fire did real work."""
    port = scope_board(
        criterion_row("A/first"),
        criterion_row("A/second", closed=True),
        criterion_row("A/third", closed=True),
    )
    last = await owed_by(port, engine(port), lane="A")
    close(port, "A/first")
    reopen(port, "A/second")
    reopen(port, "A/third")

    rested, failures = await settle_on(port, last=last)

    ready = await read_scope_ready(ref=SCOPE, tracker=port)
    assert len(ready.ready[0].gap) == 2
    assert last.open_criteria == {"A/first"}
    assert rested == []
    assert failures == []
    assert port.restored_states == []


async def test_a_tick_whose_only_closure_is_under_a_child_is_not_a_plateau() -> None:
    """The lane owes its whole SUBTREE, so a closure under a child is progress.

    The criterion that closed hangs off a deliverable child of the lane, which
    is not a criterion row itself: a reading that kept only the lane's own
    criterion children would find nothing closed and give the lane's turn up.
    """
    child = make_tracker_issue("A1", parent_key="A")
    port = scope_board(
        criterion_row("A/check"),
        criterion_row("A1/check", parent="A1"),
        children=(child,),
    )
    last = await owed_by(port, engine(port), lane="A")
    close(port, "A1/check")

    rested, failures = await settle_on(port, last=last)

    # The premise: the criterion that closed really is a descendant and not a
    # child, and the lane really did owe it.
    assert port.issues["A1/check"].parent_key == "A1"
    assert last.open_criteria == {"A/check", "A1/check"}
    assert rested == []
    assert failures == []
    assert port.restored_states == []


async def test_the_put_back_writes_the_unstarted_state_the_board_names() -> None:
    """The state name is the criterion's own, never a vocabulary of the walker's."""
    port = scope_board(criterion_row("A/check", state_name="Ready"))
    last = await owed_by(port, engine(port), lane="A")

    rested, _ = await settle_on(port, last=last)

    assert rested == ["A"]
    assert port.restored_states == [("A", "Ready")]
    assert port.issues["A"].state_name == "Ready"


async def test_the_put_back_passes_over_a_started_row_for_the_unstarted_one() -> None:
    """A started criterion names no state to go back to; the next one does."""
    port = scope_board(
        criterion_row(
            "A/first", state_name="In Review", state_kind=WorkflowStateKind.STARTED
        ),
        criterion_row("A/second", state_name="Ready"),
    )
    last = await owed_by(port, engine(port), lane="A")

    rested, _ = await settle_on(port, last=last)

    ready = await read_scope_ready(ref=SCOPE, tracker=port)
    # The order is the board's own and the first row really is the started one,
    # so passing over it is what the second row's name being written means.
    assert [row.issue_key for row in ready.ready[0].gap] == ["A/first", "A/second"]
    assert rested == ["A"]
    assert port.restored_states == [("A", "Ready")]


async def test_a_gap_with_nothing_unstarted_rests_the_lane_and_writes_nothing() -> None:
    """Every open criterion started: there is no state to put the issue back to.

    Inventing one is not the walker's business, so the lane rests on the reading
    it made and the write not made is stated by name rather than passed over.
    """
    port = scope_board(
        criterion_row(
            "A/check", state_name="In Review", state_kind=WorkflowStateKind.STARTED
        )
    )
    last = await owed_by(port, engine(port), lane="A")

    with structlog.testing.capture_logs() as logs:
        rested, failures = await settle_on(port, last=last)

    assert rested == ["A"]
    assert failures == []
    assert port.restored_states == []
    assert port.issues["A"].state_name == LANE_STATE
    assert [
        event["lane"]
        for event in logs
        if event.get("event") == "scope_lane_put_back_skipped"
    ] == ["A"]


async def test_a_put_back_that_fails_is_the_lanes_own_fault(monkeypatch) -> None:
    """The write is the lane's work, so its failure rests and reports that lane.

    A per-issue write that cannot be made says nothing about the scope, and
    ending the whole run for it would strand every other lane. The lane rests
    exactly once — the boundary rests it, and the plateau arm does not rest it
    again — and the walk has the rest of its invocation left.
    """
    port = scope_board(criterion_row("A/check"))
    last = await owed_by(port, engine(port), lane="A")

    async def unavailable(*, issue_key: str, state_name: str) -> NoReturn:
        raise TrackerUnavailableError(f"the state write for {issue_key} failed")

    monkeypatch.setattr(port, "restore_workflow_state", unavailable)

    rested, failures = await settle_on(port, last=last)

    assert rested == ["A"]
    assert [failure.issue_key for failure in failures] == ["A"]
    assert failures[0].error.error_kind == "TrackerUnavailableError"
    assert "the state write for A failed" in failures[0].error.error


# ---------------------------------------------------------------------------
# KOD-724, KOD-725 — the fire helper's own refusal.  A stream that ends with
# no final state at all, or with the delivery still pending, is a fire nothing
# can be reported about.
# ---------------------------------------------------------------------------

#: How long one of these walks may take before the test fails instead of
#: hanging.  The walk below drives no real graph, so the bound is a guard
#: against a walk that spins on a lane it never rested rather than a budget.
WALK_BOUND_SECONDS = 30

NO_FINAL_STATE = "no root values payload"
PENDING_PHASE = "a delivery still pending"


def lane_state(issue_key: str):
    """One prepared lane state, with the delivery phase the launch puts on it.

    Every field of the fire state is spelled here because making one is a
    lane launch's business and this test has no lane graph to do it. The
    delivery phase — the one field under test — is deliberately NOT spelled:
    ``NativeLaneWorkflow.prepare`` is the shipped initializer every real launch
    goes through, so the pending phase the walker reads here is the production
    one and not a value typed in a test.
    """
    return NativeLaneWorkflow.prepare(
        {
            "issue_key": issue_key,
            "lane_entry": None,
            "feature_branch": f"kodezart/{issue_key}",
            "ralph_branch": f"kodezart/{issue_key}-ralph",
            "work_base_ref": "main",
            "fire_spec": None,
            "acceptance_criteria": [],
            "criterion_set": None,
            "criteria_validation": None,
            "criteria_regeneration_rounds": 0,
            "criteria_infeasible": False,
            "accept_verdict": AcceptVerdict.rejected,
            "flagged_items": [],
            "total_iterations": 0,
            "feature_tip_sha": None,
            "review_base_sha": None,
            "review_head_sha": None,
            "merged": False,
            "merge_error": None,
            "review_passed": False,
            "review_feedback": None,
            "remediation_rounds_used": 0,
            "remediation_ticket": None,
            "remediation_entry": None,
            "best_iteration_sha": None,
            "repo_url": URL,
            "repo_visibility": RepoVisibility.PRIVATE,
            "trajectory": None,
        }
    )


class LaneFire:
    """The lane's fire, doubled down to the one call the walker makes of it."""

    def prepare(self, **facts):
        # Handed back untouched. What the walker does with a prepared state is
        # launch it, and this double is about the launch's stream alone.
        return facts, {"configurable": {"thread_id": facts["issue_key"]}}


class LaneGraph:
    """A lane graph whose stream ends the way its *shape* says it ends.

    The two shapes differ in the ONE qualifier the walker's reader reads: a
    values payload from the root is a final state, and the identical payload
    from inside the graph is not. So the arm under test is chosen by the
    namespace rather than by two unrelated payloads, and a delivery still
    pending is what both of them carry.
    """

    def __init__(self, *, shape: str) -> None:
        self.shape = shape
        self.launched: list[str] = []

    async def astream(self, initial, *, config, stream_mode, subgraphs):
        # The walker asks for both stream modes and for subgraphs. A reader
        # that stopped asking for either would not see what this double
        # answers, so the premise is stated where the answer is given.
        assert subgraphs and set(stream_mode) == {"custom", "values"}
        self.launched.append(initial["issue_key"])
        namespace = () if self.shape == PENDING_PHASE else ("native_fire",)
        yield namespace, "values", initial


class UnreportableFireLane:
    """One lane every fire of which streams the shape a test named."""

    delivers = True

    def __init__(self, *, shape: str) -> None:
        self.fire = LaneFire()
        self.graph = LaneGraph(shape=shape)

    def prepare(self, state):
        return lane_state(state["issue_key"])


@pytest.mark.parametrize("shape", [NO_FINAL_STATE, PENDING_PHASE])
async def test_a_fire_with_no_final_delivery_phase_is_the_lanes_own_failure(
    shape,
) -> None:
    """A fire nothing can be reported about refuses, and the lane pays for it.

    The walker streams one lane's fire and then says how it ended. Two streams
    leave it nothing to say: one that ended without the root ever reporting a
    final state, and one whose final state still carries the phase the launch
    initialized. Neither is a delivery that happened and neither is a delivery
    that was skipped, so reporting either as one would put a fact on the walk
    that no stream established.

    The refusal is typed and it names the scope it was read under. It is raised
    inside the lane's own boundary, so it is that lane's fault: the lane is
    reported failed with the message on the observation, rested exactly once,
    and the walk takes its next tick instead of ending on the error.
    """
    port = scope_board(criterion_row("A/check"))
    lane = UnreportableFireLane(shape=shape)
    walk = engine(
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

    async with asyncio.timeout(WALK_BOUND_SECONDS):
        observations = [
            event.observation
            async for event in walk
            if isinstance(event, ScopeWalkEvent)
        ]

    # The fire really was launched, so what refused is a stream that ran and
    # not a lane the walk never reached.
    assert lane.graph.launched == ["A"]
    assert observations[-1].dispatched == ("A",)
    failures = observations[-1].failed_lanes
    assert [failure.issue_key for failure in failures] == ["A"]
    assert [failure.error.error_kind for failure in failures] == ["ScopeReadError"]
    assert "native lane has no final delivery phase" in failures[0].error.error
    # Rested once: the boundary rests the lane it reported, and no later arm
    # rests it again.
    assert observations[-1].rested_lanes == ("A",)
    # The walk went on. The tick after the refusal is taken — the lane still
    # owes its criterion, so it is still in the ready set and still not offered
    # — and the walk ends on having no candidate rather than on the error.
    assert len(observations) == 2
    assert observations[-1].ready == ("A",)
    # And nothing was put back. The lane's turn ended in its own fault, which
    # the boundary reports, and the plateau reading is not asked about a lane
    # already resting.
    assert port.restored_states == []
