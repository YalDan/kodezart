"""The scope walk ends with one typed terminal report (KOD-471).

Driven over the composed walk, not over the terminal alone: what the report
states has to be what a real invocation's last reading held, and the event has
to be the last thing the stream carries. The walk's own fixture is imported
rather than copied — one board, one runtime, one bounded drive for every test
that needs a walk.

Every walk here is bounded by the fixture's own ``bounded_walk``, and each
tick count is a literal observed from the run before it was written down.
"""

import ast
import asyncio
import inspect

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.adapters.job_registry import InMemoryJobRegistry
from kodezart.chains.criteria import TrackerCriteria
from kodezart.composition.engine import build_workflow_engine
from kodezart.composition.jobs import build_job_queue
from kodezart.config.app import AppConfig
from kodezart.config.job_queue import JobQueueSettings
from kodezart.config.write_back import WriteBackSettings
from kodezart.core.errors import TrackerUnavailableError
from kodezart.core.protocols import GitService, ScopeStatusUpdates, TrackerPort
from kodezart.domain.errors import BaseResolutionError
from kodezart.domain.scope_terminal import (
    render_scope_status,
    scope_status_aggregates,
)
from kodezart.handlers.agent_handler import AgentHandler
from kodezart.services import scope_terminal as terminal_module
from kodezart.services.agent_service import AgentService
from kodezart.services.scope_terminal import ScopeTerminal
from kodezart.types.domain.agent import WorkflowIterationEvent
from kodezart.types.domain.gating import ContentClass, OutboundDestination
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeLaneEvent, ScopeWalkEvent
from kodezart.types.domain.scope_terminal import (
    ScopeLaneEntry,
    ScopeTerminalEvent,
    derive_scope_outcome,
)
from kodezart.types.domain.ticket_review import TicketReviewMode
from kodezart.types.requests.agent import WorkflowRequest
from tests.adapters.test_github_api import _make_client
from tests.chains.test_native_fire import (
    NativeExecutor,
    native_evaluation,
    native_operation,
)
from tests.chains.test_write_back_adoption import (
    Journal,
    RecordingTracker,
    artifact_writes,
)
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeChangePersister,
    FakeGitService,
    FakeRefPublisher,
    FakeRepoCache,
    FakeScopeStatusWriter,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_prompt_provider,
)
from tests.integration.test_scope_runtime import (
    FORGE_ORIGIN,
    ORIGIN,
    SCOPE,
    WALK_BOUND_SECONDS,
    WalkRepos,
    approve_container,
    board,
    bounded_walk,
    drive,
    finish_by_hand,
    lane_record,
    resumable,
    runtime,
    ticks_of,
    unapproved_members,
)
from tests.lane_fixture import ScopeForgeWire
from tests.services.test_scope_runtime_static import imported_modules, path_of
from tests.tracker.test_linear_tool_roster import SOURCE_ROOT

#: "the caller said nothing about this" where ``None`` is itself an answer
#: a case gives.
_UNSET = object()


def terminals(events):
    """Every terminal report one walk emitted, which is at most one."""
    return [event for event in events if isinstance(event, ScopeTerminalEvent)]


async def test_one_walk_emits_exactly_one_terminal_report_after_its_last_tick():
    harness = runtime(port=board(lanes=("A",)))

    events = await bounded_walk(harness)

    # Two ticks: the one that fired A, and the one with nothing left to offer.
    assert len(ticks_of(events)) == 2
    assert len(terminals(events)) == 1
    assert isinstance(events[-1], ScopeTerminalEvent)
    assert isinstance(events[-2], ScopeWalkEvent)


async def test_the_report_carries_one_entry_per_lane_with_its_recorded_facts():
    harness = runtime(port=board(lanes=("A", "B")), lanes=("A", "B"))

    report = terminals(await bounded_walk(harness))[0]

    assert report.scope == SCOPE
    assert [lane.issue for lane in report.lanes] == ["A", "B"]
    # Every criterion of both lanes was crossed off, so both owe nothing.
    assert [lane.done for lane in report.lanes] == [True, True]
    # This fixture's lanes commit nothing, so neither leaves a record and both
    # recorded columns are absent rather than invented.
    assert [(lane.branch, lane.pr) for lane in report.lanes] == [
        (None, None),
        (None, None),
    ]


async def test_the_scope_outcome_is_the_derivation_of_the_vector_it_reports():
    harness = runtime(port=board(lanes=("A",)))

    report = terminals(await bounded_walk(harness))[0]

    assert report.outcome is derive_scope_outcome(report.lanes)
    assert report.outcome is WorkflowOutcome.scope_converged


async def test_a_lane_that_owes_criteria_at_the_exit_reports_not_done():
    """An unapproved lane is never read for a gap, so it never owes nothing."""
    harness = runtime(port=unapproved_members(lanes=("A",)))

    events = await bounded_walk(harness)

    # One tick: the reading offered no lane at all.
    assert len(ticks_of(events)) == 1
    report = terminals(events)[0]
    assert report.lanes == (
        ScopeLaneEntry(issue="A", done=False, branch=None, pr=None),
    )
    assert report.outcome is WorkflowOutcome.scope_stopped_short


async def test_the_report_is_the_wire_snapshot_a_consumer_reads():
    harness = runtime(port=board(lanes=("A",)))

    report = terminals(await bounded_walk(harness))[0]
    payload = report.model_dump(mode="json", by_alias=True)

    assert payload["type"] == "scope_terminal"
    assert payload["outcome"] == "scope_converged"
    assert payload["lanes"] == [
        {"issue": "A", "done": True, "branch": None, "pr": None}
    ]
    assert ScopeTerminalEvent.model_validate(payload) == report


async def test_a_blocked_lane_is_a_lane_of_the_report_and_is_not_done():
    """A lane a live blocker holds is reported, not omitted for lack of a turn."""
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    # Execution is not approved for A, so nothing reads its gap and it never
    # closes; B is therefore held by a live blocker for the whole invocation.
    port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="A")] = frozenset()
    harness = runtime(port=port, lanes=("A", "B"))

    events = await bounded_walk(harness)

    # One tick: the reading offered no lane at all.
    assert len(ticks_of(events)) == 1
    assert ticks_of(events)[0].unapproved_lanes == ("A",)
    report = terminals(events)[0]
    assert [lane.issue for lane in report.lanes] == ["A", "B"]
    assert [lane.done for lane in report.lanes] == [False, False]
    assert report.outcome is WorkflowOutcome.scope_stopped_short


async def test_an_issue_kind_scope_reports_the_same_vector():
    """The event is produced for every scope kind the walk accepts (KOD-471)."""
    scope = ScopeRef(kind=ScopeKind.ISSUE, key="A")
    port = board(lanes=("A",))
    port.scope_memberships[scope] = ("A",)
    harness = runtime(port=port)

    report = terminals(await bounded_walk(harness, scope=scope, origin=ORIGIN))[0]

    assert report.scope == scope
    assert [lane.issue for lane in report.lanes] == ["A"]


# ---------------------------------------------------------------------------
# KOD-484 — the report is posted on the container, through this lane's own
# destination member, and a scope with no status surface posts nothing.
# ---------------------------------------------------------------------------


async def test_the_walk_posts_its_report_on_the_scopes_own_container():
    harness = runtime(port=board(lanes=("A",)))

    events = await bounded_walk(harness)

    report = terminals(events)[0]
    assert [ref for ref, _ in harness.status.posts] == [SCOPE]
    assert harness.status.posts[0][1] == render_scope_status(report)


async def test_the_posted_report_carries_the_outcome_and_one_line_per_lane():
    harness = runtime(port=board(lanes=("A", "B")), lanes=("A", "B"))

    await bounded_walk(harness)

    body = harness.status.posts[0][1]
    assert body.splitlines()[0] == "Scope outcome: scope_converged"
    assert len([line for line in body.splitlines() if line.startswith("- [")]) == 2
    assert "A" in body and "B" in body


async def test_the_report_goes_through_the_gate_on_this_lanes_own_destination():
    """One gated write on this destination, carrying the report's own bytes.

    The walk shares one gate with every other writer of the run, so the claim
    is about this destination's entries in it and not about the whole log:
    exactly one, declared DERIVED, carrying what the report rendered to.
    """
    harness = runtime(port=board(lanes=("A",)))

    report = terminals(await bounded_walk(harness))[0]

    gate = harness.engine._scoped_arm._terminal._gate
    assert gate.destinations.count(OutboundDestination.TRACKER_STATUS_UPDATE) == 1
    at = gate.destinations.index(OutboundDestination.TRACKER_STATUS_UPDATE)
    assert gate.content_classes[at] is ContentClass.DERIVED
    assert gate.calls[at][0] == render_scope_status(report)
    assert gate.aggregates[at] == scope_status_aggregates(report)


async def test_a_scope_with_no_status_surface_ends_with_the_event_alone():
    """An issue-kind scope has no container to post on, and none is invented."""
    scope = ScopeRef(kind=ScopeKind.ISSUE, key="A")
    port = board(lanes=("A",))
    port.scope_memberships[scope] = ("A",)
    harness = runtime(port=port)

    events = await bounded_walk(harness, scope=scope, origin=ORIGIN)

    assert len(terminals(events)) == 1
    assert harness.status.posts == []


def compose_scope_arm(*, status, registry=_UNSET):
    """The composition call, with the arm's own collaborators left to the caller.

    ``registry`` defaults to a real store, so a case about the writer refusal
    does not have to say anything about the store and the other way round.
    """
    workspace = FakeWorkspaceProvider()
    port = board(lanes=("A",))
    return build_workflow_engine(
        operation=native_operation(),
        scope_tracker=port,
        scope_registry=InMemoryJobRegistry() if registry is _UNSET else registry,
        scope_status=status,
        criteria=TrackerCriteria(tracker=port),
        config=AppConfig(
            write_back=WriteBackSettings(max_verify_rounds=2),
            ticket_review_mode=TicketReviewMode.REVIEWED,
            max_iterations=1,
            retry_max_attempts=1,
            retry_initial_interval=0.1,
        ),
        repositories=(),
        agent_service=AgentService(
            git_base_url="https://github.com",
            executor=NativeExecutor([]),
            workspace=workspace,
            persister=FakeChangePersister(),
        ),
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        workspace=workspace,
        merger=FakeBranchMerger(),
        artifact_persister=FakeArtifactPersister(),
        ref_publisher=FakeRefPublisher(),
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        github_api=None,
        checkpointer=InMemorySaver(),
    )


def test_a_scope_arm_composed_without_a_status_writer_refuses():
    """Refused at construction rather than defaulted to a writer that is silent.

    A scope arm handed no writer would walk, certify nothing and say nothing,
    which is the state this lane exists to end — so the absence is a
    composition error and not a mode.
    """
    with pytest.raises(ValueError, match="scope status writer"):
        compose_scope_arm(status=None)


def test_a_scope_arm_composed_without_a_job_registry_refuses():
    """Refused for the shape of reason the writer's absence is refused for.

    An arm composed without a record store cannot see another job over the
    same scope, so it would walk beside that job over every lane of it — and
    a store built after the arm could not be the one the arm reads (KOD-880).
    """
    with pytest.raises(ValueError, match="job registry"):
        compose_scope_arm(status=FakeScopeStatusWriter(), registry=None)


def test_the_same_composition_with_both_builds():
    """The control: nothing else about that call is what either refusal is about."""
    assert compose_scope_arm(status=FakeScopeStatusWriter()) is not None


# ---------------------------------------------------------------------------
# KOD-479 / KOD-471 — the partition: a walk or a post that raises ends the job
# with no terminal event on the stream and no status update anywhere.
# ---------------------------------------------------------------------------


class RefusingStatusWriter(FakeScopeStatusWriter):
    """A container the write cannot reach, recording nothing when it refuses."""

    async def post_status_update(self, *, ref, body) -> None:
        raise TrackerUnavailableError("the container status surface is unavailable")


async def test_a_status_update_that_cannot_be_posted_ends_the_job_with_no_event():
    """The post precedes the yield, so a post that raises leaves no report.

    Driven through the queue, because the half of the partition this states is
    the job's own outcome: the walk ran, nothing was posted, and the stream
    carries no report for a consumer to read as a finished scope.
    """
    harness = runtime(port=board(lanes=("A",)), status=RefusingStatusWriter())
    queue = build_job_queue(
        settings=JobQueueSettings(),
        workflow_engine=harness.engine,
        registry=harness.registry,
    )
    handler = AgentHandler(harness.service, SUPPRESS_ALL_SKILLS, queue=queue)
    await queue.start()
    try:
        record = await handler.submit_workflow(
            WorkflowRequest(
                prompt="Run approved scope",
                repo_url=ORIGIN,
                scope={"kind": "project", "key": SCOPE.key},
            ),
            lane="scope-requests",
        )
        payloads = []
        async with asyncio.timeout(WALK_BOUND_SECONDS):
            async for item in handler.attach_job(job_id=record.job_id):
                payloads.append(item)
        finished = await queue.get(job_id=record.job_id)
    finally:
        await queue.stop()

    # The walk itself ran: the observations are on the stream.
    assert [item for item in payloads if item["type"] == "scope_walk"]
    assert "scope_terminal" not in {item["type"] for item in payloads}
    assert finished.outcome is WorkflowOutcome.engine_error
    assert harness.status.posts == []


async def test_a_walk_that_raises_emits_no_terminal_event(monkeypatch):
    """A walk that raised never reaches the report, so none is posted either.

    The other half of the same partition: the reading the terminal would have
    reported on is the one that failed, so there is nothing to report and the
    run ends where it broke.
    """
    port = board(lanes=("A", "B"))
    harness = runtime(port=port, lanes=("A", "B"))
    original = port.scope_issues

    async def current_scope(*, ref):
        if ref == SCOPE:
            raise TrackerUnavailableError("current scope unavailable")
        return await original(ref=ref)

    events = []
    with pytest.raises(TrackerUnavailableError, match="current scope unavailable"):
        async with asyncio.timeout(WALK_BOUND_SECONDS):
            async for event in drive(harness):
                events.append(event)
                if isinstance(event, ScopeLaneEvent) and isinstance(
                    event.event, WorkflowIterationEvent
                ):
                    monkeypatch.setattr(port, "scope_issues", current_scope)

    assert terminals(events) == []
    assert harness.status.posts == []


# ---------------------------------------------------------------------------
# KOD-479 — the write set is closed: the terminal's only tracker write is the
# container status update, over every fixture the walk can end in.
# ---------------------------------------------------------------------------


def recorded(port, **rest):
    """The composed walk over a port that records every write it makes."""
    journal = Journal()
    return runtime(port=RecordingTracker(port, journal), **rest), journal


async def attributed(harness, journal, **rest):
    """Every port write the terminal made, isolated by WHEN it ran.

    The generator runs only when it is pulled, and the only code between the
    last observation's yield and the terminal's is the report itself, so the
    writes after the last observation are the terminal's and no other's.

    The marks come back as well, so a test can say what the walk itself wrote
    BEFORE its last observation and not only what the terminal did after it.
    """
    marks: list[int] = []
    events = []
    async with asyncio.timeout(WALK_BOUND_SECONDS):
        async for event in drive(harness, **rest):
            events.append(event)
            if isinstance(event, ScopeWalkEvent):
                marks.append(len(journal.writes))
    assert marks, "a walk that observed nothing states nothing about attribution"
    return events, journal.writes[marks[-1] :], marks


async def nothing_to_close() -> None:
    """The closer a fixture that opened no transport hands back."""


def evaluations_for(*, passing=(), failing=()):
    """Two gradings per lane, in the order the lanes are offered in.

    Only a lane that reaches a grading has one scripted: an echo left over
    for a lane that never graded would be spent on the next lane and grade it
    against another lane's Check.
    """
    return [
        native_evaluation(
            failed=key in failing, checks={f"{key}/check": f"{key} live Check  bytes"}
        )
        for key in (*failing, *passing)
        for _ in range(2)
    ]


def converged_lane():
    return recorded(board(lanes=("A",))), {}, 1, nothing_to_close


def two_converged_lanes():
    return recorded(board(lanes=("A", "B")), lanes=("A", "B")), {}, 1, nothing_to_close


def unapproved_lane():
    return recorded(unapproved_members(lanes=("A",))), {}, 1, nothing_to_close


def blocked_lane():
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="A")] = frozenset()
    return recorded(port, lanes=("A", "B")), {}, 1, nothing_to_close


def lane_with_a_malformed_obligation():
    """A criterion carrying no Check: the lane's own entry refuses it."""
    port = board(lanes=("A",))
    port.issues["A/check"] = port.issues["A/check"].model_copy(
        update={"body": "**Evidence:** — and no Check field at all"}
    )
    return recorded(port), {}, 1, nothing_to_close


def one_lane_gave_up():
    """A never passes its grading and is put back; B passes.

    The exit through a lane that spent its rounds without closing what it
    owed, which is also where the walk's own put-back lands in the journal.
    """
    return (
        recorded(
            board(lanes=("A", "B")),
            lanes=("A", "B"),
            evaluations=evaluations_for(failing=("A",), passing=("B",)),
        ),
        {},
        1,
        nothing_to_close,
    )


def one_lane_failed():
    """B's base cannot be resolved, so its turn ends before it launches."""
    harness, journal = recorded(
        board(lanes=("A", "B")),
        lanes=("A", "B"),
        evaluations=evaluations_for(passing=("A",)),
    )
    resolver = harness.engine._scoped_arm._resolver
    resolve = resolver.resolve

    async def refuse_one(*, issue_key, **rest):
        if issue_key == "B":
            raise BaseResolutionError(
                "the lane's base cannot be resolved", issue_id=issue_key
            )
        return await resolve(issue_key=issue_key, **rest)

    resolver.resolve = refuse_one
    return (harness, journal), {}, 1, nothing_to_close


def already_done():
    """Both lanes were finished elsewhere, so no lane is ever offered."""
    port = board(lanes=("A", "B"))
    finish_by_hand(port, "A")
    finish_by_hand(port, "B")
    return recorded(port, lanes=("A", "B"), evaluations=[]), {}, 1, nothing_to_close


def all_done_open_prs():
    """Both lanes commit and open a pull request on a forge origin.

    The exit the write set has to be closed over as well: a walk whose lanes
    actually delivered writes far more at the port than a forge-less one, so
    an attributed slice that is empty here says more than one that is empty
    over a fixture that never pushed.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    journal = Journal()
    forge = _make_client(ScopeForgeWire(head_sha_of=repos.head_of))
    harness = resumable(
        port=RecordingTracker(board(lanes=("A", "B")), journal),
        repos=repos,
        lanes=("A", "B"),
        origin=FORGE_ORIGIN,
        forge=forge,
        trunk="main",
    )
    return (harness, journal), {"origin": FORGE_ORIGIN}, 1, forge.close


def initiative_scope():
    scope = ScopeRef(kind=ScopeKind.INITIATIVE, key="scoped-initiative")
    port = approve_container(board(lanes=("A",)), scope)
    port.scope_memberships[scope] = ("A",)
    return recorded(port), {"scope": scope}, 1, nothing_to_close


def issue_scope():
    """No container, so no status surface and no write at all."""
    scope = ScopeRef(kind=ScopeKind.ISSUE, key="A")
    port = board(lanes=("A",))
    port.scope_memberships[scope] = ("A",)
    return recorded(port), {"scope": scope}, 0, nothing_to_close


WRITE_SET_FIXTURES = (
    converged_lane,
    two_converged_lanes,
    unapproved_lane,
    blocked_lane,
    lane_with_a_malformed_obligation,
    one_lane_gave_up,
    one_lane_failed,
    already_done,
    all_done_open_prs,
    initiative_scope,
    issue_scope,
)


@pytest.mark.parametrize(
    "fixture", WRITE_SET_FIXTURES, ids=[f.__name__ for f in WRITE_SET_FIXTURES]
)
async def test_the_terminals_only_tracker_write_is_the_container_status_update(
    fixture,
):
    (harness, journal), driving, expected, closer = fixture()

    try:
        events, writes, _ = await attributed(harness, journal, **driving)
    finally:
        await closer()

    assert len(terminals(events)) == 1
    # Nothing on any issue surface, and nothing on the container description:
    # the report does not reach the tracker through the port at all.
    assert writes == []
    assert len(harness.status.posts) == expected
    # And on the scope's own container, never on a lane or another key.
    assert [ref for ref, _ in harness.status.posts] == [
        driving.get("scope", SCOPE)
    ] * expected


@pytest.mark.parametrize(
    "fixture", WRITE_SET_FIXTURES, ids=[f.__name__ for f in WRITE_SET_FIXTURES]
)
async def test_the_terminal_writes_no_description_and_no_issue_surface(fixture):
    """Stated against the derived write surface rather than a list written here."""
    (harness, journal), driving, _, closer = fixture()

    try:
        _, writes, _ = await attributed(harness, journal, **driving)
    finally:
        await closer()

    assert {write.method for write in writes} & artifact_writes() == set()
    assert "edit_description" not in {write.method for write in writes}


async def test_the_attribution_isolates_the_terminal_from_the_walks_own_writes():
    """Non-vacuity: the walk wrote at the port, and none of it was the terminal's.

    Without this the empty attributed slice above would be satisfied by a
    journal that recorded nothing at all.
    """
    (harness, journal), driving, _, _ = converged_lane()

    _, writes, _ = await attributed(harness, journal, **driving)

    assert journal.writes, "a walk that wrote nothing states nothing about attribution"
    assert writes == []


async def test_the_walks_own_put_back_is_before_the_last_mark_and_not_the_terminals():
    """The put-back of a lane that gave up is the walk's, not the terminal's.

    A write surface listed by hand in the recording port would leave this one
    unrecorded and every attributed slice would still be empty; asserting the
    method lands BEFORE the last observation is what makes the port's coverage
    of it part of this criterion rather than another's.
    """
    (harness, journal), driving, _, _ = one_lane_gave_up()

    _, writes, marks = await attributed(harness, journal, **driving)

    assert "restore_workflow_state" in {
        write.method for write in journal.writes[: marks[-1]]
    }
    assert writes == []


def test_the_terminal_holds_no_tracker_port_to_write_through():
    """The collaborators are a record reader, the container-status role, the gate.

    Read off the constructor rather than asserted about instances: a port
    handed to the terminal later would be a write set nothing here bounds.
    The role beside the port grew a read (KOD-879); the port grew nothing,
    and the parameter set is the same three it always was.
    """
    parameters = inspect.signature(ScopeTerminal.__init__).parameters
    assert set(parameters) == {"self", "records", "status", "gate"}
    held = {
        name: value.annotation for name, value in parameters.items() if name != "self"
    }
    assert held["status"] is ScopeStatusUpdates
    assert TrackerPort not in held.values()


def test_the_terminals_source_names_one_write_and_it_is_the_status_update():
    """Every write of the whole derived surface the module names, which is one."""
    source = inspect.getsource(terminal_module)
    named = {method for method in artifact_writes() if method in source}
    assert named == {"post_status_update"}


# ---------------------------------------------------------------------------
# KOD-480, first clause — a scope whose lanes all hold open, unmerged pull
# requests derives the finished outcome, and no merge fact is read.
# ---------------------------------------------------------------------------


async def test_a_scope_of_open_unmerged_pull_requests_derives_the_finished_outcome():
    """Every lane's terminal act is complete; none of them is merged.

    Over repositories that actually commit and a forge that actually opens a
    pull request per lane, so the open-and-unmerged state is the fixture's own
    observation rather than a value written here. The wire raises on a merge,
    which is what makes the absence of one a fact about the run.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A", "B"))
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        harness = resumable(
            port=port,
            repos=repos,
            lanes=("A", "B"),
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
        )

        # Marked at every observation, so what the terminal itself asked the
        # forge is separable from what each lane's delivery asked it. Every
        # request and not the by-number reads alone: a by-head listing, which
        # the wire answers with a state and a merge fact, is a merge fact read
        # as much as a read of one pull request is.
        asked: list[int] = []
        events = []
        async with asyncio.timeout(WALK_BOUND_SECONDS):
            async for event in drive(harness, origin=FORGE_ORIGIN):
                events.append(event)
                if isinstance(event, ScopeWalkEvent):
                    asked.append(len(wire.requests))

        report = terminals(events)[0]
        assert [lane.done for lane in report.lanes] == [True, True]
        assert report.outcome is WorkflowOutcome.scope_converged
        # One pull request per lane, open, recorded on that lane's own record.
        assert len(wire.creates) == 2
        for lane in report.lanes:
            assert lane.pr is not None
            assert lane.pr.state == "open"
            recorded = await lane_record(port, lane.issue)
            assert recorded.pr == lane.pr
        # The deliveries read their own pull requests; the terminal read none,
        # and nothing merged — the wire raises on a merge, so its absence from
        # the requests is a fact about this run rather than an omission.
        assert wire.pr_reads, "a run that asked the forge nothing controls nothing"
        assert len(wire.requests) == asked[-1]
        assert not [
            request for request in wire.requests if request.url.path.endswith("/merge")
        ]
    finally:
        await forge.close()


async def test_a_lane_with_no_open_pull_request_still_derives_from_its_criteria():
    """The reading is criterion states, so a lane with no delivery is done too.

    The counterpart of the case above and the reason the outcome cannot be a
    function of a merge: this fixture has no forge behind its origin, so no
    lane could ever record a pull request, and the scope is finished anyway.
    """
    harness = runtime(port=board(lanes=("A",)))

    report = terminals(await bounded_walk(harness))[0]

    assert [(lane.done, lane.pr) for lane in report.lanes] == [(True, None)]
    assert report.outcome is WorkflowOutcome.scope_converged


#: How a merge, or a pull request's own lifecycle, is named in this source.
MERGE_STATE_NAMES = frozenset(
    {"PRState", "PRStateReader", "PRLifecycle", "read_pr_state", "lifecycle", "merged"}
)

#: The act the terminal is, from which the rest of it is reached.
TERMINAL_SEED = "kodezart.services.scope_terminal"

#: The prefixes the scan follows, transitively, out of the seed. The
#: ``kodezart.types.domain`` package is NOT followed: the vector's module
#: imports the v0.2 fire event's module, whose own delivery field names a
#: merge legitimately, and that is not a fact about this lane. The one typed
#: module of the terminal is therefore named rather than reached.
TERMINAL_PACKAGES = ("kodezart.services.", "kodezart.domain.")
TERMINAL_VECTOR = "kodezart.types.domain.scope_terminal"


def terminal_modules() -> set[str]:
    """Every module the terminal reaches: the act, its readings, its vector.

    Derived out of the act's own import nodes rather than listed here, so a
    module the terminal starts depending on is scanned without this test being
    edited — and a scan that had lost its surface could not report the same
    empty result as a clean one.

    Followed transitively, because what the guards below say is that no module
    of the terminal REACHES a forbidden thing: a module the act imports through
    another module is reached just as much as one it names itself. The walk is
    bounded by the modules already taken, so an import cycle terminates.
    """
    reached: set[str] = set()
    pending = [TERMINAL_SEED]
    while pending:
        module = pending.pop()
        if module in reached:
            continue
        reached.add(module)
        tree = ast.parse(path_of(module).read_text(encoding="utf-8"))
        pending.extend(imported_modules(tree, TERMINAL_PACKAGES))
    return reached | {TERMINAL_VECTOR}


def merge_state_sites(source: str, *, label: str) -> list[str]:
    """Every place *source* names a merge or a pull request's lifecycle."""
    tree = ast.parse(source)
    sites: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "kodezart.types.domain.pr_state"
        ):
            sites.append(f"{label}:{node.lineno}: pr_state import")
        elif isinstance(node, ast.ImportFrom | ast.Import):
            sites.extend(
                f"{label}:{node.lineno}: {alias.name}"
                for alias in node.names
                if alias.name in MERGE_STATE_NAMES
            )
        elif isinstance(node, ast.Attribute) and node.attr in MERGE_STATE_NAMES:
            sites.append(f"{label}:{node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in MERGE_STATE_NAMES:
            sites.append(f"{label}:{node.lineno}: {node.id}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in MERGE_STATE_NAMES
        ):
            # Equality and not a substring, so prose about a merge does not
            # trip it while a comparison against the state does.
            sites.append(f'{label}:{node.lineno}: "{node.value}"')
    return sites


def test_no_module_of_the_terminal_names_a_merge_or_a_pull_request_lifecycle():
    modules = terminal_modules()
    # Non-vacuity: the surface actually reaches the act, both of its readings
    # and the reader that produces the recorded delivery in the first place.
    assert {
        TERMINAL_SEED,
        TERMINAL_VECTOR,
        "kodezart.domain.scope_terminal",
        "kodezart.services.lane_records",
    } <= modules
    offenders = {
        module: sites
        for module in sorted(modules)
        if (
            sites := merge_state_sites(
                path_of(module).read_text(encoding="utf-8"), label=module
            )
        )
    }
    assert offenders == {}


#: One control per shape the detector claims to see that the scanned surface
#: cannot supply: the surface is expected to name nothing, so a comparison
#: against the state as a literal has no control there at all.
MERGE_STATE_CONTROLS = (
    ('state == "merged"', '"merged"'),
    ("record.pr.lifecycle", ".lifecycle"),
)


@pytest.mark.parametrize(
    ("source", "expected"),
    MERGE_STATE_CONTROLS,
    ids=[source for source, _ in MERGE_STATE_CONTROLS],
)
def test_the_merge_state_detector_sees_each_shape_it_claims_to(source, expected):
    sites = merge_state_sites(source, label="control")
    assert len(sites) == 1 and expected in sites[0], sites


def test_the_merge_state_detector_finds_the_modules_that_do_name_one():
    """The control, derived from the tree rather than picked.

    Every production module naming the lifecycle enum at all is a module this
    detector must see; a detector finding nothing there would report the same
    empty set over the terminal and say nothing.
    """
    controls = [
        path
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if "PRLifecycle" in path.read_text(encoding="utf-8")
    ]
    assert controls, "no production module names a pull request's lifecycle"
    unseen = [
        path.name
        for path in controls
        if not merge_state_sites(path.read_text(encoding="utf-8"), label=path.name)
    ]
    assert unseen == []


#: The three columns of ``LanePR``, which is the whole of a recorded delivery:
#: reading any one of them off a record is the terminal reading a pull request.
PR_COLUMN_NAMES = frozenset({"state", "number", "url"})


def pr_column_sites(source: str, *, label: str) -> list[str]:
    """Every place *source* reads a column of a pull request off something."""
    tree = ast.parse(source)
    return [
        f"{label}:{node.lineno}: .{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in PR_COLUMN_NAMES
    ]


def test_the_terminal_reads_no_column_of_a_recorded_pull_request():
    """The act names none of the three columns, pinned structurally.

    The behavioural tests hold the done column against every value the three
    columns can take, but they can only hold the values a fixture supplies. A
    done column flipped on a value outside that set — a number above some
    threshold, a state string no fixture spells — would pass every one of them
    and still be the terminal reading a delivery. So the pin is structural:
    the act does not read the columns at all, at any value, and a flip gated
    on one cannot be written without this failing.

    The seed alone, because this is about the act: the readings it reaches
    carry a record's pull request through and legitimately name its columns,
    and the vector's module declares them.
    """
    source = path_of(TERMINAL_SEED).read_text(encoding="utf-8")
    # Non-vacuity: the act does reach a recorded delivery, so an empty result
    # is the act declining to read the value rather than never holding one.
    read = {
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
    }
    assert "pr" in read, "the act reads no recorded delivery to state anything about"
    assert pr_column_sites(source, label=TERMINAL_SEED) == []


#: The one shape the column scan claims to see, which is a column read off a
#: record — so a scan that had lost its detector could not report the same
#: empty result over the seed as a clean one.
PR_COLUMN_CONTROLS = (("record.pr.state", ".state"),)


@pytest.mark.parametrize(
    ("source", "expected"),
    PR_COLUMN_CONTROLS,
    ids=[source for source, _ in PR_COLUMN_CONTROLS],
)
def test_the_pull_request_column_detector_sees_each_shape_it_claims_to(
    source, expected
):
    sites = pr_column_sites(source, label="control")
    assert len(sites) == 1 and expected in sites[0], sites


# ---------------------------------------------------------------------------
# KOD-585 — the report states what it read, and it reads no ref, no tree and
# no working directory: no module of the terminal reaches the git port at all.
# ---------------------------------------------------------------------------

#: The concrete adapter package, so a report that imports an implementation is
#: seen without the port itself being named anywhere in it.
GIT_PORT_ADAPTERS = "kodezart.adapters.git"

#: One name per noun the criterion names — a ref on the remote, a ref locally,
#: a tree, a working directory — held against the derived set below, so a
#: derivation that had gone empty cannot report the same clean result as a
#: clean surface. This is a floor under the derivation, not the surface that
#: is scanned: the scanned surface is derived and this set never bounds it.
GIT_PORT_ANCHORS = frozenset(
    {"remote_branch_sha", "current_sha", "tree_of", "worktree_identity"}
)


def git_port_names() -> frozenset[str]:
    """The port's own name and every operation it declares, off its members.

    Derived from the protocol rather than listed, so an operation added to the
    port is covered without this test being edited.

    The whole declared surface, not a read/write split: a scan over every
    operation is stronger than one over the reads alone while needing no verb
    or return-annotation classification, which would be a second derivation
    with controls of its own. The cost of that, stated rather than left to be
    discovered: the derived set holds ordinary words (``commit``, ``push``,
    ``fetch``, ``is_repo``, ``has_changes``, ``diff_summary``, ``add_all``,
    ``reset_hard``), so a report module that grew an innocent ``.commit``
    attribute would trip this. It is green over the scanned surface today, and
    a failure names the offending file and line.
    """
    return frozenset(
        {
            GitService.__name__,
            *(
                name
                for name in dir(GitService)
                if not name.startswith("_")
                and callable(getattr(GitService, name, None))
            ),
        }
    )


def git_port_sites(source: str, *, label: str) -> list[str]:
    """Every place *source* names the git port, an adapter of it, or one of
    its operations.

    Blind spots, stated rather than hidden: a reach through ``getattr`` with a
    computed name is not seen, and a name spelled inside a larger string
    annotation is not seen, because the literal arm is an equality and not a
    substring.
    """
    names = git_port_names()
    sites: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith(GIT_PORT_ADAPTERS)
        ):
            sites.append(f"{label}:{node.lineno}: {node.module}")
        elif isinstance(node, ast.ImportFrom | ast.Import):
            sites.extend(
                f"{label}:{node.lineno}: {alias.name}"
                for alias in node.names
                if alias.name in names
            )
        elif isinstance(node, ast.Attribute) and node.attr in names:
            sites.append(f"{label}:{node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in names:
            sites.append(f"{label}:{node.lineno}: {node.id}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in names
        ):
            # Equality and not a substring, so prose naming a commit does not
            # trip it while a dispatch on the operation's name does.
            sites.append(f'{label}:{node.lineno}: "{node.value}"')
    return sites


def test_no_module_of_the_terminal_reaches_the_git_port():
    """What the report states is what it read, and it reads no ref at all.

    A report makes no claim about its own environment because it takes no
    environment fact: no module of the act, its readings or its vector names
    the git port, an adapter of it, or any operation it declares. The port's
    remote-ref reader belongs to the delivery path, where a lane's run-state
    record reads it — a record, not a report (KOD-585, KOD-118).

    Structural rather than behavioural: a read that RETURNED what the record
    already holds would be invisible to every assertion over a rendered body,
    so the absence is asserted over the syntax tree.

    Non-vacuous in both directions. The name set is held against an anchor per
    noun, so a derivation gone empty cannot report a clean result; the scanned
    surface is held against the five modules this claim is about, one of them
    reached only through another, so neither a surface gone empty nor one that
    had stopped following the imports out can either; and the modules that DO
    read a ref are found from the tree by the two tests below, so a blind
    detector cannot.
    """
    names = git_port_names()
    assert GIT_PORT_ANCHORS <= names, sorted(names)
    modules = terminal_modules()
    assert {
        TERMINAL_SEED,
        TERMINAL_VECTOR,
        "kodezart.domain.scope_terminal",
        "kodezart.services.lane_reports",
        # Reached through ``kodezart.services.lane_records`` and named nowhere
        # in the act, so this is what says the walk is transitive rather than
        # one import deep — which is what "reaches" asks for.
        "kodezart.domain.lane_entry",
    } <= modules
    offenders = {
        module: sites
        for module in sorted(modules)
        if (
            sites := git_port_sites(
                path_of(module).read_text(encoding="utf-8"), label=module
            )
        )
    }
    assert offenders == {}


#: One control per arm the detector claims, because a control for an arm
#: cannot come from the scanned surface: that surface is expected to name
#: nothing, so until these rows existed four of the five arms could be deleted
#: with every assertion still passing.
GIT_PORT_CONTROLS = (
    ("await self._git.remote_branch_sha(cwd, remote, branch)", ".remote_branch_sha"),
    ("def __init__(self, *, git: GitService) -> None: ...", "GitService"),
    (
        "from kodezart.adapters.git.service import SubprocessGitService",
        "kodezart.adapters.git.service",
    ),
    (
        "identity = await git.worktree_identity(cwd, repository_path=path)",
        ".worktree_identity",
    ),
    ('reader = getattr(git, "current_sha")', '"current_sha"'),
)


@pytest.mark.parametrize(
    ("source", "expected"),
    GIT_PORT_CONTROLS,
    ids=[source for source, _ in GIT_PORT_CONTROLS],
)
def test_the_git_port_detector_sees_each_shape_it_claims_to(source, expected):
    sites = git_port_sites(source, label="control")
    assert len(sites) == 1 and expected in sites[0], sites


def test_the_git_port_detector_finds_the_modules_that_do_read_a_ref():
    """The second control, derived from the tree rather than picked.

    Every production module whose text names the remote-ref read is a module
    this detector must see. It is also the other half of the non-vacuity
    proof: these modules really do read a ref, none of them is in the scanned
    surface, and the assertion above is empty anyway — so an empty result
    there is a bounded surface declining to reach the port, not a detector
    that cannot see one.

    The module that DECLARES the port is excluded, and the exclusion is
    derived from the port rather than written down: declaring an operation as
    a ``def`` is not reaching it, and a detector that fired there would be
    detecting declarations.
    """
    declares = path_of(GitService.__module__)
    controls = [
        path
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if "remote_branch_sha" in path.read_text(encoding="utf-8") and path != declares
    ]
    assert controls, "no production module reads a ref off the remote"
    unseen = [
        path.name
        for path in controls
        if not git_port_sites(path.read_text(encoding="utf-8"), label=path.name)
    ]
    assert unseen == []


# ---------------------------------------------------------------------------
# KOD-832 clause 6 — a scratch-shaped scope ends with exactly one status
# update carrying the per-lane vector and the derived outcome, and no other
# terminal write.
# ---------------------------------------------------------------------------

#: A project scope of three lanes, the second held by the first, which is the
#: shape the recorded acceptance run walks. Doubles only: nothing here reaches
#: a live workspace, and the recorded run is that clause's own evidence.
SCRATCH = ScopeRef(kind=ScopeKind.PROJECT, key="scratch-project")
SCRATCH_LANES = ("DUC-1209", "DUC-1210", "DUC-1211")


async def test_a_scratch_shaped_scope_ends_with_exactly_one_status_update():
    """One status update per clean exit, and no other write of the terminal's.

    The whole clause in one fixture: three lanes, the second held by the first,
    on a project scope over a forge that actually opens a pull request per
    lane — so the converged state the vector reports is the stacked one, with
    every lane holding an open, unmerged delivery, and the recorded columns are
    the fixture's own observations rather than values written here.

    Driven twice over the same board and the same container, because
    exactly-one is a property of the terminal running once at a clean exit and
    not of a mark it holds: the second invocation walks the same finished
    scope, renders the same vector, finds it already on the container and
    posts nothing (KOD-879). Nothing is remembered between the two and no mark
    is held — what the second invocation consults is the container's own
    contents, which is why a terminal that deduped from memory is still
    excluded.

    Doubles only — nothing here reaches a live workspace.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    board_port = approve_container(
        board(lanes=SCRATCH_LANES, blocked={"DUC-1210": ("DUC-1209",)}), SCRATCH
    )
    board_port.scope_memberships[SCRATCH] = SCRATCH_LANES
    journal = Journal()
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        harness = resumable(
            port=RecordingTracker(board_port, journal),
            repos=repos,
            lanes=SCRATCH_LANES,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
        )

        # Marked at every observation, so what the terminal itself asked the
        # forge and put on the container is separable from what the lanes did.
        marks: list[int] = []
        asked: list[int] = []
        posted: list[int] = []
        posts_when_reported: int | None = None
        events = []
        async with asyncio.timeout(WALK_BOUND_SECONDS):
            async for event in drive(harness, scope=SCRATCH, origin=FORGE_ORIGIN):
                events.append(event)
                if isinstance(event, ScopeWalkEvent):
                    marks.append(len(journal.writes))
                    asked.append(len(wire.requests))
                    posted.append(len(harness.status.posts))
                elif isinstance(event, ScopeTerminalEvent):
                    posts_when_reported = len(harness.status.posts)
        assert marks, "a walk that observed nothing states nothing about attribution"
        writes = journal.writes[marks[-1] :]

        # Exactly one report, and it is the last thing the stream carries.
        reports = terminals(events)
        assert len(reports) == 1
        assert events[-1] is reports[0]
        report = reports[0]

        # Exactly one status update, on the scope's own container, carrying the
        # per-lane vector and the derived outcome.
        assert len(harness.status.posts) == 1
        posted_ref, body = harness.status.posts[0]
        assert posted_ref == SCRATCH
        assert body == render_scope_status(report)
        assert body.splitlines()[0] == f"Scope outcome: {report.outcome.value}"
        assert [
            line.split()[2] for line in body.splitlines() if line.startswith("- [")
        ] == [*SCRATCH_LANES]

        # The post lands before the event reaches a consumer: nothing was on
        # the container at the walk's last observation, and the update is there
        # by the time the report arrives.
        assert posted[-1] == 0
        assert posts_when_reported == 1

        # The vector covers every lane of the reading, with each lane's own
        # recorded branch and open delivery read back off that lane's record.
        assert [lane.issue for lane in report.lanes] == [*SCRATCH_LANES]
        assert report.outcome is WorkflowOutcome.scope_converged
        for lane in report.lanes:
            recorded = await lane_record(board_port, lane.issue)
            assert lane.branch == recorded.branch
            assert lane.pr is not None
            assert lane.pr.state == "open"
            assert lane.pr == recorded.pr

        # The terminal asked the forge nothing, and nothing merged: the wire
        # raises on a merge, so the absence of one from the requests is a fact
        # about this run rather than an omission.
        assert wire.creates, "a run that opened no delivery controls nothing"
        assert len(wire.requests) == asked[-1]
        assert not [
            request for request in wire.requests if request.url.path.endswith("/merge")
        ]

        # And nothing else: no write on any issue surface and none on the
        # container description is attributable to the terminal.
        assert writes == []
        assert journal.writes, "a walk that wrote nothing states nothing here"
        first_post = harness.status.posts[0]

        # The second clean exit over the same harness. Four ticks, each
        # offering nothing: a lane that is finished and already holds an open
        # delivery is walked past rather than dispatched again.
        second, second_writes, _ = await attributed(
            harness, journal, scope=SCRATCH, origin=FORGE_ORIGIN, job="second-job"
        )
        assert len(ticks_of(second)) == 4
        assert [tick.dispatched for tick in ticks_of(second)] == [()] * 4
        assert len(terminals(second)) == 1
        assert len(harness.status.posts) == 1
        assert harness.status.posts[0] == first_post
        assert render_scope_status(terminals(second)[0]) == first_post[1]
        assert second_writes == []
    finally:
        await forge.close()
