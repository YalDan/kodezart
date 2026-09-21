"""Real request composition, controller and native graphs with external doubles."""

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
import structlog.testing
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.adapters.job_registry import InMemoryJobRegistry
from kodezart.chains.criteria import TrackerCriteria
from kodezart.composition.engine import build_workflow_engine
from kodezart.composition.jobs import build_job_queue
from kodezart.composition.tracker import criteria_stage_label_key
from kodezart.config.app import AppConfig
from kodezart.config.job_queue import JobQueueSettings
from kodezart.config.write_back import WriteBackSettings
from kodezart.core.errors import TrackerUnavailableError
from kodezart.core.protocols import PRCreator
from kodezart.domain.agent import (
    best_iteration_ref,
    generate_ralph_branch_name,
    mint_lane_branches,
)
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import (
    BaseResolutionError,
    ForgeAPIError,
    GitSourceReadError,
    LaneRecordWriteError,
    ScopedExecutionUnavailableError,
    ScopeNotApprovedError,
    ScopePlanRefusalError,
    ScopeReadError,
    WorkspaceError,
)
from kodezart.domain.fire_spec import criterion_field_bodies
from kodezart.domain.git_url import resolve_repo_url
from kodezart.domain.lane_entry import recorded_branches
from kodezart.domain.organize import stage_rows
from kodezart.handlers.agent_handler import AgentHandler
from kodezart.services import scope_runtime
from kodezart.services.agent_service import AgentService
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.lane_state_writer import TrackerLaneStateWriter
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.types.domain.agent import (
    ResultEvent,
    SystemEvent,
    TicketDraftOutput,
    WorkflowCompleteEvent,
    WorkflowConsolidationEvent,
    WorkflowIterationEvent,
    WorkflowScopeBaseEvent,
)
from kodezart.types.domain.branch import (
    BranchRole,
    WorkRef,
    WorkRefRole,
    trunk_base,
)
from kodezart.types.domain.consolidation import (
    ChangesetDigest,
    ConsolidationOutcome,
    ConsolidationStatus,
)
from kodezart.types.domain.job import JobState
from kodezart.types.domain.native_delivery import LaneDeliveryEvent
from kodezart.types.domain.operation import LifecycleStage, RepoEntry, ScopeLabel
from kodezart.types.domain.organize import split_label_key
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.persist import PersistResult, PersistSource
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeLaneEvent, ScopeWalkEvent
from kodezart.types.domain.scope_terminal import ScopeLaneEntry
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.ticket_review import TicketReviewMode
from kodezart.types.domain.tracker import IssuePriority, WorkflowStateKind
from kodezart.types.requests.agent import WorkflowRequest
from tests.adapters.test_github_api import _make_client
from tests.api.v1.test_jobs import _build_app
from tests.chains.test_native_fire import (
    NativeExecutor,
    NativeSourceReader,
    native_evaluation,
    native_operation,
)
from tests.fakes import (
    FIXTURE_EPOCH,
    SUPPRESS_ALL_SKILLS,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeChangePersister,
    FakeDeliveryProbe,
    FakeGitService,
    FakeRefPublisher,
    FakeRepoCache,
    FakeScopeStatusWriter,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_prompt_provider,
    make_tracker_issue,
)
from tests.lane_fixture import (
    TRUNK_BRANCHES,
    TRUNK_SHA,
    LaneRepo,
    ScopeForgeWire,
    base_echo,
    criteria_echo,
)

ORIGIN = "file:///scope-repository.git"
SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="scoped-project")
STAGED = "criteria-staged"


def board(
    *,
    lanes=("A",),
    blocked=None,
    approved=True,
    checks=None,
    priorities=None,
    operation=None,
    staged=True,
):
    """The scope's lanes and their criterion sub-issues.

    *checks* names each lane's criteria; a lane not named there has the one
    criterion every lane has had, under the body every test reads it by.

    *priorities* names a lane's own priority; a lane not named there keeps the
    none every lane has had. A ready set is ranked by effective priority before
    age and every issue here is created at the same instant, so a test about
    which lane a tick offers FIRST states the priorities rather than relying on
    the order the rows happen to be built in.

    *operation* is the deployment this board belongs to, defaulting to the
    native fixture. Its own criteria-stage key and marker prefixes are what
    the lanes carry and what the port answers under, so a test about a
    SHIPPED operation file walks a board labelled the way that file says.
    The fixture operation declares no organize mandates and therefore no
    criteria stage, so it keeps this module's own constant.

    *staged* is the board a walk starts from: every lane already carries the
    run-stage markers. Every one of them, read off the operation's own table:
    a lane holding the criteria marker and not the ticket marker would make
    the run's first stage open a session, so a board meant to be past the
    stages has to carry each stage's marker. A test about the stages
    themselves passes False, so the markers are what the run has to put there.
    """
    operation = native_operation() if operation is None else operation
    stage_key = criteria_stage_label_key(operation) or STAGED
    stage_markers = frozenset(
        split_label_key(row.spec.terminal_marker_key)[1]
        for row in stage_rows(
            operation.resolve_organize_mandates(), under_approval=True
        )
    ) or frozenset({stage_key})
    rows = []
    for key in lanes:
        rows.append(
            make_tracker_issue(
                key,
                body=f"Exact native subject {key}  with spaces\n",
                blocked_by=(blocked or {}).get(key, ()),
                issue_labels=stage_markers if staged else frozenset(),
                priority=(priorities or {}).get(key, IssuePriority.NONE),
            )
        )
        for name in (checks or {}).get(key, ("check",)):
            label = key if name == "check" else f"{key}/{name}"
            rows.append(
                make_tracker_issue(
                    f"{key}/{name}",
                    parent_key=key,
                    issue_labels=frozenset({"criterion"}),
                    body=f"**Check:** {label} live Check  bytes\n**Evidence:** —",
                )
            )
    # The addressed scope is a container, so the entry's approval question is
    # asked of it; the per-member seeds stay, because the fixture's issues
    # carry no project and the ready read still asks each of them (KOD-425).
    members = {
        ScopeRef(kind=ScopeKind.ISSUE, key=key): frozenset({ScopeLabel.APPROVED})
        for key in lanes
        if approved
    }
    return FakeTrackerPort(
        issues=rows,
        scope_memberships={SCOPE: tuple(lanes)},
        criteria_stage_label_key=stage_key,
        # The board reads its markers under the operation the engine writes
        # them under; a port with no prefixes could answer for no lane.
        marker_prefixes=operation.marker_prefixes,
        scope_containers=[
            ScopeContainer(
                ref=SCOPE,
                name="scoped project",
                description="",
                url="https://tracker.invalid/project/scoped-project",
            )
        ],
        scope_label_members=(
            {**members, SCOPE: frozenset({ScopeLabel.APPROVED})}
            if approved
            else members
        ),
    )


def approve_container(port, ref):
    """Seed *ref* on *port* as a container the entry admits, and return *port*.

    A case addressing a run at a scope other than ``SCOPE`` has to say about
    it the two things the board says about ``SCOPE``: the container metadata
    the entry's approval question reads, and the approval carried on it.
    """
    port.scope_containers[ref] = ScopeContainer(
        ref=ref,
        name=f"{ref.kind.value} {ref.key}",
        description="",
        url=f"https://tracker.invalid/{ref.kind.value}/{ref.key}",
    )
    port.scope_label_members[ref] = frozenset({ScopeLabel.APPROVED})
    return port


def unapproved_members(**rest):
    """A board whose container carries the approval and whose members do not.

    The entry asks the ADDRESSED scope, which is the container, so a run of
    this board passes it and every lane is then filtered at the per-lane
    approval read — the state a case about that filter needs. ``approved=False``
    alone withholds the container's label too, and such a run is refused before
    it reads a member.
    """
    port = board(approved=False, **rest)
    port.scope_label_members[SCOPE] = frozenset({ScopeLabel.APPROVED})
    return port


def opened_branch(acquisitions, *, after: int = 0):
    """The first acquisition at or after *after* that names a branch.

    The pre-loop question step opens a detached tree of its own, so the
    loop's acquisition is the first one that NAMES a branch rather than the
    first one by position.
    """
    return next(
        call for call in acquisitions[after:] if call.get("branch_name") is not None
    )


class RemoteGit(FakeGitService):
    async def remote_branch_sha(self, cwd, remote, branch):
        self.calls.append(("remote_branch_sha", cwd, remote, branch))
        return ("b" if branch in {"trunk", "main"} else "a") * 40


class ObservedNativeExecutor(NativeExecutor):
    """Script the SDK's real opening/result pair for attributed node sessions."""

    def __init__(self, evaluations):
        super().__init__(evaluations)
        self.run_identities = []

    async def stream(self, **kwargs):
        self.run_identities.append(kwargs.get("run_identity"))
        async for event in super().stream(**kwargs):
            if isinstance(event, ResultEvent):
                yield SystemEvent(subtype="init", data={"session_id": event.session_id})
            yield event


@dataclass
class Harness:
    """The composed engine and every double outside it, each named.

    The version-control service, the persister and the merger are held here
    rather than reached through the service's private attributes, so a test
    asserting that something called none of them reads each double's own log.
    """

    engine: object
    port: FakeTrackerPort
    executor: NativeExecutor
    service: AgentService
    artifacts: FakeArtifactPersister
    saver: InMemorySaver
    workspace: FakeWorkspaceProvider
    status: FakeScopeStatusWriter
    git: object
    persister: object
    merger: object
    registry: InMemoryJobRegistry
    """The record store the composed scope arm reads liveness from.

    Held here so a test that also builds a queue builds it on the SAME store:
    two stores would let the entry's refusal read an empty one while the queue
    wrote the other."""


def runtime(
    *,
    port=None,
    lanes=("A",),
    saver=None,
    evaluations=None,
    trunk="trunk",
    origin=ORIGIN,
    forge=None,
    persister=None,
    git=None,
    source=None,
    workspace=None,
    merger=None,
    ref_publisher=None,
    max_iterations=1,
    operation=None,
    status=None,
    organize=None,
    executor=None,
):
    """The composed engine over external doubles.

    *persister*, *git*, *source* and *workspace* default to today's
    no-commit doubles; a test about what a walk leaves on the board supplies
    repositories that actually commit, so every recorded fact comes from an
    observation of one.
    """
    port = port or board(lanes=lanes)
    executor = (
        ObservedNativeExecutor(
            evaluations
            or [
                native_evaluation(checks={f"{key}/check": f"{key} live Check  bytes"})
                for key in lanes
                for _ in range(2)
            ]
        )
        if executor is None
        else executor
    )
    git = git if git is not None else RemoteGit()
    # The workspace reports its identity through the same Git double the rest
    # of the fixture reads, so a prepared tree and the head it was cut at are
    # one repository's answer rather than two doubles'.
    workspace = FakeWorkspaceProvider(git=git) if workspace is None else workspace
    persister = FakeChangePersister() if persister is None else persister
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor,
        workspace=workspace,
        persister=persister,
    )
    artifacts = FakeArtifactPersister()
    status = FakeScopeStatusWriter() if status is None else status
    registry = InMemoryJobRegistry()
    saver = saver or InMemorySaver()
    merger = (
        FakeBranchMerger(
            consolidation_outcomes=[
                ConsolidationOutcome(
                    status=ConsolidationStatus.FAST_FORWARDED,
                    feature_tip_sha="a" * 40,
                )
                for _ in lanes
            ],
        )
        if merger is None
        else merger
    )
    # Pair the fake filesystem/Git boundary with its immutable-source double.
    # The production builder, native owner and graph remain actual consumers.
    with pytest.MonkeyPatch.context() as external:
        external.setattr(
            "kodezart.composition.engine.SubprocessGitSourceReader",
            NativeSourceReader if source is None else (lambda: source),
        )
        engine = build_workflow_engine(
            operation=native_operation() if operation is None else operation,
            config=AppConfig(
                write_back=WriteBackSettings(max_verify_rounds=2),
                ticket_review_mode=TicketReviewMode.REVIEWED,
                max_iterations=max_iterations,
                retry_max_attempts=1,
                retry_initial_interval=0.1,
                **({} if organize is None else {"organize": organize}),
            ),
            repositories=(RepoEntry(url=origin, trunk=trunk),),
            agent_service=service,
            git=git,
            cache=FakeRepoCache(),
            workspace=workspace,
            merger=merger,
            artifact_persister=artifacts,
            ref_publisher=FakeRefPublisher()
            if ref_publisher is None
            else ref_publisher,
            prompts=make_prompt_provider(),
            skills=SUPPRESS_ALL_SKILLS,
            gate=PassThroughGate(),
            github_api=forge,
            checkpointer=saver,
            criteria=TrackerCriteria(tracker=port),
            scope_tracker=port,
            scope_registry=registry,
            scope_status=status,
        )
    return Harness(
        engine,
        port,
        executor,
        service,
        artifacts,
        saver,
        workspace,
        status,
        git,
        persister,
        merger,
        registry,
    )


def drive(harness, *, job="scope-job", scope=SCOPE, origin=ORIGIN, path=None):
    return harness.engine.run(
        prompt="Request prose is not the native subject",
        repo_path=path,
        repo_url=origin,
        base_spec=trunk_base("unused-request-default"),
        scope=scope,
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=[],
        cache_key=job,
    )


def lane_failures(events):
    """Every lane failure the walk contained, as its last observation names them."""
    observations = [
        event.observation for event in events if isinstance(event, ScopeWalkEvent)
    ]
    return observations[-1].failed_lanes if observations else ()


async def walk_reporting(harness, *, kind, match="", **rest):
    """Drive a walk one lane of which fails, and read that failure off it.

    A lane's own refusal no longer ends the run (KOD-841): it is contained at
    the walk's lane boundary and named on the observation, so a test about
    that refusal asserts its type and message there instead of catching it.
    """
    events = await bounded_walk(harness, **rest)
    failures = lane_failures(events)
    assert [failure.error.error_kind for failure in failures] == [kind]
    assert match in failures[0].error.error
    return events


async def test_request_queue_constructor_reaches_real_native_graph_without_child_jobs():
    # Over repositories that actually commit (``resumable``, below): B's base
    # is A's recorded deliverable branch, so A has to leave a record and push
    # the branch it names before B can be prepared at all (KOD-842).
    harness = resumable(
        repos=WalkRepos(),
        port=board(lanes=("A", "B"), blocked={"B": ("A",)}),
        lanes=("A", "B"),
    )
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
        async with asyncio.timeout(15):
            async for item in handler.attach_job(job_id=record.job_id):
                payloads.append(item)
        assert not [item for item in payloads if item["type"] == "error"]
        nested = [item for item in payloads if item["type"] == "scope_lane"]
        iterations = [
            item for item in nested if item["event"]["type"] == "workflow_iteration"
        ]
        assert [item["laneKey"] for item in iterations] == ["A", "B"]
        assert (
            iterations[0]["event"]["evaluation"]["criteriaResults"][0]["criterionId"]
            == "A/check"
        )
        observations = [
            item["observation"] for item in payloads if item["type"] == "scope_walk"
        ]
        assert observations[-1]["dispatched"] == ["A", "B"]
        assert observations[-1]["unresolvedCriteria"] == []
        assert "workflow_complete" not in {item["type"] for item in payloads}
        # One terminal report per invocation, at the one clean exit.
        assert [item["type"] for item in payloads].count("scope_terminal") == 1
        finished = await queue.get(job_id=record.job_id)
        assert finished.state is JobState.TERMINAL
        assert finished.outcome is WorkflowOutcome.scope_converged
        assert list(queue.registry.records) == [record.job_id]
        assert harness.port.claim_writes == []
        # The only bodies the walk wrote are the two Evidence rows its own
        # evaluations stamped; no child job and no claim was written at all.
        assert {key for key, _, _ in harness.port.issue_writes} == {
            "A/check",
            "B/check",
        }
        assert harness.artifacts.persist_calls == []
        assert (
            "Exact native subject A  with spaces"
            in harness.executor.execution_prompts[0]
        )
        assert "Request prose" not in harness.executor.execution_prompts[0]
    finally:
        await queue.stop()


@pytest.mark.parametrize("change", ["approval", "membership", "check"])
async def test_next_lane_uses_current_approval_membership_and_check(change):
    port = board(lanes=("A", "B"))
    harness = runtime(port=port, lanes=("A", "B"))
    events = []
    async with asyncio.timeout(WALK_BOUND_SECONDS):
        async for event in drive(harness):
            events.append(event)
            if isinstance(event, ScopeLaneEvent) and isinstance(
                event.event, WorkflowIterationEvent
            ):
                if event.lane_key != "A":
                    continue
                if change == "approval":
                    port.scope_label_members[
                        ScopeRef(kind=ScopeKind.ISSUE, key="B")
                    ] = frozenset()
                elif change == "membership":
                    port.scope_memberships[SCOPE] = ("A",)
                else:
                    port.issues["B/check"] = port.issues["B/check"].model_copy(
                        update={
                            "body": "**Check:** amended B Check  exact bytes\n"
                            "**Evidence:** —"
                        }
                    )
    launched = [
        event.lane_key
        for event in events
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, WorkflowIterationEvent)
    ]
    assert launched == (["A", "B"] if change == "check" else ["A"])
    last = [event.observation for event in events if isinstance(event, ScopeWalkEvent)][
        -1
    ]
    if change == "approval":
        assert last.unapproved_lanes == ("B",)
        assert "B/check" in last.unresolved_criteria
    if change == "check":
        assert "amended B Check  exact bytes" in harness.executor.execution_prompts[-1]
        assert "B live Check  bytes" not in harness.executor.execution_prompts[-1]


async def test_an_unapproved_scope_is_refused_and_a_scope_at_rest_is_observed():
    """Three different outcomes, and none of them is silence.

    A scope nobody approved is refused by type before a single member is
    read, so no observation is produced at all. An approved container whose
    member is unapproved passes the entry and is filtered one lane at a time,
    which is reported and leaves the lane owing. A scope that IS approved
    throughout and has nothing left to do is observed once, with nothing
    dispatched. The old assertion here compared the first two observations;
    each of the three now stands on its own.
    """
    port = board(approved=False)
    harness = runtime(port=port)

    def refuse(name):
        async def read(**kwargs):
            raise AssertionError(f"a member was read before the refusal: {name}")

        return read

    for name in ("scope_issues", "read_planning_issue", "read_issue"):
        setattr(port, name, refuse(name))
    events = []
    with pytest.raises(ScopeNotApprovedError) as caught:
        async with asyncio.timeout(WALK_BOUND_SECONDS):
            async for event in drive(harness):
                events.append(event)
    # Collected outside the comprehension: a comprehension inside the raises
    # block discards whatever was yielded before the raise, so it cannot say
    # that nothing was yielded at all. Bounded under this module's one bound,
    # like every other walk here.
    assert events == []
    assert caught.value.ref == SCOPE
    # The refusal's type and rendering are what the job's error event and the
    # lane-failure path carry, so both are contract, not incidental.
    assert isinstance(caught.value, ScopeReadError)
    assert str(caught.value) == "scope is not approved (scope: project:scoped-project)"
    assert harness.executor.schema_calls == []

    # The container carries the approval and its one member does not, so the
    # entry passes and the per-lane filter is what reports the member.
    harness = runtime(port=unapproved_members())
    events = await bounded_walk(harness)
    assert len(ticks_of(events)) == 1
    observation = ticks_of(events)[0]
    assert observation.unapproved_lanes == ("A",)
    assert observation.unresolved_criteria == ("A/check",)
    assert observation.dispatched == ()
    assert harness.executor.schema_calls == []
    assert not any(isinstance(event, WorkflowCompleteEvent) for event in events)
    # An unapproved lane owes a gap nothing read, so it is not done and the
    # scope cannot derive the finished outcome from it.
    assert events[-1].outcome is WorkflowOutcome.scope_stopped_short
    assert events[-1].lanes[0] == ScopeLaneEntry(
        issue="A", done=False, branch=None, pr=None
    )

    rested = board()
    for issue in list(rested.issues.values()):
        if "criterion" in issue.issue_labels:
            rested.issues[issue.issue_key] = issue.model_copy(
                update={
                    "state_name": "Done",
                    "state_kind": WorkflowStateKind.COMPLETED,
                }
            )
    events = await bounded_walk(runtime(port=rested))
    walks = ticks_of(events)
    assert len(walks) == 1
    assert walks[0].dispatched == ()
    assert walks[0].unresolved_criteria == ()


async def test_a_configuration_refusal_precedes_the_approval_read():
    """A typed configuration refusal costs no tracker read.

    The request that names no repository cannot execute whatever the board
    says, so the refusal is answered from configuration alone. Hoisting the
    approval read above it would spend three reads to reach the same no.
    """
    port = board(approved=False)
    harness = runtime(port=port)

    def refuse(name):
        async def read(**kwargs):
            raise AssertionError(f"the approval was read before the refusal: {name}")

        return read

    for name in ("read_scope_labels", "execution_approved", "container_metadata"):
        setattr(port, name, refuse(name))

    with pytest.raises(ScopedExecutionUnavailableError):
        _ = await bounded_walk(harness, origin=None)


async def test_a_missing_container_is_a_read_error_not_a_refusal():
    """The entry does not relabel an unreadable scope as an unapproved one.

    One layer down this is pinned on every implementation; here it is that
    the entry passes the read error through rather than reporting the scope
    as carrying no approval.
    """
    port = board(approved=False)
    del port.scope_containers[SCOPE]
    harness = runtime(port=port)

    with pytest.raises(ScopeReadError) as caught:
        _ = await bounded_walk(harness)

    assert not isinstance(caught.value, ScopeNotApprovedError)
    assert "container metadata is missing" in str(caught.value)


async def test_existing_plan_barrier_prevents_any_lane_effect():
    port = board()
    port.issues["decision"] = make_tracker_issue(
        "decision", parent_key="A", issue_labels=frozenset({"decision"})
    )
    harness = runtime(port=port)
    with pytest.raises(ScopePlanRefusalError) as caught:
        _ = await bounded_walk(harness)
    assert caught.value.open_decisions == ("decision",)
    assert harness.executor.schema_calls == []


@pytest.mark.parametrize("failure", ["base", "record", "fire"])
async def test_one_lanes_failure_is_reported_and_the_walk_continues(
    monkeypatch, failure
):
    """Three ready lanes, the second one's own work raises (KOD-841).

    The other two fire, the failing lane is named on the observation right
    after its tick and on every later one, with its error's type and its own
    message, and it is never offered again. That ``drive()`` runs to its end
    at all IS the "no engine error" clause: the walk raises nothing, and a
    contained failure travels on the observation instead. A failure of the
    SCOPE's own ready read is a different fault and still ends the run, which
    ``test_tracker_outage_between_lanes_cannot_reuse_the_previous_ready_set``
    and ``test_a_failed_ready_read_in_a_lanes_turn_ends_the_run`` pin.
    """
    lanes = ("A", "B", "C")
    port = board(lanes=lanes)
    harness = runtime(
        port=port,
        lanes=lanes,
        # Only the two lanes that reach a grading have one scripted: an echo
        # left over for the failing lane would be spent on the next lane and
        # grade it against another lane's Check.
        evaluations=[
            native_evaluation(checks={f"{key}/check": f"{key} live Check  bytes"})
            for key in ("A", "C")
            for _ in range(2)
        ],
    )
    if failure == "base":
        resolver = harness.engine._scoped_arm._resolver
        resolve = resolver.resolve

        async def refuse_one(*, issue_key, **rest):
            if issue_key == "B":
                raise BaseResolutionError(
                    "the lane's base cannot be resolved", issue_id=issue_key
                )
            return await resolve(issue_key=issue_key, **rest)

        monkeypatch.setattr(resolver, "resolve", refuse_one)
    elif failure == "record":
        # The listing the lane's record is read from fails for this lane only:
        # unreadable is never treated as "no record", so nothing is minted.
        listing = port.list_comments

        async def unreadable(*, issue_key):
            if issue_key == "B":
                raise TrackerUnavailableError("the lane record listing failed")
            return await listing(issue_key=issue_key)

        monkeypatch.setattr(port, "list_comments", unreadable)
    else:
        # A criterion carrying no Check at all: the lane's own entry refuses
        # it, before any session, the way a malformed obligation does.
        port.issues["B/check"] = port.issues["B/check"].model_copy(
            update={"body": "**Evidence:** — and no Check field at all"}
        )
    # Bounded, like every walk of this module: a lane offered again after it
    # failed would walk forever, and a hang is not a failing assertion. Four
    # ticks take milliseconds here.
    events = await bounded_walk(harness)
    observations = [
        event.observation for event in events if isinstance(event, ScopeWalkEvent)
    ]
    iterations = [
        event.lane_key
        for event in events
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, WorkflowIterationEvent)
    ]
    assert iterations == ["A", "C"]
    # Four ticks: A is selected at tick 1 and its one criterion closes, so tick
    # 2 no longer offers it and selects B, which fails; C is selected at tick 3
    # and closes its own, and tick 4 has nothing left to offer. Every lane here
    # owes exactly one criterion, so no fire of this walk leaves its lane owing
    # anything for the next tick to offer it for again (KOD-724). The failure is
    # on the observation of the tick right after B's and on every later one, not
    # only on the terminal one.
    assert len(observations) == 4
    assert [
        observation.tick for observation in observations if observation.failed_lanes
    ] == [3, 4]
    assert all(
        [item.issue_key for item in observation.failed_lanes] == ["B"]
        for observation in observations[2:]
    )
    # A lane that raised before its graph launched was never dispatched; one
    # that raised inside it was launched exactly once and not offered again.
    assert list(observations[-1].dispatched).count("B") == (
        1 if failure == "fire" else 0
    )
    assert [key for key in observations[-1].dispatched if key != "B"] == ["A", "C"]
    # What keeps B from being offered again is that it RESTS. Being dispatched
    # no longer stops a lane being selected, so the resting is load bearing:
    # B is the one lane resting, and it is named once however it failed.
    assert observations[-1].rested_lanes == ("B",)
    reported = observations[-1].failed_lanes
    # Exactly one entry: the lane was tried once and rested, not retried on
    # every remaining tick.
    assert [item.issue_key for item in reported] == ["B"]
    assert (
        reported[0].error.error_kind
        == {
            "base": "BaseResolutionError",
            "record": "LaneRecordReadError",
            "fire": "InvalidFireCriterionError",
        }[failure]
    )
    # The message the lane's own refusal was raised with, not merely some text:
    # a report carrying another lane's reason, or a fixed one, reads the same
    # against a truthiness check.
    assert {
        "base": "the lane's base cannot be resolved",
        "record": "lane record None on 'B' for 'B' could not be read: "
        "the tracker comment read failed or was incomplete",
        "fire": "criterion 'B/check' of fire subject 'B' cannot be consumed: "
        "one nonempty Check field is required",
    }[failure] in reported[0].error.error
    fired = {
        key
        for key in lanes
        if any(
            f"Exact native subject {key}" in prompt
            for prompt in harness.executor.execution_prompts
        )
    }
    assert fired == {"A", "C"}


@pytest.mark.parametrize("site", ["readmission", "launch"])
async def test_a_failed_ready_read_in_a_lanes_turn_ends_the_run(monkeypatch, site):
    """The scope's own ready read is the walk's fault wherever it lands.

    A lane's turn makes two of the walk's three ready reads: one after base
    resolution and one before the graph launches. Both are the SCOPE's read,
    so an outage at either ends the run rather than being recorded against
    the lane that happened to be in flight and resting it — and the lane it
    was about never opens a session.
    """
    harness = runtime(port=board(lanes=("A", "B")), lanes=("A", "B"))
    fresh = scope_runtime.read_scope_ready
    reads = 0

    async def failing(*, ref, tracker):
        nonlocal reads
        reads += 1
        # 1 is the tick's own read, 2 the readmission after base resolution,
        # 3 the one before the launch.
        if reads == {"readmission": 2, "launch": 3}[site]:
            raise TrackerUnavailableError("the scope ready read failed")
        return await fresh(ref=ref, tracker=tracker)

    monkeypatch.setattr(scope_runtime, "read_scope_ready", failing)
    events = []
    with pytest.raises(TrackerUnavailableError, match="the scope ready read failed"):
        async with asyncio.timeout(WALK_BOUND_SECONDS):
            async for event in drive(harness):
                events.append(event)
    assert lane_failures(events) == ()
    assert harness.executor.execution_prompts == []


async def test_tracker_outage_between_lanes_cannot_reuse_the_previous_ready_set(
    monkeypatch,
):
    port = board(lanes=("A", "B"))
    harness = runtime(port=port, lanes=("A", "B"))
    original = port.scope_issues

    async def current_scope(*, ref):
        if ref == SCOPE:
            raise TrackerUnavailableError("current scope unavailable")
        return await original(ref=ref)

    with pytest.raises(TrackerUnavailableError, match="current scope unavailable"):
        async with asyncio.timeout(WALK_BOUND_SECONDS):
            async for event in drive(harness):
                if isinstance(event, ScopeLaneEvent) and isinstance(
                    event.event, WorkflowIterationEvent
                ):
                    monkeypatch.setattr(port, "scope_issues", current_scope)
    assert len(harness.executor.execution_prompts) == 1
    assert "Exact native subject B" not in harness.executor.execution_prompts[0]
    assert port.claim_writes == []


async def test_approval_removed_during_preparation_prevents_any_native_node(
    monkeypatch,
):
    port = board()
    harness = runtime(port=port)
    controller = harness.engine._scoped_arm
    original = controller._cache.ensure_available

    async def ensure_available(url, cache_key=None):
        path = await original(url, cache_key)
        port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="A")] = frozenset()
        return path

    monkeypatch.setattr(controller._cache, "ensure_available", ensure_available)
    events = await bounded_walk(harness)
    assert harness.executor.schema_calls == []
    assert ticks_of(events)[-1].unapproved_lanes == ("A",)
    assert ticks_of(events)[-1].unresolved_criteria == ("A/check",)


def owed_again(port, *, lane="A", check=None):
    """Put a lane's criterion back in Todo, the way the product does.

    A lane whose every criterion its run crossed off is closed by the
    tracker's rollup, and the walker offers such a lane for its delivery
    alone. What re-opens one in production is the amendment write-back: the
    criterion returns to the unstarted state with its Evidence cleared,
    carrying the amended Check when the amendment changed the text.
    """
    key = f"{lane}/check"
    issue = port.issues[key]
    current = (
        check
        if check is not None
        else criterion_field_bodies(issue.body, field="Check")[0]
    )
    port.issues[key] = issue.model_copy(
        update={
            "state_kind": WorkflowStateKind.UNSTARTED,
            "state_name": "Todo",
            "body": f"**Check:** {current}\n**Evidence:** —",
        }
    )


def lane_of(harness):
    return harness.engine._scoped_arm._lane_for(ORIGIN)


async def test_current_closed_blocker_unlocks_next_lane_using_its_recorded_branch():
    """B stands on the deliverable branch A's own record names (KOD-842).

    Nothing seeds a ref anywhere on this board: A's record is written by A's
    own commit, so the branch B was prepared with can only have been read
    from it. The record stands on A's LOOP branch, which is asserted to be
    what B was NOT based on — a reader answering with the record's branch
    would otherwise resolve a base and look right.
    """
    repos = WalkRepos()
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    harness = resumable(repos=repos, port=port, lanes=("A", "B"))
    events = []
    async with asyncio.timeout(WALK_BOUND_SECONDS):
        async for event in drive(harness):
            events.append(event)
            if isinstance(event, ScopeLaneEvent) and isinstance(
                event.event, LaneDeliveryEvent
            ):
                if event.lane_key == "A":
                    # A's criterion is already Done: its own evaluation step
                    # crossed it off, so nothing here has to close the blocker.
                    assert (
                        port.issues["A/check"].state_kind is WorkflowStateKind.COMPLETED
                    )
    iterations = [
        event.lane_key
        for event in events
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, WorkflowIterationEvent)
    ]
    assert iterations == ["A", "B"]
    assert port.issues["A"].state_kind is WorkflowStateKind.UNSTARTED
    final = ticks_of(events)[-1]
    assert final.unresolved_criteria == ()
    # Exactly the two criterion sub-issues moved, and neither subject did.
    assert port.workflow_writes == [
        ("A/check", LifecycleStage.DONE),
        ("B/check", LifecycleStage.DONE),
    ]
    # The base B was actually prepared with, read off the run's own base event
    # rather than a checkpoint's request metadata (KOD-776, KOD-806: the scope
    # path persists no graph state).
    bases = {
        event.lane_key: event.event.base_branch
        for event in events
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, WorkflowScopeBaseEvent)
    }
    record = await lane_record(port, "A")
    assert bases["B"] == recorded_branches(record=record).deliverable_branch
    assert bases["B"] != record.branch
    # And no work ref was ever recorded on either lane: the record is the
    # whole carrier of a blocker's branch on this path.
    assert await port.work_refs(issue_key="A") == ()


def finish_by_hand(port, key: str) -> None:
    """Lane *key* as a board holds it when somebody finished it elsewhere.

    Its criterion is Done and its own issue is closed, and nothing anywhere
    records a branch for it: the lane never ran here, so base resolution can
    only assume its work reached the trunk.
    """
    for issue_key in (key, f"{key}/check"):
        port.issues[issue_key] = port.issues[issue_key].model_copy(
            update={"state_name": "Done", "state_kind": WorkflowStateKind.COMPLETED}
        )


class UnreadableDelivery(FakeDeliveryProbe):
    """A forge that cannot answer the delivery question about one issue.

    It answers every other issue as usual, because the question about the
    candidate itself is asked outside the lane's boundary: a double refusing
    both would end the walk instead of reporting the lane.
    """

    def __init__(self, *, refuses: str) -> None:
        super().__init__()
        self.refuses = refuses

    async def open_delivery_exists(self, *, repo_url: str, issue_key: str) -> bool:
        if issue_key == self.refuses:
            self.calls.append(issue_key)
            raise ForgeAPIError(
                "the delivery listing failed",
                status_code=500,
                detail=f"GET /pulls for {issue_key}",
            )
        return await super().open_delivery_exists(
            repo_url=repo_url, issue_key=issue_key
        )


@pytest.mark.parametrize("answer", ["false", "true", "unreadable"])
async def test_a_closed_blocker_with_no_record_is_gated_by_one_open_delivery_read(
    answer,
):
    """A closed blocker recording nothing is assumed landed — after one read.

    The assumption base resolution makes for such a blocker is wrong in one
    observable case: the blocker's work is sitting in a delivery nobody merged
    (KOD-721, KOD-777). The walker asks the forge once, before it resolves, and
    all three answers are the lane's: no open delivery states the assumption in
    the log and the lane stands on the trunk; an open one refuses the lane; a
    forge that cannot answer refuses it with the forge's own error.
    """
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    finish_by_hand(port, "A")
    harness = runtime(
        port=port,
        lanes=("A", "B"),
        evaluations=[
            native_evaluation(checks={"B/check": "B live Check  bytes"})
            for _ in range(2)
        ],
    )
    probe = (
        UnreadableDelivery(refuses="A")
        if answer == "unreadable"
        else FakeDeliveryProbe(delivered=("A",) if answer == "true" else ())
    )
    harness.engine._scoped_arm._probe_for = lambda _: probe

    with structlog.testing.capture_logs() as logs:
        events = await bounded_walk(harness)

    # The blocker was asked about exactly once and nothing else was asked
    # about at all, whatever the answer was: the read is made per blocker per
    # turn, and the whole sequence is the assertion because a count of one key
    # cannot see a read about another (KOD-431).
    assert probe.calls == ["A"]
    # The carve-out is one OPEN-delivery read and nothing else. The probe the
    # walk is handed answers merge state as readily as the native client does,
    # so an empty list is a fact about the walker and not about an unreachable
    # double (KOD-721: no code path derives landedness from the forge).
    assert probe.merge_state.calls == []
    fired = [
        event.lane_key
        for event in events
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, WorkflowIterationEvent)
    ]
    assumed = [
        event
        for event in logs
        if event.get("event") == "base_input_no_open_delivery"
        and event.get("blocker") == "A"
    ]
    if answer == "false":
        assert [failure.error.error_kind for failure in lane_failures(events)] == []
        assert fired == ["B"]
        bases = {
            event.lane_key: event.event.base_branch
            for event in events
            if isinstance(event, ScopeLaneEvent)
            and isinstance(event.event, WorkflowScopeBaseEvent)
        }
        assert bases["B"] == "trunk"
        # Stated by name, once, and never silently.
        assert [event["lane"] for event in assumed] == ["B"]
        return
    assert fired == []
    assert assumed == []
    failures = lane_failures(events)
    assert [failure.issue_key for failure in failures] == ["B"]
    assert [failure.error.error_kind for failure in failures] == [
        {"true": "BaseResolutionError", "unreadable": "ForgeAPIError"}[answer]
    ]
    assert {
        "true": "an unrecorded open delivery exists for the blocker",
        "unreadable": "the delivery listing failed",
    }[answer] in failures[0].error.error
    if answer == "true":
        # A reader of the walk sees which blocker refused the lane. The failure
        # carries str(exc) alone, so the message is the only place it can. The
        # rendered tail is matched rather than the key on its own: one capital
        # letter is in half the sentences a rewording could produce.
        assert failures[0].error.error.endswith("for the blocker A")


async def test_actual_http_sse_preserves_nested_progress_and_delivery_discriminators():
    harness = runtime()
    async for app in _build_app(harness.engine, checkpointer=harness.saver):
        fired = await app.client.post(
            "/api/v1/agent/fire",
            json={
                "prompt": "scope request",
                "repoUrl": ORIGIN,
                "scope": SCOPE.model_dump(mode="json"),
            },
        )
        assert fired.status_code == 202
        job_id = fired.json()["jobId"]
        stream = await app.client.get(f"/api/v1/jobs/{job_id}/stream")
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(line[6:])
            for line in stream.text.splitlines()
            if line.startswith("data: ")
        ]
        assert not [event for event in events if event["type"] == "error"]
        lane_events = [event for event in events if event["type"] == "scope_lane"]
        assert lane_events and all(event["laneKey"] == "A" for event in lane_events)
        assert all(
            ScopeLaneEvent.model_validate_json(json.dumps(event)).lane_key == "A"
            for event in lane_events
        )
        iteration = next(
            event["event"]
            for event in lane_events
            if event["event"]["type"] == "workflow_iteration"
        )
        assert iteration["evaluation"]["criteriaResults"][0]["criterionId"] == "A/check"
        session = next(
            event["event"]
            for event in lane_events
            if event["event"]["type"] == "node_session_started"
        )
        assert session["invocation"]["run"]["name"] == "A"
        assert session["invocation"]["run"]["started_at"]
        delivery = next(
            event["event"]
            for event in lane_events
            if event["event"]["type"] == "lane_delivery"
        )
        assert delivery["delivery"]["phase"] == "skipped"
        observation = [
            item["observation"] for item in events if item["type"] == "scope_walk"
        ][-1]
        assert observation["skippedLanes"] == ["A"]
        assert observation["unresolvedCriteria"] == []
        assert harness.port.issues["A/check"].state_kind is WorkflowStateKind.COMPLETED
        assert all(event["type"] != "workflow_complete" for event in events)
        status = (await app.client.get(f"/api/v1/jobs/{job_id}")).json()
        assert status["state"] == "terminal"
        assert status["outcome"] == "scope_converged"


async def test_actual_scope_composition_retains_completed_native_delivery_record():
    """A completed delivery is retained where a lane's state lives: its record.

    The scope path persists no graph state (KOD-840), so what the lane graph
    held in process is gone with the process. The record is read back through
    the production reader afterwards and carries the pull request the delivery
    opened — url, number and state — written by the delivering step onto the
    one comment the commit before it left (KOD-843).
    """
    from kodezart.types.domain.native_delivery import CompletedLaneDelivery

    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A",))
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        harness = resumable(
            port=port, repos=repos, origin=FORGE_ORIGIN, forge=forge, trunk="main"
        )
        events = await bounded_walk(harness, origin=FORGE_ORIGIN)
        deliveries = [
            event.event.delivery
            for event in events
            if isinstance(event, ScopeLaneEvent)
            and isinstance(event.event, LaneDeliveryEvent)
        ]
        assert len(deliveries) == len(wire.creates) == 1
        phase = deliveries[0]
        assert isinstance(phase, CompletedLaneDelivery)
        assert phase.result.issue_id == phase.result.lane_key == "A"
        assert phase.result.base_branch == "main"
        assert phase.result.checks_passed is True
        assert not phase.result.remediation_pending
        addressed = ScopeLaneEvent(
            lane_key="A", event=LaneDeliveryEvent(delivery=phase)
        )
        assert (
            ScopeLaneEvent.model_validate_json(addressed.model_dump_json()) == addressed
        )
        record = await lane_record(port, "A")
        assert record.pr is not None
        assert (record.pr.url, record.pr.number, record.pr.state) == (
            phase.result.pr.url,
            phase.result.pr.number,
            phase.result.pr.state,
        )
        # The head that pull request was opened on is the lane's own deliverable
        # at the sha this walk's repositories hold, not a value spelled here.
        assert wire.creates[0]["head"] == phase.result.head_branch
        assert phase.result.final_commit_sha == repos.head_of(phase.result.head_branch)
        assert record.head_sha == repos.head_of(record.branch)
        assert ticks_of(events)[-1].unresolved_criteria == ()
        assert harness.port.workflow_writes == [("A/check", LifecycleStage.DONE)]
        # The sha the lane's own loop branch stands at, as the repositories
        # answer it, rather than a value spelled here.
        assert parse_criterion_evidence(
            harness.port.issues["A/check"].body
        ).graded_sha == repos.head_of(record.branch)
    finally:
        await forge.close()


# ---------------------------------------------------------------------------
# KOD-832 clause 3 — every fire is execution-only, and the pre-loop question
# step pins each open question's answer before the first loop iteration.
# ---------------------------------------------------------------------------

#: The three lanes of the clause: one whose open question is answered and
#: pinned, one with none, and one whose record cannot be written.
CLAUSE_3_LANES = ("A", "B", "C")

CLAUSE_3_QUESTION = "Does a Check's byte run include its trailing marker?"
CLAUSE_3_RESOLUTION = "The trailing marker is not part of the Check's bytes."


def clause_3_answer(key: str) -> dict[str, object]:
    return {
        "issueRef": f"{key}/check",
        "question": CLAUSE_3_QUESTION,
        "rulingClass": "pin_reading",
        "resolution": CLAUSE_3_RESOLUTION,
        "rejectedAlternative": (
            "Reading the marker as Check text, under which the criterion can "
            "never be observed."
        ),
        "repoEvidence": ["lane-0.py — the reader this tree already has"],
    }


def record_prefix() -> str:
    return native_operation().marker_prefixes["ruling"]


def records_on(port, key: str):
    return [
        comment
        for comment in port.comments
        if comment.issue_key == key and comment.body.startswith(f"[{record_prefix()}")
    ]


class UnwritableLane(FakeTrackerPort):
    """One lane's record write fails; every other write of the board is fine."""

    refuse = "C"

    async def upsert_comment(self, *, target, marker, body, holder=None, expected=None):
        if marker.startswith(f"[{record_prefix()}") and target.startswith(self.refuse):
            raise TrackerUnavailableError("this lane's record write is unavailable")
        return await super().upsert_comment(
            target=target, marker=marker, body=body, holder=holder, expected=expected
        )


class QuestionedExecutor(ObservedNativeExecutor):
    """Answers each lane's question pass and reads the board at each iteration.

    The board is read through a FRESH record reader at the moment a writer
    session opens, so what the first iteration was shown came off the tracker
    rather than out of anything this walk carried.
    """

    def __init__(self, evaluations, *, port, answers):
        super().__init__(evaluations)
        self._port = port
        self._answers = dict(answers)
        #: One entry per writer session: its prompt and the record count of
        #: every issue on the board at the moment it opened.
        self.records_at_execution: list[tuple[str, dict[str, int]]] = []
        #: The run identity each writer session was opened under, in order.
        self.execution_identities: list[object] = []

    async def stream(self, **kwargs):
        properties = (kwargs.get("output_format") or {}).get("schema", {}).get(
            "properties"
        ) or {}
        if "rulings" in properties:
            subject = re.findall(r"<issue_key>(.*?)</issue_key>", kwargs["prompt"])[-1]
            self.question_answers = [{"rulings": self._answers.get(subject, [])}]
        if "claims" in properties:
            self.execution_identities.append(kwargs.get("run_identity"))
            reader = RulingRecordReader(
                tracker=self._port, operation=native_operation()
            )
            self.records_at_execution.append(
                (
                    kwargs["prompt"],
                    {
                        key: len(await reader.read_issue(issue_key=key))
                        for key in sorted(self._port.issues)
                    },
                )
            )
        async for event in super().stream(**kwargs):
            yield event


def wrote_for(executor, key: str):
    """Every writer session of lane *key*, by the subject text it carries."""
    return [
        row
        for row in executor.records_at_execution
        if f"Exact native subject {key}  with spaces" in row[0]
    ]


async def test_every_scoped_fire_pins_its_open_questions_before_its_first_iteration(
    monkeypatch,
):
    """Three lanes, one walk: what the clause says, at the composed boundary.

    A raises a question and its answer is on the tracker before the iteration
    that reads it. B raises none and fires anyway. C's record cannot be
    written, so C is never entered — and the walk still finishes without
    reporting a failed lane.
    """

    def never_drafted(*_args, **_kwargs):
        pytest.fail("a scoped fire drafted a subject")

    monkeypatch.setattr(TicketDraftOutput, "__init__", never_drafted)
    rows = board(lanes=CLAUSE_3_LANES)
    port = UnwritableLane(
        issues=list(rows.issues.values()),
        scope_memberships={SCOPE: CLAUSE_3_LANES},
        criteria_stage_label_key=STAGED,
        marker_prefixes=native_operation().marker_prefixes,
        scope_containers=list(rows.scope_containers.values()),
        scope_label_members=dict(rows.scope_label_members),
    )
    harness = runtime(port=port, lanes=CLAUSE_3_LANES)
    executor = QuestionedExecutor(
        list(harness.executor.evaluations),
        port=port,
        answers={key: [clause_3_answer(key)] for key in ("A", "C")},
    )
    harness.service._executor = executor
    harness = Harness(
        harness.engine,
        port,
        executor,
        harness.service,
        harness.artifacts,
        harness.saver,
        harness.workspace,
        harness.status,
        harness.git,
        harness.persister,
        harness.merger,
        harness.registry,
    )

    events = await bounded_walk(harness)

    # The step is part of the arm this walk dispatches through.
    fire = lane_of(harness).fire
    assert fire.native_graph is not None
    assert "rule_open_questions" in set(fire.native_graph.get_graph().nodes)

    # One lane's failure never ends the walk, no lane is reported failed, and
    # no fire generated anything or ran without a run identity.
    assert lane_failures(events) == ()
    assert not any("slug" in props for props in executor.schema_calls)
    # Every attributed session of the walk names a lane of this scope. The
    # step's own two sessions are unattributed, because the shared read-only
    # judgment helper takes no run identity.
    named = {row.name for row in executor.run_identities if row is not None}
    assert named and named <= set(CLAUSE_3_LANES)
    # Each writer session in particular: attribution is what a session carries,
    # so a walk that opened one without it fails here rather than passing on
    # the strength of some other session that was attributed. A and B fired; C
    # never entered its loop.
    assert executor.execution_identities
    assert all(row is not None for row in executor.execution_identities)
    assert {row.name for row in executor.execution_identities} == {"A", "B"}

    # A's first iteration opened after its record was on the board, and it was
    # shown the subject body, the live Check bytes and the pinned text.
    opened = wrote_for(executor, "A")
    assert opened, "lane A never opened a writer session"
    first_prompt, counts = opened[0]
    assert counts["A/check"] == 1
    assert "A live Check  bytes" in first_prompt
    assert CLAUSE_3_RESOLUTION in first_prompt
    assert len(records_on(port, "A/check")) == 1
    assert records_on(port, "A") == []
    # A lane this walk converged is not offered again, so the other half of the
    # clause — a re-entered pass writing nothing — is pinned where one lane does
    # fire twice in one invocation: test_a_second_fire_of_a_pinned_lane_adds_no_record.

    # B fired with nothing open and carries no record.
    assert wrote_for(executor, "B")
    assert records_on(port, "B/check") == []
    assert records_on(port, "B") == []

    # C was never entered, and its delivery says why.
    assert wrote_for(executor, "C") == []
    skipped = [
        event.event.delivery
        for event in events
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, LaneDeliveryEvent)
        and event.lane_key == "C"
    ]
    assert skipped
    assert all(delivery.phase == "skipped" for delivery in skipped)
    assert {delivery.outcome for delivery in skipped} == {
        WorkflowOutcome.ruling_unrecorded
    }
    assert records_on(port, "C/check") == []


# ---------------------------------------------------------------------------
# KOD-832 clause 4 — a scoped walk leaves every criterion finished with the
# head sha and a record.
# ---------------------------------------------------------------------------


class WalkRepos:
    """One repository per lane branch, each numbering its own shas.

    Every repository is seeded where no other one's shas reach, so a lane's
    head is that lane's: unseeded, the first commit of each branch would be
    the same value and no assertion could tell one lane's tree from
    another's. A walk dispatches its lanes one at a time and the workspace
    provider hands each of them the same path, so the tree an unaddressed
    read belongs to is the lane that last committed and not the path. Push
    status is still answered per branch: a double that answered every branch
    alike would report one lane's push from a branch nobody pushed.
    """

    #: How far apart two repositories' sha spaces are set.
    SPACING = 0x1000

    def __init__(self, *, remote: str = "origin", url: str | None = None) -> None:
        self.remote = remote
        #: The same remote, under the other name callers have for it: the lane
        #: state writer knows the configured remote NAME and a delivery knows
        #: the repository URL. Two names for one remote, so a read through
        #: either is a read of these repositories — and a read through a third
        #: name is still a read of a remote this walk knows nothing about.
        self.remote_names = frozenset(
            {remote}
            | ({url, resolve_repo_url(url, "https://github.com")} if url else set())
        )
        self.branches: dict[str, LaneRepo] = {}
        #: The refs a delivery published, which a later lane resolves a base
        #: from: they carry no commits of this walk and hold one sha each.
        self.delivered: dict[str, str] = {}
        # Before the first commit of the walk the trees are the trunk's, which
        # is what the repository this walk was cut from holds.
        self.committing = LaneRepo(branch="trunk", remote=remote)

    def head_of(self, branch: str) -> str | None:
        """The sha this walk holds for *branch*, without creating a repository.

        ``of`` makes one and makes it the tree later unaddressed reads answer
        from, so a forge double asking what a branch stands at would change
        what the walk is standing on.
        """
        repo = self.branches.get(branch)
        if repo is not None:
            return repo.head
        return self.delivered.get(branch)

    def of(self, branch: str) -> LaneRepo:
        repo = self.branches.setdefault(
            branch,
            LaneRepo(
                branch=branch,
                remote=self.remote,
                seed=(len(self.branches) + 1) * self.SPACING,
            ),
        )
        self.committing = repo
        return repo

    @property
    def current(self) -> LaneRepo:
        return self.committing


class WalkGit(FakeGitService):
    """The git reads of a whole walk, answered from its repositories."""

    def __init__(self, repos: WalkRepos) -> None:
        super().__init__()
        self.repos = repos

    async def current_sha(self, cwd: str) -> str:
        self.calls.append(("current_sha", cwd))
        # The commit a tree was cut at, where one was recorded for that path:
        # a tree standing at a base is not standing at the branch the lane
        # last committed on.
        return self.checkouts.get(cwd, self.repos.current.head)

    async def remote_branch_sha(self, cwd, remote, branch):
        self.calls.append(("remote_branch_sha", cwd, remote, branch))
        if branch in TRUNK_BRANCHES:
            return TRUNK_SHA
        if remote not in self.repos.remote_names:
            return None
        repo = self.repos.branches.get(branch)
        if repo is not None:
            return repo.pushed
        return self.repos.delivered.get(branch)

    async def is_ancestor(self, cwd, ancestor_ref, descendant_ref):
        self.calls.append(("is_ancestor", cwd, ancestor_ref, descendant_ref))
        shas = [TRUNK_SHA, *self.repos.current.shas]
        return (
            ancestor_ref in shas
            and descendant_ref in shas
            and shas.index(ancestor_ref) <= shas.index(descendant_ref)
        )

    async def diff_summary(self, cwd, base_ref, head_ref):
        self.calls.append(("diff_summary", cwd, base_ref, head_ref))
        repo = self.repos.current
        made = repo.shas.index(head_ref) + 1 if head_ref in repo.shas else 0
        return ChangesetDigest(
            file_paths=[f"lane-{index}.py" for index in range(made)],
            commit_subjects=[f"feat: commit {index + 1}" for index in range(made)],
            commit_count=made,
        )


class WalkSource(NativeSourceReader):
    """Resolves each lane's refs against the repository that holds them.

    A ref no repository of this walk holds is a read this double cannot
    answer, and it refuses: answered with the current head instead, a
    grading of some ref nobody wrote would read as a grading of the lane's
    own branch.
    """

    def __init__(self, repos: WalkRepos) -> None:
        self.repos = repos

    async def resolve_commit(self, *, cwd, ref):
        if ref in TRUNK_BRANCHES or ref == TRUNK_SHA:
            return TRUNK_SHA
        if ref in self.repos.branches:
            return self.repos.branches[ref].head
        if ref in self.repos.delivered:
            return self.repos.delivered[ref]
        if ref in set(self.repos.delivered.values()) or any(
            ref in repo.shas for repo in self.repos.branches.values()
        ):
            return ref
        raise GitSourceReadError(
            ref=ref, path=None, reason="no repository of this walk holds the ref"
        )


class WalkWorkspaces(FakeWorkspaceProvider):
    """Cuts each lane's tree from the branch that lane commits on.

    A lane's repository exists from the moment its tree is acquired, not
    from its first commit: the guard reads the tree's starting HEAD before
    anything is committed in it, and a walk whose repositories appeared only
    at the commit would answer that read from the previous lane's branch.
    """

    def __init__(self, repos: WalkRepos, *, git: WalkGit) -> None:
        super().__init__(git=git)
        self.repos = repos

    async def acquire(self, **arguments):
        branch = arguments.get("branch_name")
        if branch is not None:
            self.repos.of(branch)
        return await super().acquire(**arguments)


class WalkMerger(FakeBranchMerger):
    """Consolidates a lane's loop branch onto its deliverable, in the repositories.

    The deliverable branch then stands on the remote at the sha the loop branch
    reached, which is the fact a delivery checks before it opens anything. A
    double that answered a tip nobody published would let a delivery proceed
    from a head no repository holds.
    """

    def __init__(self, repos: WalkRepos) -> None:
        super().__init__()
        self.repos = repos

    async def consolidate(
        self,
        *,
        repo_path,
        repo_url,
        base_branch,
        feature_branch,
        source_branch,
        cache_key=None,
    ):
        committing = self.repos.current
        source = self.repos.of(source_branch)
        feature = self.repos.of(feature_branch)
        integrated = feature.head == source.head
        feature.head = source.head
        feature.shas = list(source.shas)
        feature.publish()
        # Consolidation moves a branch; it does not make the lane's tree the
        # one later reads answer from.
        self.repos.committing = committing
        self.calls.append(
            {
                "method": "consolidate",
                "base_branch": base_branch,
                "feature_branch": feature_branch,
                "source_branch": source_branch,
            }
        )
        return ConsolidationOutcome(
            status=ConsolidationStatus.ALREADY_INTEGRATED
            if integrated
            else ConsolidationStatus.FAST_FORWARDED,
            feature_tip_sha=source.head,
        )


class WalkRefPublisher(FakeRefPublisher):
    """Publish a ref into the walk's own repositories, at the sha it names.

    The default double records the call and nothing else, which leaves a ref a
    stall exit published standing where an unwritten branch stands: at the
    trunk, carrying none of the lane's commits. A pull request opened from a
    branch consolidated off THAT ref then holds no work at all, and "from the
    best iteration" is a statement about a branch name rather than about a
    commit. Here the ref really holds the commit it was published with.

    Publishing a ref does not make it the tree later unaddressed reads answer
    from, so the committing repository is put back: the same rule the walk's
    merger follows for the same reason.
    """

    def __init__(self, repos: WalkRepos) -> None:
        super().__init__()
        self.repos = repos

    async def publish(self, *, commit_sha, ref, **rest):
        committing = self.repos.current
        published = self.repos.of(ref)
        published.head = commit_sha
        published.shas = [commit_sha]
        published.publish()
        self.repos.committing = committing
        return await super().publish(commit_sha=commit_sha, ref=ref, **rest)


class WalkPersister(FakeChangePersister):
    """Commits and pushes the branch each lane of the walk is on."""

    def __init__(self, repos: WalkRepos) -> None:
        super().__init__()
        self.repos = repos

    async def persist(
        self, *, workspace_path, branch, before_commit=None, before_publish=None, **rest
    ):
        if before_commit is not None:
            await before_commit()
        self.calls.append({"workspace_path": workspace_path, "branch": branch})
        repo = self.repos.of(branch)
        sha = repo.commit()
        if before_publish is not None:
            await before_publish(sha)
        repo.publish()
        return PersistResult(
            commit_sha=sha,
            branch=branch,
            message=f"feat: {branch} commit {len(repo.shas)}\n\nthe body of it",
            source=PersistSource.WORKING_TREE_COMMIT,
        )


async def lane_record(port, key: str, *, operation=None):
    """The record this walk left on one lane's issue, read back fresh."""
    reader = LaneRecordReader(
        tracker=port, operation=native_operation() if operation is None else operation
    )
    _, record = await reader.read(issue_key=key, lane_key=key)
    return record


async def recorded_so_far(port, key: str):
    """The same record, or ``None`` where the walk has not written one yet.

    A reading taken DURING a walk cannot assume a record exists: the tick that
    precedes a lane's first fire is a tick the lane has no record at, and a
    reading that refused there could not be taken at every tick.
    """
    located = await LaneRecordReader(tracker=port, operation=native_operation()).find(
        issue_key=key, lane_key=key
    )
    return None if located is None else located[1]


def deliverable_of(repos: WalkRepos, key: str) -> WorkRef:
    """The ref a delivery records, published on the remote as a delivery does."""
    branch, sha = f"recorded-{key}", "a" * 40
    repos.delivered[branch] = sha
    return WorkRef(
        issue_id=key,
        role=WorkRefRole.DELIVERABLE,
        branch=branch,
        pushed_head_sha=sha,
        recorded_at=FIXTURE_EPOCH,
    )


async def test_a_scoped_walk_finishes_every_criterion_at_its_head_with_a_record():
    """The whole walk, in process: two lanes, one blocked on the other.

    Read off the board as the walk runs and again at the end. Each lane's
    criterion is finished with the sha its own branch head carries, and each
    lane's issue holds a record naming that head and the push of it, written
    by the commit that made it. Neither subject is written by anything, the
    walk's last observation owes nothing, and each lane posted its first push
    exactly once.
    """
    repos = WalkRepos()
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    git = WalkGit(repos)
    source = WalkSource(repos)
    harness = runtime(
        port=port,
        lanes=("A", "B"),
        persister=WalkPersister(repos),
        git=git,
        source=source,
        workspace=WalkWorkspaces(repos, git=git),
    )
    mid_walk: dict[str, tuple[str, str, str, str | None, str]] = {}

    events = []
    async with asyncio.timeout(WALK_BOUND_SECONDS):
        async for event in drive(harness):
            events.append(event)
            if not isinstance(event, ScopeLaneEvent):
                continue
            if isinstance(event.event, WorkflowIterationEvent):
                lane = event.lane_key
                if lane not in mid_walk:
                    record = await lane_record(port, lane)
                    mid_walk[lane] = (
                        port.issues[f"{lane}/check"].state_name,
                        parse_criterion_evidence(
                            port.issues[f"{lane}/check"].body
                        ).graded_sha,
                        record.head_sha,
                        record.pushed_head_sha,
                        # The repository's own head at this instant: read here
                        # rather than at the end, the comparison is to the tree
                        # this lane stood at when the cross-off was written.
                        repos.branches[record.branch].head,
                    )
            if isinstance(event.event, LaneDeliveryEvent):
                # A delivery leaves the lane's deliverable branch on the remote,
                # which is the ref a dependent lane's base resolves to. The branch
                # is read off the record, because the record is where a lane's
                # deliverable branch is written (KOD-842).
                delivered = recorded_branches(
                    record=await lane_record(port, event.lane_key)
                )
                repos.delivered[delivered.deliverable_branch] = repos.branches[
                    delivered.loop_branch
                ].head

    heads = {branch: repo.head for branch, repo in repos.branches.items()}
    assert len(heads) == 2
    # Two repositories, two sha spaces: each lane's own head is a value no
    # other lane's tree ever stood at, so "its own branch head" is a
    # comparison and not a coincidence.
    assert len({*heads.values()}) == 2
    assert set(mid_walk) == {"A", "B"}

    for lane in ("A", "B"):
        criterion = port.issues[f"{lane}/check"]
        record = await lane_record(port, lane)
        own_head = heads[record.branch]
        state_name, graded_sha, head, pushed, head_then = mid_walk[lane]
        assert state_name == LifecycleStage.DONE.value
        assert graded_sha == head == pushed == head_then
        assert criterion.state_kind is WorkflowStateKind.COMPLETED
        assert parse_criterion_evidence(criterion.body).graded_sha == own_head
        assert record.head_sha == record.pushed_head_sha == own_head
        assert [
            event.kind
            for event in await port.lane_run_events(issue_key=lane, lane_key=lane)
        ] == [RunEventKind.FIRST_PUSH]
        assert port.issues[lane].state_kind is WorkflowStateKind.UNSTARTED

    assert port.workflow_writes == [
        ("A/check", LifecycleStage.DONE),
        ("B/check", LifecycleStage.DONE),
    ]
    # One record and one first-push event per lane, and nothing else.
    assert len(port.comments) == 4
    # Push status is per branch: a branch this walk never pushed is reported
    # as unpushed, so no lane's push is ever read off another lane's branch.
    assert await git.remote_branch_sha("/w", repos.remote, "never-pushed") is None
    # And a ref no repository of this walk holds is a read the source refuses:
    # answered with the current head, a grading of a ref nobody wrote would
    # read as a grading of the lane's own branch.
    with pytest.raises(GitSourceReadError):
        await source.resolve_commit(cwd="/w", ref="no-such-ref")
    observations = [
        event.observation for event in events if isinstance(event, ScopeWalkEvent)
    ]
    assert observations[-1].unresolved_criteria == ()
    assert observations[-1].dispatched == ("A", "B")


#: Two more criteria under lane A, so one iteration can pass some of its
#: roster and fail the rest and the loop has somewhere left to go.
FURTHER_CHECKS = ("A/second", "A/third")


def lane_with_three_criteria() -> FakeTrackerPort:
    """Lane A's board, widened by two more criterion sub-issues."""
    port = board(lanes=("A",))
    for key in FURTHER_CHECKS:
        port.issues[key] = make_tracker_issue(
            key,
            parent_key="A",
            issue_labels=frozenset({"criterion"}),
            body=f"**Check:** {key} live Check  bytes\n**Evidence:** —",
        )
    return port


async def test_a_walk_that_breaks_what_it_finished_takes_it_back_once():
    """The regression, in process, over the whole walk.

    Iteration 1 finishes one criterion. Iteration 2 breaks it and finishes
    the other two: read at that iteration's event, the sub-issue is back out
    of its finished state, its Evidence row carries the sha that grading was
    read at, and the lane's stream holds exactly one refutation naming that
    sha. Iteration 3 finishes it again at the final head, and the stream
    still holds that one refutation: a criterion this lane broke and then
    fixed is one regression, recorded once.
    """
    broken, *rest = keys = ("A/check", *FURTHER_CHECKS)
    repos = WalkRepos()
    port = lane_with_three_criteria()
    git = WalkGit(repos)
    harness = runtime(
        port=port,
        lanes=("A",),
        evaluations=[
            criteria_echo(keys=keys, passed={broken}),
            criteria_echo(keys=keys, passed=set(rest)),
            criteria_echo(keys=keys, passed=set(keys)),
            criteria_echo(keys=keys, passed=set(keys)),
        ],
        persister=WalkPersister(repos),
        git=git,
        source=WalkSource(repos),
        workspace=WalkWorkspaces(repos, git=git),
        max_iterations=3,
    )
    at_iteration: dict[int, tuple[str, str, list[str | None], str]] = {}

    async with asyncio.timeout(WALK_BOUND_SECONDS):
        async for event in drive(harness):
            if not isinstance(event, ScopeLaneEvent):
                continue
            if isinstance(event.event, WorkflowIterationEvent):
                issue = port.issues[broken]
                at_iteration[event.event.iteration] = (
                    issue.state_name,
                    parse_criterion_evidence(issue.body).graded_sha,
                    [
                        posted.graded_sha
                        for posted in await port.lane_run_events(
                            issue_key="A", lane_key="A"
                        )
                        if posted.kind is RunEventKind.CRITERION_REFUTED
                    ],
                    repos.branches[event.event.branch].head,
                )
            if isinstance(event.event, LaneDeliveryEvent):
                port.recorded_work_refs["A"] = [deliverable_of(repos, "A")]

    heads = [repo.head for repo in repos.branches.values()]
    assert len(heads) == 1
    # The refuting sha is the branch's own head at that iteration, captured
    # from the repository rather than read back off the row it was written on.
    refuted_at = at_iteration[2][3]
    assert at_iteration[2] == ("Todo", refuted_at, [refuted_at], refuted_at)
    assert at_iteration[1][0] == LifecycleStage.DONE.value
    assert refuted_at != at_iteration[1][1]

    final = port.issues[broken]
    assert final.state_kind is WorkflowStateKind.COMPLETED
    assert parse_criterion_evidence(final.body).graded_sha == heads[0]
    assert [
        posted.graded_sha
        for posted in await port.lane_run_events(issue_key="A", lane_key="A")
        if posted.kind is RunEventKind.CRITERION_REFUTED
    ] == [refuted_at]
    assert all(
        port.issues[key].state_kind is WorkflowStateKind.COMPLETED for key in keys
    )
    # Nothing wrote the issue that owns them, and the state moves are exactly
    # the four a tick makes: one per criterion, plus the criterion this walk
    # broke and finished again. The move back out of the finished state is not
    # among them — the double ledgers it in neither write log — so the row's
    # own state and the stream above are what carry it.
    assert port.issues["A"].state_kind is WorkflowStateKind.UNSTARTED
    assert port.workflow_writes == [
        (broken, LifecycleStage.DONE),
        *((key, LifecycleStage.DONE) for key in rest),
        (broken, LifecycleStage.DONE),
    ]


# ---------------------------------------------------------------------------
# KOD-610 — a criterion its lane's resolved base already satisfies stays owed,
# and the walk that found it out finishes.
# ---------------------------------------------------------------------------

#: The stacked lane's roster: one criterion its base does not satisfy, and one
#: it does. On the STACKED lane rather than on the blocker, because a criterion
#: the base satisfies keeps its lane owed and a lane that stays owed never
#: unblocks the lane behind it — so the base a blocker delivered is only
#: reachable when the blocker itself finishes.
STACKED_NAMES = ("check", "second")
#: The same roster as the board's own keys for it.
STACKED_CHECKS = tuple(f"B/{name}" for name in STACKED_NAMES)


class BaseTreeExecutor(ObservedNativeExecutor):
    """Notes which acquisition each base session's own tree came from.

    The workspace double hands one path back for every acquisition, so the tree
    a base session ran in is named by the acquisition that opened it — the last
    one made when that session starts — and never by its path.
    """

    def __init__(self, evaluations):
        super().__init__(evaluations)
        #: The provider's own acquisition log, set once the harness is built.
        self.acquisitions: list[dict[str, object]] = []
        #: What each base session's tree was cut at, in order.
        self.base_refs: list[str] = []

    async def stream(self, **kwargs):
        schema = (kwargs.get("output_format") or {}).get("schema", {})
        if "baseCheckResults" in schema.get("properties", {}):
            self.base_refs.append(self.acquisitions[-1]["ref"])
        async for event in super().stream(**kwargs):
            yield event


class RefusedResolvedBase(WalkWorkspaces):
    """Refuses the tree at a base a blocker's delivery resolved to.

    Named by the shape of the acquisition rather than by a ref, because the ref
    is a sha another lane produced during the walk: a detached tree cut off the
    clone at a commit that is neither this lane's own head nor the trunk is the
    tree at a resolved base and nothing else in this walk.
    """

    async def acquire(self, **arguments):
        if (
            arguments.get("repo_url") is None
            and not arguments.get("create_branch", True)
            and arguments["ref"] not in {self.repos.current.head, TRUNK_SHA}
        ):
            raise WorkspaceError("no tree can be cut at the resolved base")
        return await super().acquire(**arguments)


def stacked_board() -> FakeTrackerPort:
    """Two lanes, the second blocked on the first and carrying two criteria."""
    return board(lanes=("A", "B"), blocked={"B": ("A",)}, checks={"B": STACKED_NAMES})


def stacked_walk(*, repos, port, executor, workspace=None):
    """The composed engine over this walk's own repositories.

    Built from the pieces ``resumable`` assembles rather than through it, so the
    refusing variant can hand in its own provider while every other double stays
    the family's own.
    """
    git = WalkGit(repos)
    harness = runtime(
        port=port,
        lanes=("A", "B"),
        executor=executor,
        persister=WalkPersister(repos),
        git=git,
        source=WalkSource(repos),
        workspace=(
            WalkWorkspaces(repos, git=git)
            if workspace is None
            else workspace(repos, git)
        ),
        merger=WalkMerger(repos),
        ref_publisher=WalkRefPublisher(repos),
    )
    executor.acquisitions = harness.workspace.acquisitions
    return harness


async def test_a_criterion_its_lanes_base_satisfies_stays_owed_and_the_walk_ends():
    """The whole thing in process, over the real scope composition.

    Lane A finishes and delivers; lane B is then prepared on the branch A's
    record names, and B's criteria are read at the commit that branch stands at
    — which is what shows the checks run at the base the walk RESOLVED and not
    at a trunk somebody hard-coded. One of B's criteria already passes there, so
    it is not moved to Done and lane B stays owed: the exclusion is the rollup's
    own doing and no arithmetic was told about this case. B's other criterion is
    finished in the same tick, the walk ends with no lane failure and no engine
    error, and every tree it cut was given back.
    """
    other, satisfied = STACKED_CHECKS
    repos = WalkRepos()
    port = stacked_board()
    executor = BaseTreeExecutor(
        [
            criteria_echo(keys=("A/check",), passed={"A/check"}),
            criteria_echo(keys=("A/check",), passed={"A/check"}),
            criteria_echo(keys=STACKED_CHECKS, passed=set(STACKED_CHECKS)),
            criteria_echo(keys=STACKED_CHECKS, passed=set(STACKED_CHECKS)),
            criteria_echo(keys=(satisfied,), passed={satisfied}),
            criteria_echo(keys=(satisfied,), passed={satisfied}),
        ]
    )
    executor.base_readings = [
        base_echo(keys=("A/check",), satisfied=set()),
        base_echo(keys=STACKED_CHECKS, satisfied={satisfied}),
        base_echo(keys=(satisfied,), satisfied={satisfied}),
    ]
    harness = stacked_walk(repos=repos, port=port, executor=executor)

    events = await bounded_walk(harness)

    assert lane_failures(events) == ()
    # B twice: a lane whose fire closed a criterion it owed is offered again in
    # the same invocation (KOD-724), and the second fire reads the criterion the
    # base satisfies at that base again and reaches the same answer.
    assert ticks_of(events)[-1].dispatched == ("A", "B", "B")
    assert ticks_of(events)[-1].unresolved_criteria == (satisfied,)
    # The criterion the base does not satisfy is finished at its lane's head;
    # the one it does satisfy is not moved at all, and neither lane's own issue
    # is written by anything.
    assert port.issues[other].state_kind is WorkflowStateKind.COMPLETED
    assert port.issues[satisfied].state_kind is WorkflowStateKind.UNSTARTED
    assert port.issues["B"].state_kind is WorkflowStateKind.UNSTARTED
    assert port.workflow_writes == [
        ("A/check", LifecycleStage.DONE),
        (other, LifecycleStage.DONE),
    ]
    # B's base is the commit A's DELIVERED ref stands at, read off A's own
    # record: the trunk answered A's base and could not have answered B's.
    delivered = recorded_branches(
        record=await lane_record(port, "A")
    ).deliverable_branch
    assert executor.base_refs == [
        TRUNK_SHA,
        repos.branches[delivered].head,
        repos.branches[delivered].head,
    ]
    assert repos.branches[delivered].head != TRUNK_SHA
    acquired = [call for call in harness.workspace.calls if call[0] == "acquire"]
    released = [call for call in harness.workspace.calls if call[0] == "release"]
    assert len(acquired) == len(released)


async def test_a_walk_whose_resolved_base_cannot_be_read_still_fires_every_lane():
    """A base tree nobody can cut ends no lane and no walk.

    The same two lanes, with the tree at the resolved base refused. Lane A's
    own base is the trunk and is read, so A finishes and delivers; lane B is
    still fired, its base cannot be read at all, and every criterion it passed
    at its head therefore stays owed rather than being crossed off on a reading
    nobody took. The walk reports them unresolved, contains no lane failure, and
    ends without an engine error.
    """
    repos = WalkRepos()
    port = stacked_board()
    executor = BaseTreeExecutor(
        [
            criteria_echo(keys=("A/check",), passed={"A/check"}),
            criteria_echo(keys=("A/check",), passed={"A/check"}),
            criteria_echo(keys=STACKED_CHECKS, passed=set(STACKED_CHECKS)),
            criteria_echo(keys=STACKED_CHECKS, passed=set(STACKED_CHECKS)),
        ]
    )
    executor.base_readings = [base_echo(keys=("A/check",), satisfied=set())]
    harness = stacked_walk(
        repos=repos,
        port=port,
        executor=executor,
        workspace=lambda repos, git: RefusedResolvedBase(repos, git=git),
    )

    events = await bounded_walk(harness)

    assert lane_failures(events) == ()
    assert ticks_of(events)[-1].dispatched == ("A", "B")
    assert ticks_of(events)[-1].unresolved_criteria == STACKED_CHECKS
    assert port.issues["A/check"].state_kind is WorkflowStateKind.COMPLETED
    assert [port.issues[key].state_kind for key in STACKED_CHECKS] == [
        WorkflowStateKind.UNSTARTED,
        WorkflowStateKind.UNSTARTED,
    ]
    assert port.workflow_writes == [("A/check", LifecycleStage.DONE)]
    # A's own base was read, so this is a lane that could not read its base and
    # not a walk in which nothing tried.
    assert executor.base_refs == [TRUNK_SHA]
    acquired = [call for call in harness.workspace.calls if call[0] == "acquire"]
    released = [call for call in harness.workspace.calls if call[0] == "release"]
    assert len(acquired) == len(released)


# ---------------------------------------------------------------------------
# KOD-684 — re-entry driven by the record alone: the recorded branch is
# checked out and nothing is minted.
# ---------------------------------------------------------------------------

#: Lane A with a second criterion, so one fire can finish part of its roster
#: and the lane is still owed when the next process enters it.
TWO_CHECKS = {"A": ("check", "second")}
A_KEYS = ("A/check", "A/second")
C_KEYS = ("C/check", "C/second")


def echoes(*, passed, rounds: int = 6):
    """Enough evaluator echoes for whatever the fire does, each passing *passed*.

    A fire that fails part of its roster may take a remediation round, and the
    number of gradings that follows is the graph's business and not this
    fixture's; every grading of one run answers the same way.
    """
    return [criteria_echo(keys=A_KEYS, passed=passed) for _ in range(rounds)]


def resumable(*, repos: WalkRepos, merger=None, ref_publisher=None, **rest):
    """A runtime over one repository family that commits as a real lane does.

    Two runtimes built over the SAME family are two processes against one
    remote: the branches and their pushed heads outlive the first one, which
    is the whole premise of entering from the record.

    *merger* and *ref_publisher* default to the family's own doubles; a test
    about a consolidation that answers something else, or one that reads what
    was published, supplies its own and keeps the reference.
    """
    git = WalkGit(repos)
    return runtime(
        persister=WalkPersister(repos),
        git=git,
        source=WalkSource(repos),
        workspace=WalkWorkspaces(repos, git=git),
        merger=WalkMerger(repos) if merger is None else merger,
        ref_publisher=WalkRefPublisher(repos)
        if ref_publisher is None
        else ref_publisher,
        **rest,
    )


async def first_fire(port, repos, *, passed=("A/check",)):
    """Run one: one fire, which finishes part of lane A's roster and records it.

    Ended at the observation of the tick that follows that fire, because a lane
    whose fire closed a criterion it owed is offered again in the same
    invocation (KOD-724) and A still owes one here. What every test built on
    this fixture re-enters is ONE fire's record: a second fire inside run one
    would put a second entry's work on it and leave the re-entry these tests
    are about with nothing left to tell apart.
    """
    harness = resumable(port=port, repos=repos, evaluations=echoes(passed=set(passed)))
    stream = drive(harness, job="first-job")
    observed = 0
    async with asyncio.timeout(WALK_BOUND_SECONDS):
        async for event in stream:
            if isinstance(event, ScopeWalkEvent):
                observed += 1
                if observed == 2:
                    break
    await stream.aclose()
    # The walk really reached the tick after the fire, so what follows is that
    # fire's record and not the record of a walk that ended some other way.
    assert observed == 2
    return harness, await lane_record(port, "A")


def mint_spy(monkeypatch) -> list[str]:
    """Record every mint of a LANE'S TWO NAMES, and mint as usual.

    Patched at the definition AND at the alias the fire holds: a mint through
    either name is a mint, and a spy on one name only answers "uncalled" for a
    mint made through the other. It records rather than raises, so what the
    test states is the empty list beside a walk that otherwise ran — a raising
    spy would be contained at the lane boundary and read as some other
    failure.

    A lane's pair of names is not the only name a fire can draw: a remediation
    round draws a fresh LOOP name of its own, which ``loop_name_spy`` below
    records and this one cannot see.
    """
    calls: list[str] = []

    def recording(issue_key):
        calls.append(issue_key)
        return mint_lane_branches(issue_key)

    for name in (
        "kodezart.domain.agent.mint_lane_branches",
        "kodezart.chains.ralph_workflow.mint_lane_branches",
    ):
        monkeypatch.setattr(name, recording)
    return calls


def loop_name_spy(monkeypatch) -> list[str]:
    """Record every draw of a fresh LOOP branch name, and draw as usual.

    The other mint. A lane's pair of names is drawn once, but a remediation
    round draws a new loop name beside the pair, so a fire that re-entered a
    recorded lane on a freshly cut loop branch could pass a count of lane mints
    and lose the work the record names (KOD-684).

    Patched at the definition and at both aliases a caller holds, so a draw
    through any of the three is recorded. The definition is where minting a
    lane's pair draws its loop name too, which makes this spy demonstrably
    live in the same walk: a fire that minted a pair shows one draw here.
    """
    calls: list[str] = []

    def recording(feature_branch):
        calls.append(feature_branch)
        return generate_ralph_branch_name(feature_branch)

    for name in (
        "kodezart.domain.agent.generate_ralph_branch_name",
        "kodezart.chains.fire_remediation.generate_ralph_branch_name",
        "kodezart.chains.fire_specification.generate_ralph_branch_name",
    ):
        monkeypatch.setattr(name, recording)
    return calls


async def test_a_recorded_lane_resumes_on_its_recorded_branch_and_mints_nothing(
    monkeypatch,
):
    """A second process enters lane A from its record and nothing else.

    Run one finishes one of A's two criteria and records its commit. Run two
    shares only the board and the remote: it checks the recorded branch out
    without cutting it, mints no name, adds its own run's association pair and
    one more commit row to the same record, and owes only the criterion run
    one did not finish.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    _, before = await first_fire(port, repos)
    assert port.issues["A/check"].state_kind is WorkflowStateKind.COMPLETED
    assert port.issues["A/second"].state_kind is WorkflowStateKind.UNSTARTED
    deliverable = recorded_branches(record=before).deliverable_branch

    minted = mint_spy(monkeypatch)
    second = resumable(port=port, repos=repos, evaluations=echoes(passed=set(A_KEYS)))
    events = await bounded_walk(second, job="second-job")

    # Nothing was minted on any path, and the lane did not fail on the way to
    # not minting: an empty spy beside a reported failure would say nothing.
    assert minted == []
    assert lane_failures(events) == ()
    assert not any("slug" in props for props in second.executor.schema_calls)
    opened = opened_branch(second.workspace.acquisitions)
    assert opened["branch_name"] == opened["ref"] == before.branch
    assert opened["create_branch"] is False
    after = await lane_record(port, "A")
    assert after.branch == before.branch
    # Exactly one more association pair, this run's, and nothing else: every
    # branch run one recorded is still named, under run one's id.
    assert {(item.branch, item.role, item.run_id) for item in after.associations} == {
        *((item.branch, item.role, item.run_id) for item in before.associations),
        (before.branch, BranchRole.LOOP, "second-job"),
        (deliverable, BranchRole.DELIVERABLE, "second-job"),
    }
    assert len(after.commits) == len(before.commits) + 1
    assert after.head_sha == repos.branches[before.branch].head
    prompt = second.executor.execution_prompts[0]
    assert "A/second live Check  bytes" in prompt
    assert "A live Check  bytes" not in prompt
    assert port.issues["A/second"].state_kind is WorkflowStateKind.COMPLETED


async def test_a_changed_check_on_re_entry_is_owed_and_graded_again():
    """Nothing of a previous grading is replayed on re-entry.

    A criterion the first fire finished is put back the way the product puts
    one back, carrying an amended Check. The second process reads the Todo set
    at entry, so that criterion is owed again and the text it is graded
    against is the amended one.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    await first_fire(port, repos)
    owed_again(port, check="materially changed Check")

    second = resumable(port=port, repos=repos, evaluations=echoes(passed=set(A_KEYS)))
    _ = await bounded_walk(second, job="second-job")

    prompt = second.executor.execution_prompts[0]
    assert "materially changed Check" in prompt
    assert "A live Check  bytes" not in prompt
    assert "A/second live Check  bytes" in prompt
    assert all(
        port.issues[key].state_kind is WorkflowStateKind.COMPLETED for key in A_KEYS
    )


@pytest.mark.parametrize("damage", ["base", "branch"])
async def test_a_recorded_lane_the_facts_no_longer_admit_is_reported_not_fired(
    monkeypatch, damage
):
    """Neither a moved base nor a vanished branch is answered by minting.

    A branch cut from a base the lane would no longer be diffed against, and a
    recorded branch the remote no longer holds, are both refusals of this one
    lane: nothing is minted in their place, no session opens, and the record
    is left exactly as the first fire wrote it.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    _, before = await first_fire(port, repos)
    minted = mint_spy(monkeypatch)
    if damage == "branch":
        repos.branches[before.branch].pushed = None

    second = resumable(
        port=port,
        repos=repos,
        evaluations=echoes(passed=set(A_KEYS)),
        trunk="other-trunk" if damage == "base" else "trunk",
    )
    await walk_reporting(
        second,
        kind="LaneEntryError",
        match=("is not the base" if damage == "base" else "absent from the remote"),
    )
    assert minted == []
    assert second.executor.schema_calls == []
    assert await lane_record(port, "A") == before


async def test_the_scoped_arm_holds_no_checkpointer_while_the_authored_arm_keeps_it():
    """Every graph of BOTH scoped lanes, none compiled with a saver.

    The scoped arm holds two lanes, one per origin shape, and each has its own
    fire engine: a test that inspected only the origin this harness runs on
    would say nothing about the other, and a forge-origin run would persist
    lane state unnoticed. A saver IS configured for this deployment, and the
    authored arm still holds it, so the absence is a composition choice and
    not an unconfigured deployment (KOD-840). A whole scoped run then writes
    nothing to it.
    """
    saver = InMemorySaver()
    harness = runtime(saver=saver)

    for url in (ORIGIN, FORGE_ORIGIN):
        lane = harness.engine._scoped_arm._lane_for(url)
        assert lane.graph.checkpointer is None
        assert lane.fire.native_graph is not None
        assert lane.fire.native_graph.checkpointer is None
        assert lane.fire.checkpointer is None
        assert lane.fire.implementation._quality_gate._checkpointer is None
    # The two are different engines, so the loop above asserted twice about
    # two things and not twice about one.
    assert harness.engine._scoped_arm._lane_for(ORIGIN) is not (
        harness.engine._scoped_arm._lane_for(FORGE_ORIGIN)
    )
    # Not vacuous: the arm a run without a scope takes still holds it.
    authored = harness.engine.arm_for(None).fire
    assert authored.graph.checkpointer is saver
    assert authored.implementation._quality_gate._checkpointer is saver

    events = await bounded_walk(harness)

    # The run really ran, so the empty saver below is a statement about it.
    assert ticks_of(events)[-1].dispatched == ("A",)
    assert [checkpoint async for checkpoint in saver.alist(None)] == []


# ---------------------------------------------------------------------------
# KOD-433 — the subject text is read once at entry and compared with the
# digest the record pinned.
# ---------------------------------------------------------------------------


def subject_reads(port, monkeypatch) -> list[str]:
    """Every read of a subject's own text the board answers, in order.

    The criterion says the text is read ONCE at entry, and a re-read returns
    the same bytes in every fixture here: only the count can tell the two
    apart.
    """
    reads: list[str] = []
    answering = port.read_fire_spec

    async def counted(*, issue_key):
        reads.append(issue_key)
        return await answering(issue_key=issue_key)

    monkeypatch.setattr(port, "read_fire_spec", counted)
    return reads


async def test_the_digest_is_pinned_at_the_first_record_write(monkeypatch):
    """What the record keeps about the subject is the sha256 of the text read.

    Computed here rather than through the production function: an expectation
    taken from the code under test is a tautology on the algorithm, and the
    record's own field would accept a digest of any other one.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    reads = subject_reads(port, monkeypatch)
    minted = mint_spy(monkeypatch)
    _, record = await first_fire(port, repos)

    assert (
        record.body_digest
        == hashlib.sha256(port.issues["A"].body.encode("utf-8")).hexdigest()
    )
    assert reads == ["A"]
    # The spy every "mints nothing" assertion rests on sees the one mint a
    # first fire makes: a spy that saw nothing here would answer "uncalled"
    # for every resumed lane too.
    assert minted == ["A"]


def unpin_digest(port, key: str) -> None:
    """Rewrite one lane's record the way a record written before the pin reads.

    The field is dropped from the stored JSON rather than set to null: every
    record a board carried before the pin existed has no such field at all, and
    that is the record a resumed lane meets first.
    """
    prefix = native_operation().marker_prefixes["run_state"]
    for index, comment in enumerate(port.comments):
        if comment.body.startswith(f"[{prefix}:{key}]"):
            port.comments[index] = comment.model_copy(
                update={
                    "body": "\n".join(
                        line
                        for line in comment.body.splitlines()
                        if '"bodyDigest"' not in line
                    )
                }
            )


async def test_a_record_with_no_digest_is_entered_and_pinned_by_its_next_write(
    monkeypatch,
):
    """A lane whose record predates the pin re-enters and leaves one behind.

    Every record written before the digest existed carries none, so this is the
    path production takes first: the entry has nothing to compare, the lane
    resumes on its recorded branch without minting, reads its subject once, and
    the commit that follows pins the digest of the text it read.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    _, before = await first_fire(port, repos)
    assert before.body_digest is not None
    unpin_digest(port, "A")
    assert (await lane_record(port, "A")).body_digest is None

    minted = mint_spy(monkeypatch)
    second = resumable(port=port, repos=repos, evaluations=echoes(passed=set(A_KEYS)))
    reads = subject_reads(port, monkeypatch)
    events = await bounded_walk(second, job="second-job")

    # Not refused, and not re-entered by minting a second branch beside the
    # record: the lane ran, on the branch the record names.
    assert lane_failures(events) == ()
    assert minted == []
    assert reads == ["A"]
    assert len(second.executor.execution_prompts) == 1
    assert "Exact native subject A" in second.executor.execution_prompts[0]
    after = await lane_record(port, "A")
    assert (
        after.body_digest
        == hashlib.sha256(port.issues["A"].body.encode("utf-8")).hexdigest()
    )


async def test_a_subject_amended_between_runs_is_refused_by_digest_not_re_read(
    monkeypatch,
):
    """An edited subject is an amendment, never a silent re-read.

    Run one pins the digest. The subject is then edited, and the second
    process reads the text once at entry, compares, and refuses this one lane
    before any session: the criteria it owes were graded against the text the
    digest names. Nothing re-pins the digest, so the record is untouched.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    _, before = await first_fire(port, repos)
    port.issues["A"] = port.issues["A"].model_copy(
        update={"body": "A later subject body must not be read into a resumed lane"}
    )
    minted = mint_spy(monkeypatch)

    second = resumable(port=port, repos=repos, evaluations=echoes(passed=set(A_KEYS)))
    reads = subject_reads(port, monkeypatch)
    await walk_reporting(
        second, kind="SubjectAmendedError", match="differs from the recorded"
    )

    # Read once, at the entry, and compared there: a second process that
    # re-read the text on its way past the comparison would read it again.
    assert reads == ["A"]
    assert minted == []
    assert second.executor.execution_prompts == []
    assert second.executor.evaluation_prompts == []
    after = await lane_record(port, "A")
    assert after == before
    assert (
        after.body_digest
        == before.body_digest
        != hashlib.sha256(port.issues["A"].body.encode("utf-8")).hexdigest()
    )


# ---------------------------------------------------------------------------
# KOD-449 — a killed scope re-enters and dispatches exactly the lanes that
# are left, on their recorded branches, reading no merge state to decide.
# ---------------------------------------------------------------------------

FORGE_ORIGIN = "https://github.com/owner/repo"
#: Lane C owes two criteria, so the fire killed mid-flight leaves one finished
#: and one open and the re-entry has something to tell apart.
THREE_LANES = ("A", "B", "C")


def one_check_echoes(key: str, rounds: int = 2):
    """The gradings one single-criterion lane's fire asks for."""
    return [
        criteria_echo(keys=(f"{key}/check",), passed={f"{key}/check"})
        for _ in range(rounds)
    ]


async def test_kill_and_re_enter_dispatches_exactly_the_remaining_lanes(monkeypatch):
    """A scope killed after two lanes finished re-enters on the third alone.

    Run one delivers A and B — really delivers them: the forge double answers
    each pull request's head at the sha the walk's own merger published, so
    the identity check a delivery makes passes and no lane fails — and is then
    KILLED while C's fire is in flight, after C's first criterion was crossed
    off and recorded: the walk's task is cancelled, and cancellation is a
    BaseException the lane boundary does not contain, so the run really ends
    where a process would. Run two shares only the board, the remote and the
    forge: it dispatches C and nothing else, fails no lane, resumes on C's
    recorded branch without minting, acquires no other branch, owes only C's
    open criterion, and reads no pull-request state at all before C's first
    session.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=THREE_LANES, checks={"C": ("check", "second")})
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        first = resumable(
            port=port,
            repos=repos,
            lanes=THREE_LANES,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=[
                *one_check_echoes("A"),
                *one_check_echoes("B"),
                *[criteria_echo(keys=C_KEYS, passed={"C/check"}) for _ in range(4)],
            ],
        )
        seen: list[object] = []

        async def walk() -> None:
            async for event in drive(first, job="first-job", origin=FORGE_ORIGIN):
                seen.append(event)

        task = asyncio.create_task(walk())
        # The same bound over run one's own spin: the walk either reaches C or
        # ends, and "neither" is a failure rather than a test that never
        # returns.
        deadline = asyncio.get_running_loop().time() + 60
        while not any(
            isinstance(event, ScopeLaneEvent)
            and event.lane_key == "C"
            and isinstance(event.event, WorkflowIterationEvent)
            for event in seen
        ):
            assert not task.done(), "the walk ended before lane C was in flight"
            assert asyncio.get_running_loop().time() < deadline, (
                "the walk never reached lane C"
            )
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert port.issues["C/check"].state_kind is WorkflowStateKind.COMPLETED
        assert port.issues["C/second"].state_kind is WorkflowStateKind.UNSTARTED
        killed = await lane_record(port, "C")
        # A and B ran to delivery before the kill, and neither failed doing it:
        # a contained delivery failure would have been reported here.
        assert len(wire.creates) == 2
        assert lane_failures(seen) == ()
        assert killed.branch not in {create["head"] for create in wire.creates}

        minted = mint_spy(monkeypatch)
        second = resumable(
            port=port,
            repos=repos,
            lanes=THREE_LANES,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=[
                criteria_echo(keys=C_KEYS, passed=set(C_KEYS)) for _ in range(6)
            ],
        )
        # The wire outlives the kill, so what "zero" means is "none more than
        # the reads A's and B's own deliveries made in run one": each delivery
        # reads its pull request's own state three times — at the identity
        # check that follows opening it, once more before it returns, and again
        # where the lane graph completes.
        before_re_entry = len(wire.pr_reads)
        assert before_re_entry == 6
        reads_at_first_session: list[int] = []
        sessions = second.executor.stream

        def recording(**arguments):
            reads_at_first_session.append(len(wire.pr_reads))
            return sessions(**arguments)

        monkeypatch.setattr(second.executor, "stream", recording)
        # Bounded: a walk that offered a lane forever would hang here instead
        # of failing, and a hang is not an assertion. This run is five ticks:
        # A and B are offered for a delivery they already recorded and rest,
        # C fires and closes its last criterion, C is offered for a delivery it
        # then records too and rests, and the fifth has nothing left to offer.
        events = await bounded_walk(second, job="second-job", origin=FORGE_ORIGIN)

        assert len(ticks_of(events)) == 5
        assert [
            event.lane_key
            for event in events
            if isinstance(event, ScopeLaneEvent)
            and isinstance(event.event, WorkflowIterationEvent)
        ] == ["C"]
        assert not [
            prompt
            for prompt in second.executor.execution_prompts
            for key in ("A", "B")
            if f"Exact native subject {key}" in prompt
        ]
        # Exactly the lanes that are left, read off the walk's own report
        # rather than off which lanes emitted an iteration: a lane that
        # entered and then failed would appear here.
        final = [
            event.observation for event in events if isinstance(event, ScopeWalkEvent)
        ][-1]
        assert final.dispatched == ("C",)
        assert final.failed_lanes == ()
        assert minted == []
        # One branch was checked out in this whole run, and it is the one the
        # record names.
        assert {
            acquisition["branch_name"]
            for acquisition in second.workspace.acquisitions
            if acquisition["branch_name"]
        } == {killed.branch}
        opened = opened_branch(second.workspace.acquisitions)
        assert opened["branch_name"] == opened["ref"] == killed.branch
        assert opened["create_branch"] is False
        prompt = second.executor.execution_prompts[0]
        assert "C/second live Check  bytes" in prompt
        assert "C live Check  bytes" not in prompt
        # No pull request's own state was read to decide any of that: the entry
        # reads the record and the remote head, and a delivery's own read comes
        # after the lane has already worked. The unfiltered open-delivery
        # listing is a different request and is not counted here at all; since
        # KOD-431 the walk reads it for no candidate anyway.
        assert reads_at_first_session
        assert reads_at_first_session[0] == before_re_entry
        # And C's own delivery does make its three, after the lane has worked.
        assert len(wire.pr_reads) == before_re_entry + 3
        assert len(wire.creates) == 3
    finally:
        await forge.close()


# ---------------------------------------------------------------------------
# KOD-844 — a recorded lane with every criterion Done and no pull request is
# dispatched for its delivery alone.
# ---------------------------------------------------------------------------


#: The seconds ANY walk of this module is allowed before the test fails.
#:
#: A lane the walk selects and neither dispatches nor rests is offered again
#: on the next tick, forever: the regression answers nothing rather than
#: answering wrongly, and an unbounded test would hang a whole run instead of
#: failing one case. The bound is orders above what these walks take and far
#: under anything a reader would wait out.
#:
#: Every walk here goes through it — the whole-walk ones through
#: ``bounded_walk`` below, the ones a test reads event by event under this same
#: bound written out, and the one walk a test cancels mid-flight under its own
#: deadline. A walk that stops fitting the bound is a defect to find, never a
#: bound to raise.
WALK_BOUND_SECONDS = 60


async def bounded_walk(harness, **rest):
    """Every event of one walk, or a failure where the walk does not end."""
    async with asyncio.timeout(WALK_BOUND_SECONDS):
        return [event async for event in drive(harness, **rest)]


def ticks_of(events):
    """One entry per tick the walk observed, which is one per selection read."""
    return [event.observation for event in events if isinstance(event, ScopeWalkEvent)]


async def stopped_at_consolidation(port, repos, *, origin, forge=None, lane="A"):
    """Run one of one lane, ended the instant its loop branch was consolidated.

    The stream is closed where a process dies, and generator close is a
    ``BaseException`` the lane boundary does not contain, so the run really
    ends there. What it leaves is exactly the state this criterion is about:
    the criterion crossed off, a record naming the lane's branches, and no
    pull request anywhere.
    """
    harness = resumable(
        port=port,
        repos=repos,
        origin=origin,
        forge=forge,
        trunk="main",
        evaluations=one_check_echoes(lane, rounds=4),
    )
    stream = drive(harness, job="first-job", origin=origin)
    async with asyncio.timeout(WALK_BOUND_SECONDS):
        async for event in stream:
            if isinstance(event, ScopeLaneEvent) and isinstance(
                event.event, WorkflowConsolidationEvent
            ):
                break
    await stream.aclose()
    assert port.issues[f"{lane}/check"].state_kind is WorkflowStateKind.COMPLETED
    record = await lane_record(port, lane)
    assert record.pr is None
    return harness, record


async def test_a_finished_lane_without_a_pull_request_is_delivered_without_a_loop():
    """The lane the kill left between its last cross-off and its delivery.

    Its gap is empty, so no reading of readiness would ever offer it as a
    candidate for work, and its record carries no pull request, so nothing it
    produced is published. Run two dispatches it for the delivery alone: no
    implementation session opens at all, the recorded branches are
    consolidated and reviewed, one pull request is opened from the branch the
    record names, and the record then carries it. Run three has nothing left
    to do with the lane and says so instead of offering it again.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A",))
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        _, killed = await stopped_at_consolidation(
            port, repos, origin=FORGE_ORIGIN, forge=forge
        )
        deliverable = recorded_branches(record=killed).deliverable_branch
        assert wire.creates == []

        second = resumable(
            port=port,
            repos=repos,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=one_check_echoes("A", rounds=4),
        )
        with structlog.testing.capture_logs() as delivering:
            events = await bounded_walk(second, job="second-job", origin=FORGE_ORIGIN)

        assert lane_failures(events) == ()
        # No iteration, and no session an iteration would have opened: the
        # lane had nothing left to execute and none was asked for.
        assert second.executor.execution_prompts == []
        assert not [
            event
            for event in events
            if isinstance(event, ScopeLaneEvent)
            and isinstance(event.event, WorkflowIterationEvent)
        ]
        # One pull request, from the branch the record names — not from the
        # loop branch the record stands on.
        assert [create["head"] for create in wire.creates] == [deliverable]
        assert killed.branch != deliverable
        # And its checks were watched, at that same head and once: what the
        # record carries is a delivery whose checks were observed, not a pull
        # request somebody opened.
        assert wire.watches == [f"/repos/owner/repo/commits/{deliverable}/check-runs"]
        delivered = await lane_record(port, "A")
        assert delivered.pr is not None
        assert delivered.pr.number == ScopeForgeWire.FIRST_NUMBER
        assert str(ScopeForgeWire.FIRST_NUMBER) in delivered.pr.url
        # The lane's delivery is what the walk reports it did.
        walked = [
            event.observation for event in events if isinstance(event, ScopeWalkEvent)
        ]
        assert walked[-1].dispatched == ("A",)
        # And the turn rested the lane although its delivery DELIVERED. The
        # rest is unconditional: it reads nothing about how the fire ended, so
        # a delivered turn and a skipped one rest alike and no per-fire ending
        # reaches the next dispatch decision (KOD-724). Two ticks and no more
        # is what says the rest happened here — a walk that rested this lane
        # only on the skipped ending would offer it again on a third tick.
        assert len(walked) == 2 and walked[-1].rested_lanes == ("A",)
        assert [
            event["lane"]
            for event in delivering
            if event.get("event") == "scope_lane_finished_turn_rested"
        ] == ["A"]

        third = resumable(
            port=port,
            repos=repos,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=one_check_echoes("A", rounds=4),
        )
        with structlog.testing.capture_logs() as logs:
            after = await bounded_walk(third, job="third-job", origin=FORGE_ORIGIN)

        settled = ticks_of(after)
        # Two ticks and no more: the one that selects the lane and rests it,
        # and the one that finds nothing left to offer. A lane the walk failed
        # to rest would be selected again on every tick, so the count is what
        # tells a rested lane from a walk spinning on it.
        assert len(settled) == 2
        assert settled[-1].dispatched == ()
        assert lane_failures(after) == ()
        assert third.executor.execution_prompts == []
        assert len(wire.creates) == 1
        # Rested by name and exactly once: the record carries the pull
        # request, so there is nothing to do and the walk cannot spin on it.
        assert [
            event["lane"]
            for event in logs
            if event.get("event") == "scope_lane_nothing_to_do"
        ] == ["A"]
    finally:
        await forge.close()


class DivergentConsolidation(FakeBranchMerger):
    """A consolidation that answers "the two branches diverged", moving nothing.

    Divergence is one of the four answers the merger is a total function over
    and it raises nothing: the fire routes straight to its terminal without
    reviewing or delivering, so what the turn leaves is a lane that reached no
    pull request at all and a board holding exactly what it held before.
    """

    def __init__(self, repos: WalkRepos) -> None:
        super().__init__()
        self.repos = repos

    async def consolidate(self, *, source_branch, **rest):
        self.calls.append(
            {"method": "consolidate", "source_branch": source_branch, **rest}
        )
        head = self.repos.head_of(source_branch)
        # The sha is the one this walk's repositories hold for the branch the
        # consolidation was asked about: a made-up tip would let a later read
        # stand on a head no repository of the walk carries.
        assert head is not None, source_branch
        return ConsolidationOutcome(
            status=ConsolidationStatus.DIVERGENT, feature_tip_sha=head
        )


async def test_a_delivery_only_turn_that_reaches_no_pull_request_rests_the_lane():
    """The finished lane whose one delivery-only turn opens nothing (KOD-724).

    Its consolidation answers that the recorded branches diverged, so the fire
    ends with no delivery: nothing is published, nothing is recorded, and the
    board holds what it held before the turn. The lane rests all the same,
    because a lane selected for its delivery alone takes ONE such turn per
    invocation whatever that turn's fire did — an identical second turn would
    say what this one said — and resting it is what keeps the walk from
    consolidating and reviewing the lane once per tick for the rest of the
    invocation. Nothing about the fire's ending is read to decide it (KOD-725).
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A",))
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        _, killed = await stopped_at_consolidation(
            port, repos, origin=FORGE_ORIGIN, forge=forge
        )
        assert wire.creates == []

        second = resumable(
            port=port,
            repos=repos,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=one_check_echoes("A", rounds=4),
            merger=DivergentConsolidation(repos),
        )
        with structlog.testing.capture_logs() as logs:
            events = await bounded_walk(second, job="second-job", origin=FORGE_ORIGIN)

        # Two ticks: the delivery-only turn, and the one with nothing left to
        # offer. A lane the walk failed to rest would be offered on every tick
        # forever, which the bound catches as a failure rather than a hang.
        assert len(ticks_of(events)) == 2
        assert lane_failures(events) == ()
        assert ticks_of(events)[-1].dispatched == ("A",)
        assert ticks_of(events)[-1].rested_lanes == ("A",)
        # The turn was really fired and its fire really reached no delivery.
        assert ticks_of(events)[-1].skipped_lanes == ("A",)
        assert [
            event["lane"]
            for event in logs
            if event.get("event") == "scope_lane_finished_turn_rested"
        ] == ["A"]
        # No session was opened for it either — the lane owed no work — and
        # nothing about the lane moved: no pull request on the forge, none on
        # the record, and the deliverable branch the kill left still named.
        assert second.executor.execution_prompts == []
        assert wire.creates == []
        assert (await lane_record(port, "A")).pr is None
        assert recorded_branches(record=killed).deliverable_branch
    finally:
        await forge.close()


async def test_a_reopened_criterion_refuses_a_deliver_only_entry(monkeypatch):
    """A criterion reopened between the walk's selection and the fire's entry.

    The lane was selected because its subtree owed nothing, and the fire's own
    reading of the finished roster is taken again inside the graph. Between the
    two the board moves: the criterion returns to Todo the way an amendment
    puts one back. The roster read refuses and names it, before any session
    opens and before anything is published.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A",))
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        await stopped_at_consolidation(port, repos, origin=FORGE_ORIGIN, forge=forge)

        second = resumable(
            port=port,
            repos=repos,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=one_check_echoes("A", rounds=4),
        )
        read_spec = port.read_fire_spec
        reopened: list[str] = []

        async def reopening(*, issue_key):
            spec = await read_spec(issue_key=issue_key)
            if not reopened:
                reopened.append(issue_key)
                owed_again(port)
            return spec

        monkeypatch.setattr(port, "read_fire_spec", reopening)
        events = await bounded_walk(second, job="second-job", origin=FORGE_ORIGIN)

        assert reopened == ["A"]
        failures = lane_failures(events)
        assert [failure.issue_key for failure in failures] == ["A"]
        assert [failure.error.error_kind for failure in failures] == [
            "FireSpecEntryError"
        ]
        assert "A/check" in failures[0].error.error
        assert "not finished" in failures[0].error.error
        # Nothing was published and nothing was executed on the way to the
        # refusal: it lands before the lane does anything at all.
        assert wire.creates == []
        assert second.executor.execution_prompts == []
        assert (await lane_record(port, "A")).pr is None
    finally:
        await forge.close()


async def test_a_forge_less_origin_never_selects_a_finished_lane():
    """The same record, on an origin no pull request could be opened on.

    Such a lane could never record a pull request, so delivering it would
    change nothing about it and every invocation would consolidate and review
    it again. It is not a candidate at all there: the walk dispatches nothing,
    reports no failure, and leaves the lane for a person.
    """
    repos = WalkRepos()
    port = board(lanes=("A",))
    _, killed = await stopped_at_consolidation(port, repos, origin=ORIGIN)

    second = resumable(
        port=port,
        repos=repos,
        origin=ORIGIN,
        trunk="main",
        evaluations=one_check_echoes("A", rounds=4),
    )
    events = await bounded_walk(second, job="second-job")

    walked = [
        event.observation for event in events if isinstance(event, ScopeWalkEvent)
    ]
    assert walked[-1].dispatched == ()
    assert lane_failures(events) == ()
    assert second.executor.execution_prompts == []
    # Not vacuous: the record is there, with every criterion Done and no pull
    # request, which on a delivering origin is the entry the walk fires.
    assert (await lane_record(port, "A")).pr is None
    assert recorded_branches(record=killed).deliverable_branch


def open_listings(wire, *, after: int) -> list[httpx.Request]:
    """Every unfiltered open-pull-request listing this wire answered since *after*.

    That listing is the one question an origin is asked about lanes in
    general — which of them a delivery is already open for — and asking it
    about a lane selected for its delivery alone is exactly what a finished
    candidate must not go through. A listing filtered by head is a different
    question, asked by the delivery about its own branch.
    """
    return [
        request
        for request in wire.requests[after:]
        if request.method == "GET"
        and request.url.path.endswith("/pulls")
        and request.url.params.get("state") == "open"
        and "head" not in request.url.params
    ]


async def test_a_pull_request_opened_but_not_recorded_is_reused_and_recorded(
    monkeypatch,
):
    """The lane whose process died between the create and the record write.

    Run one really delivers A: the pull request is opened on the forge and the
    write that would put it on A's record is lost. What that leaves is a lane
    whose criteria are all Done and whose record carries no pull request —
    indistinguishable, on the board, from a lane that never delivered at all.

    So run two selects it for its delivery alone again, and neither delivery
    probe is asked about it: what that probe excludes is a lane a pull request
    is already open for, which is this lane exactly. The delivery finds its
    own open pull request by the head it stands on, reuses it rather than
    opening a second one, and the record finally carries it.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A",))
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    record_pull_request = TrackerLaneStateWriter.record_pull_request
    lost: list[str] = []

    async def losing(self, *, lane_key, pr, visibility):
        """The first record write of the run, lost after the create it follows."""
        if not lost:
            lost.append(lane_key)
            raise LaneRecordWriteError(
                lane_key=lane_key, reason="the record write did not reach the board"
            )
        return await record_pull_request(
            self, lane_key=lane_key, pr=pr, visibility=visibility
        )

    try:
        first = resumable(
            port=port,
            repos=repos,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=one_check_echoes("A", rounds=4),
        )
        monkeypatch.setattr(TrackerLaneStateWriter, "record_pull_request", losing)
        opened = await bounded_walk(first, job="first-job", origin=FORGE_ORIGIN)

        assert lost == ["A"]
        killed = await lane_record(port, "A")
        deliverable = recorded_branches(record=killed).deliverable_branch
        # The premise, asserted rather than assumed: the pull request IS open
        # on the forge, the lane's turn failed, and its record says nothing
        # about the pull request.
        assert [create["head"] for create in wire.creates] == [deliverable]
        assert [failure.issue_key for failure in lane_failures(opened)] == ["A"]
        assert killed.pr is None
        assert port.issues["A/check"].state_kind is WorkflowStateKind.COMPLETED

        answered = len(wire.requests)
        second = resumable(
            port=port,
            repos=repos,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=one_check_echoes("A", rounds=4),
        )
        events = await bounded_walk(second, job="second-job", origin=FORGE_ORIGIN)

        assert lane_failures(events) == ()
        assert ticks_of(events)[-1].dispatched == ("A",)
        # Delivery only, again: no session opened for a lane that owes nothing.
        assert second.executor.execution_prompts == []
        # Reused, not re-opened: one create in both runs together.
        assert len(wire.creates) == 1
        recorded = await lane_record(port, "A")
        assert recorded.pr is not None
        assert recorded.pr.number == ScopeForgeWire.FIRST_NUMBER
        # And the lane was never put through the probe that would have
        # excluded it for the very pull request it exists to finish.
        assert open_listings(wire, after=answered) == []
    finally:
        await forge.close()


async def test_a_criterion_reopened_on_readmission_turns_the_lane_into_a_ready_one(
    monkeypatch,
):
    """The criterion reopened between the selection read and the readmission.

    A lane selected for its delivery alone is readmitted by the question its
    selection asked — is this issue still reported finished — and here the
    answer changes under it: the criterion is put back the way an amendment
    puts one back. The turn ends there, unfired, and the lane is not rested:
    it owes work now, so the next tick offers it as a ready lane and it runs
    the loop, delivers, and records its pull request. Readmitting it anyway
    would carry an entry that owes nothing into a graph whose own reading of
    the roster refuses, and the lane would be reported failed instead.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A",))
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        await stopped_at_consolidation(port, repos, origin=FORGE_ORIGIN, forge=forge)

        second = resumable(
            port=port,
            repos=repos,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=one_check_echoes("A", rounds=6),
        )
        ready_read = scope_runtime.read_scope_ready
        reads = []

        async def reopening(**rest):
            """Reopen the criterion the instant the selection read has answered."""
            answered = await ready_read(**rest)
            reads.append(answered)
            if len(reads) == 1:
                owed_again(port)
            return answered

        monkeypatch.setattr(scope_runtime, "read_scope_ready", reopening)
        events = await bounded_walk(second, job="second-job", origin=FORGE_ORIGIN)

        # The window is the one the test claims: the lane was selected as a
        # finished one and the very next read no longer reports it so.
        assert [issue.issue_key for issue in reads[0].closed] == ["A"]
        assert reads[1].closed == ()
        assert [row.issue.issue_key for row in reads[1].ready] == ["A"]
        # Fired as a ready lane, once, on the tick after the one that dropped
        # it: one implementation session, no failure, one pull request.
        assert lane_failures(events) == ()
        assert ticks_of(events)[-1].dispatched == ("A",)
        assert len(second.executor.execution_prompts) == 1
        assert len(wire.creates) == 1
        delivered = await lane_record(port, "A")
        assert delivered.pr is not None
        assert delivered.pr.number == ScopeForgeWire.FIRST_NUMBER
    finally:
        await forge.close()


# ---------------------------------------------------------------------------
# KOD-721 on the delivery-only path — a lane selected for its delivery alone
# stands on a base too, so the gate on its blockers is owed there as well.
# ---------------------------------------------------------------------------


async def test_a_finished_candidates_blockers_are_gated_like_a_ready_ones():
    """The lane selected to deliver is gated on its blockers before it resolves.

    B owes nothing and holds a record with no pull request, so it is selected
    for its delivery alone; A is closed and recorded nothing, so B's base
    resolution would assume A's work reached the trunk. That assumption is
    wrong in one observable case, and it is wrong the same way for a lane
    delivering as for a lane about to run: the pull request B would open
    stands on the base this gate is about. The gate asks once, B is refused
    by the resolution error naming A, and nothing is opened for it.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    finish_by_hand(port, "A")
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        _, killed = await stopped_at_consolidation(
            port, repos, origin=FORGE_ORIGIN, forge=forge, lane="B"
        )
        assert killed.pr is None

        second = resumable(
            port=port,
            repos=repos,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=one_check_echoes("B", rounds=4),
        )
        probe = FakeDeliveryProbe(delivered=("A",))
        second.engine._scoped_arm._probe_for = lambda _: probe
        events = await bounded_walk(second, job="second-job", origin=FORGE_ORIGIN)

        # The premise, asserted: no lane on this board owes a criterion, so the
        # walk's ready set is empty on every tick and B can only have been
        # selected as a candidate for its delivery alone.
        assert [tick.ready for tick in ticks_of(events)] == [(), (), ()]
        failures = lane_failures(events)
        assert [failure.issue_key for failure in failures] == ["B"]
        assert [failure.error.error_kind for failure in failures] == [
            "BaseResolutionError"
        ]
        assert failures[0].error.error.endswith("for the blocker A")
        # Asked once, about the blocker and about nothing else, and about
        # merge state never: the whole sequence, not one key's count.
        assert probe.calls == ["A"]
        assert probe.merge_state.calls == []
        # Refused before anything was published: the delivery this lane was
        # selected for never ran.
        assert wire.creates == []
        assert (await lane_record(port, "B")).pr is None
        assert second.executor.execution_prompts == []
    finally:
        await forge.close()


# ---------------------------------------------------------------------------
# KOD-328 — a dependent lane's pull request stands on its blocker's recorded
# branch, and a base the remote does not hold refuses instead of trunk.
# ---------------------------------------------------------------------------


class RefusingCreate(ScopeForgeWire):
    """A forge that will not open a pull request for one lane's branch.

    Which one is refused is decided by the head the request names, because a
    native lane's deliverable branch carries its own issue key. So the lane
    whose delivery fails is chosen, and every other lane's request — the
    dependent lane's own create included — is answered as usual.
    """

    def __init__(self, *, refuses: str, **rest) -> None:
        super().__init__(**rest)
        self.refuses = refuses

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/pulls"):
            head = json.loads(request.content)["head"]
            if head.startswith(f"kodezart/{self.refuses}-"):
                self.requests.append(request)
                return httpx.Response(422, json={"message": "no pull request here"})
        return super().__call__(request)


def bases_of(events):
    """The base each lane was actually prepared with, off the run's own event."""
    return {
        event.lane_key: event.event.base_branch
        for event in events
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, WorkflowScopeBaseEvent)
    }


def pull_numbers_read(wire) -> set[str]:
    """The pull requests whose own state this wire was asked about."""
    return {request.url.path.rsplit("/", 1)[-1] for request in wire.pr_reads}


async def test_a_dependent_lane_opens_its_pull_request_against_its_blockers_branch():
    """B's pull request is opened against the branch A's record names.

    A's own delivery is refused by the forge, so A holds no pull request at
    all when B delivers: nothing about B's base can have come from reading
    one. What B stands on is the deliverable branch A's record names, which is
    what B's pull request is then opened against — and the only pull request
    whose state this run reads is B's own.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    wire = RefusingCreate(refuses="A", head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        harness = resumable(
            port=port,
            repos=repos,
            lanes=("A", "B"),
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=[
                *one_check_echoes("A", rounds=4),
                *one_check_echoes("B", rounds=4),
            ],
        )
        events = await bounded_walk(harness, job="stacked-job", origin=FORGE_ORIGIN)

        record = await lane_record(port, "A")
        deliverable = recorded_branches(record=record).deliverable_branch
        # A's turn ended in the forge's own error, and A was offered once: a
        # failed lane is rested for the rest of the invocation.
        failures = lane_failures(events)
        assert [failure.issue_key for failure in failures] == ["A"]
        assert [failure.error.error_kind for failure in failures] == ["ForgeAPIError"]
        final = [
            event.observation for event in events if isinstance(event, ScopeWalkEvent)
        ][-1]
        assert final.dispatched == ("A", "B")
        assert record.pr is None
        # One pull request exists, B's, and it stands on A's recorded branch —
        # not on A's loop branch and not on the trunk.
        assert len(wire.creates) == 1
        assert wire.creates[-1]["base"] == deliverable
        assert wire.creates[-1]["head"] == (
            recorded_branches(record=await lane_record(port, "B")).deliverable_branch
        )
        assert deliverable != record.branch
        assert bases_of(events)["B"] == deliverable
        assert bases_of(events)["B"] not in TRUNK_BRANCHES
        # Nothing read a pull request of A's to settle any of that: A has none,
        # and every state read this run made names B's.
        assert pull_numbers_read(wire) == {str(ScopeForgeWire.FIRST_NUMBER)}
        assert wire._numbers[wire.creates[-1]["head"]] == ScopeForgeWire.FIRST_NUMBER
        # And B's own delivery completed: it did not merely reach the create
        # while its blocker's turn failed. A lane whose delivery was refused
        # anywhere after the create would fail with A and carry no pull
        # request on its record either.
        delivered = await lane_record(port, "B")
        assert delivered.pr is not None
        assert delivered.pr.number == ScopeForgeWire.FIRST_NUMBER
    finally:
        await forge.close()


async def test_a_blocker_branch_absent_from_the_remote_refuses_without_trunk():
    """A base that resolved and is not on the remote is a refusal, never trunk.

    A delivers and its record names its deliverable branch; the branch is then
    gone from the remote, as a branch somebody deleted is. B's base resolution
    located the premise and cannot find it published, so B refuses. Falling
    back to the trunk would diff and deliver B against a tree A's work is not
    in, and look like a lane that simply had no blocker.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
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
            evaluations=[
                *one_check_echoes("A", rounds=4),
                *one_check_echoes("B", rounds=4),
            ],
        )
        deleted: list[str] = []
        events = []
        async with asyncio.timeout(WALK_BOUND_SECONDS):
            async for event in drive(harness, job="stacked-job", origin=FORGE_ORIGIN):
                events.append(event)
                if (
                    isinstance(event, ScopeLaneEvent)
                    and isinstance(event.event, LaneDeliveryEvent)
                    and event.lane_key == "A"
                    and not deleted
                ):
                    # A delivered, so its branch is published and its record names
                    # it. Now the remote holds it no longer: what the repository
                    # committed locally is untouched, which is what a branch
                    # somebody deleted on the remote leaves behind.
                    branch = recorded_branches(
                        record=await lane_record(port, "A")
                    ).deliverable_branch
                    repos.branches[branch].pushed = None
                    deleted.append(branch)

        assert deleted
        assert (await lane_record(port, "A")).pr is not None
        failures = lane_failures(events)
        assert [failure.issue_key for failure in failures] == ["B"]
        assert [failure.error.error_kind for failure in failures] == [
            "BaseResolutionError"
        ]
        assert "absent from the remote" in failures[0].error.error
        # The branch is on the refusal itself. The walk's report carries a
        # typed error's message and not its fields, so the error is asked for
        # again here, from the resolver this walk holds and over the board and
        # remote it left: the premise it names is A's own recorded branch.
        with pytest.raises(BaseResolutionError) as caught:
            await harness.engine._scoped_arm._resolver.resolve(
                issue_key="B",
                repo_path="/tmp/walk",
                integration_workspace="/tmp/walk-integration",
                trunk="main",
                now=datetime.now(tz=UTC),
            )
        assert caught.value.branches == (deleted[0],)
        # B never reached a base at all, so nothing named the trunk for it,
        # and A's is the only pull request this run opened.
        assert "B" not in bases_of(events)
        assert [create["head"] for create in wire.creates] == [deleted[0]]
    finally:
        await forge.close()


# ---------------------------------------------------------------------------
# KOD-431 — the walk asks an origin nothing about a candidate. Where a lane
# stands is its own tracker record, never the forge's list of open deliveries.
# ---------------------------------------------------------------------------


def probe_of(harness, *, origin: str):
    """The origin's own delivery reader, as the walk's composition built it."""
    return harness.engine._scoped_arm._probe_for(origin)


async def test_a_scope_whose_pull_requests_are_all_open_still_walks_to_completion():
    """Every lane's delivery is open, over a graph, and every lane fires anyway.

    The literal scenario KOD-431 excludes, in one walk: every lane's pull
    request open and unmerged AND a dependency edge across them. Run one
    delivers both lanes, so the origin holds one open pull request per lane.
    Their criteria are then owed again the way an amendment owes one again,
    which is the board a scope under review carries: every lane is ready in
    turn, and every lane's own delivery is open. That the origin reports each
    of them open is read here through the shipped reader, so the premise is the
    forge's answer and not a guess about how a pull request names its lane.

    B stands on A, so B is a dependent whose OWN pull request is open behind a
    blocker whose pull request is open too. What releases it is A's criteria
    being Done — nothing waits for either delivery to land, and no stacked
    ordering of the two open pull requests is imposed anywhere. The walk fires
    both, excludes no candidate for a delivery of its own, and asks the origin
    what it already has not once. Each lane's existing pull request receives
    the next commits rather than a second one being opened (KOD-785).
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        first = resumable(
            port=port,
            repos=repos,
            lanes=("A", "B"),
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=[
                *one_check_echoes("A", rounds=4),
                *one_check_echoes("B", rounds=4),
            ],
        )
        opened = await bounded_walk(first, job="first-job", origin=FORGE_ORIGIN)

        assert lane_failures(opened) == ()
        assert len(wire.creates) == 2
        reader = probe_of(first, origin=FORGE_ORIGIN)
        for key in ("A", "B"):
            assert (await lane_record(port, key)).pr is not None
            assert await reader.open_delivery_exists(
                repo_url=FORGE_ORIGIN, issue_key=key
            )
            owed_again(port, lane=key)

        answered = len(wire.requests)
        second = resumable(
            port=port,
            repos=repos,
            lanes=("A", "B"),
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=[
                *one_check_echoes("A", rounds=4),
                *one_check_echoes("B", rounds=4),
            ],
        )
        events = await bounded_walk(second, job="second-job", origin=FORGE_ORIGIN)

        # Five ticks: A's fire, the tick after it that offers A for its delivery
        # alone and finds its pull request already recorded, then the same pair
        # for B once A's criterion is Done, and the tick with nothing left to
        # offer. The edge orders the two fires; it holds neither of them back.
        assert len(ticks_of(events)) == 5
        assert lane_failures(events) == ()
        assert ticks_of(events)[-1].dispatched == ("A", "B")
        assert ticks_of(events)[-1].rested_lanes == ("A", "B")
        assert ticks_of(events)[-1].unresolved_criteria == ()
        # Not one candidate was passed over for its own delivery, and the
        # listing that would have passed it over was never read. The one
        # exclusion the walk does carry is the edge itself: on tick one B
        # stands behind an A that owes its criterion again, which is a fact
        # about the graph and not about anybody's pull request.
        assert [
            (exclusion.issue_key, exclusion.clause.value, exclusion.detail)
            for tick in ticks_of(events)
            for exclusion in tick.exclusions
        ] == [("B", "live_blocker", "A")]
        assert [len(tick.exclusions) for tick in ticks_of(events)] == [1, 0, 0, 0, 0]
        assert not [
            exclusion
            for tick in ticks_of(events)
            for exclusion in tick.exclusions
            if exclusion.clause.value == "open_delivery"
        ]
        assert open_listings(wire, after=answered) == []
        # B stood on the branch A's own record names, and nothing merged
        # anything: the dependent whose own pull request is open is released by
        # its blocker's criteria being Done, not by that blocker's delivery
        # landing.
        assert (
            bases_of(events)["B"]
            == recorded_branches(record=await lane_record(port, "A")).deliverable_branch
        )
        assert [
            request for request in wire.requests if request.url.path.endswith("/merge")
        ] == []
        # The same two pull requests, each carrying the second run's work.
        assert len(wire.creates) == 2
        assert all(
            port.issues[f"{key}/check"].state_kind is WorkflowStateKind.COMPLETED
            for key in ("A", "B")
        )
    finally:
        await forge.close()


async def test_a_dependent_behind_a_blocker_with_an_open_pull_request_is_unlocked():
    """The whole graph walks while the blocker's delivery sits open (KOD-431).

    A fires, closes its criterion and opens its pull request; nobody merges it,
    and nothing about this walk could. B stands on A, so the deadlock this
    criterion exists to exclude is B held until A's delivery lands: the ready
    set has no landing and no merge precondition, and a blocker whose criteria
    are Done discharges its dependents whatever its pull request is doing.

    B is therefore fired in the same invocation, on the deliverable branch A's
    own record names — the branch A's delivery published, not the trunk and not
    a merge commit that does not exist — and opens its own pull request against
    it.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
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
            evaluations=[
                *one_check_echoes("A", rounds=4),
                *one_check_echoes("B", rounds=4),
            ],
        )
        events = await bounded_walk(harness, job="only-job", origin=FORGE_ORIGIN)

        assert lane_failures(events) == ()
        # Five ticks: A's fire, the delivery-only turn that rests A now that
        # its pull request is on its record, B's fire, B's own such turn, and
        # the tick with nothing left to offer.
        assert len(ticks_of(events)) == 5
        # The premise, read through the shipped reader and through the record:
        # the origin reports A's delivery OPEN, A's record carries it, and
        # nothing merged it — no request of this walk asked anything to.
        record = await lane_record(port, "A")
        assert record.pr is not None
        assert await probe_of(harness, origin=FORGE_ORIGIN).open_delivery_exists(
            repo_url=FORGE_ORIGIN, issue_key="A"
        )
        assert [
            request for request in wire.requests if request.url.path.endswith("/merge")
        ] == []
        # And B was fired anyway, on A's recorded deliverable branch.
        assert "B" in ticks_of(events)[-1].dispatched
        deliverable = recorded_branches(record=record).deliverable_branch
        assert bases_of(events)["B"] == deliverable
        assert bases_of(events)["B"] not in TRUNK_BRANCHES
        assert deliverable != record.branch
        assert len(wire.creates) == 2
        assert port.issues["B/check"].state_kind is WorkflowStateKind.COMPLETED
        # B's own delivery really completed at the head it published: its
        # checks were watched there, and the run reported them COMPLETED.
        opened_for_b = recorded_branches(
            record=await lane_record(port, "B")
        ).deliverable_branch
        assert wire.watches[-1] == (
            f"/repos/owner/repo/commits/{opened_for_b}/check-runs"
        )
    finally:
        await forge.close()


async def test_a_done_blocker_with_no_pull_request_unlocks_its_dependent():
    """The gate's one read stays the only question the walk asks (KOD-431).

    B stands on A, which is Done and recorded nothing, so base resolution
    assumes A's work reached the trunk — after the one read that settles it
    (KOD-721, KOD-777). B itself carries an open pull request from the run
    before, and that is no longer a reason to pass it over: the dependent is
    unlocked exactly as it was when the candidate probes stood in the way of
    nothing here. The origin is asked once per turn, about the blocker and
    never about the candidate; B fires on the trunk and its own pull request
    receives the commit.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    finish_by_hand(port, "A")
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        first = resumable(
            port=port,
            repos=repos,
            lanes=("A", "B"),
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=one_check_echoes("B", rounds=4),
        )
        opened = await bounded_walk(first, job="first-job", origin=FORGE_ORIGIN)

        assert lane_failures(opened) == ()
        assert len(wire.creates) == 1
        reader = probe_of(first, origin=FORGE_ORIGIN)
        assert await reader.open_delivery_exists(repo_url=FORGE_ORIGIN, issue_key="B")
        # And nothing is open for the blocker, which is the arm the gate takes.
        assert not await reader.open_delivery_exists(
            repo_url=FORGE_ORIGIN, issue_key="A"
        )
        owed_again(port, lane="B")

        answered = len(wire.requests)
        second = resumable(
            port=port,
            repos=repos,
            lanes=("A", "B"),
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=one_check_echoes("B", rounds=4),
        )
        with structlog.testing.capture_logs() as logs:
            events = await bounded_walk(second, job="second-job", origin=FORGE_ORIGIN)

        # Four ticks: A has no record to deliver from and rests, B fires, B is
        # offered for its delivery alone and its pull request is already on its
        # record, and the fourth finds nothing left to offer.
        assert len(ticks_of(events)) == 4
        assert lane_failures(events) == ()
        # B is admitted on the very first tick: its blocker is Done, and a Done
        # blocker discharges its dependents on the same tick it is read.
        assert ticks_of(events)[0].ready == ("B",)
        assert ticks_of(events)[-1].dispatched == ("B",)
        assert ticks_of(events)[-1].rested_lanes == ("A", "B")
        assert bases_of(events)["B"] == "main"
        # Stated by name, once per turn B took: the assumption the gate's own
        # read licensed, for the fire and again for the delivery-only turn the
        # tick after it offers.
        assert [
            event["lane"]
            for event in logs
            if event.get("event") == "base_input_no_open_delivery"
        ] == ["B", "B"]
        # And exactly as many listings as there were gate reads. B's own open
        # delivery — which the origin does report — was asked about never, and
        # firing B at all is what says so: a candidate read would have passed
        # it over on the very pull request its next commit belongs in.
        assert len(open_listings(wire, after=answered)) == 2
        assert len(wire.creates) == 1
        assert port.issues["B/check"].state_kind is WorkflowStateKind.COMPLETED
    finally:
        await forge.close()


# ---------------------------------------------------------------------------
# KOD-724 — a lane larger than one fire's budget is fired again in the same
# invocation, while its last fire closed a criterion the lane owed.
# ---------------------------------------------------------------------------

#: The gradings lane A's first fire asks for, observed rather than assumed.
#:
#: Stated because the echoes the SECOND fire is answered with begin after them:
#: a fire resumed on one open criterion is graded against that criterion alone,
#: and an echo naming the one its last fire already closed is not an answer to
#: the question this one asked.
FIRST_FIRE_GRADINGS = 2


def budget_bound_lane(repos, *, port):
    """Lane A, two checks and a budget of one iteration: two fires to converge.

    One iteration closes one of the two criteria and the budget ends there, so
    the lane is larger than one fire. The merger is the walk's own, which is
    unbounded: the default one supplies a single consolidation per lane and a
    second fire of the same lane would find none left.
    """
    return resumable(
        port=port,
        repos=repos,
        max_iterations=1,
        evaluations=[
            *(
                criteria_echo(keys=A_KEYS, passed={"A/check"})
                for _ in range(FIRST_FIRE_GRADINGS)
            ),
            *(criteria_echo(keys=("A/second",), passed={"A/second"}) for _ in range(4)),
        ],
    )


async def test_a_second_fire_of_a_pinned_lane_adds_no_record():
    """One invocation, two fires of one lane, and one record between them.

    The second fire re-enters the question step like a second process does: it
    reads the board, finds the identity its own earlier pass minted, and owes
    nothing. What proves it is the write journal — the record is written once
    and the second fire's pass writes nothing at all.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    harness = budget_bound_lane(repos, port=port)
    prefix = native_operation().marker_prefixes["ruling"]
    answers = {
        "rulings": [
            {
                "issueRef": "A/check",
                "question": "Does the Check's byte run include its marker?",
                "rulingClass": "pin_reading",
                "resolution": "The marker is not part of the Check's bytes.",
                "rejectedAlternative": "Reading the marker as Check text.",
                "repoEvidence": ["lane-0.py"],
            }
        ]
    }
    # Every pass answers the same question again, so what stops the second and
    # third writing is the identity its own earlier pass minted and nothing
    # else — a pass handed no answer would owe nothing whatever the board holds.
    harness.executor.question_answers = [answers] * 3

    events = await bounded_walk(harness, job="converging-job")

    assert ticks_of(events)[-1].dispatched == ("A", "A")
    assert lane_failures(events) == ()
    # Three passes through the step — two fires and one remediation round —
    # one record, and one write of it. The count is the observed one.
    assert len(harness.executor.question_prompts) == 3
    records = [c for c in port.comments if c.body.startswith(f"[{prefix}")]
    assert len(records) == 1
    assert records[0].issue_key == "A/check"
    assert [
        write for write in port.comment_writes if write[1].startswith(f"[{prefix}")
    ] == [(records[0].comment_key, records[0].body)]
    # One judged write across the three passes: a re-write of identical text
    # leaves that journal alone, a second judgement does not.
    assert len(harness.executor.judge_sessions) == 1
    # And every pass after the first was shown what the first one pinned.
    assert all(
        "The marker is not part of the Check's bytes." in prompt
        for prompt in harness.executor.question_prompts[1:]
    )


async def test_a_lane_larger_than_one_fires_budget_converges_across_fires():
    """Two criteria, a one-iteration budget, and one invocation (KOD-724).

    The first fire closes one criterion and its budget ends; the lane still owes
    the other, and having been fired is no longer what stops it being offered.
    The tick after reads which of the criteria the lane owed the subtree now
    carries as closed, finds one of them there, and offers the lane again. The
    second fire closes the rest, so the scope converges inside this invocation
    instead of owing the remainder to the next one.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    # Somebody moved A's own issue while it was being worked, so a put-back
    # would be observable: a restore onto the state an issue is already in
    # writes nothing, and on a board holding A where the put-back would put it
    # the write and its absence read the same.
    port.issues["A"] = port.issues["A"].model_copy(
        update={"state_name": "In Progress", "state_kind": WorkflowStateKind.STARTED}
    )
    harness = budget_bound_lane(repos, port=port)
    events = await bounded_walk(harness, job="converging-job")

    # Three ticks: the two fires, and the one with nothing left to offer. The
    # lane leaves the ready set when its gap empties, so nothing rests here and
    # the walk ends because there is no candidate rather than because it gave up.
    assert len(ticks_of(events)) == 3
    assert lane_failures(events) == ()
    assert [tick.ready for tick in ticks_of(events)] == [("A",), ("A",), ()]
    assert ticks_of(events)[-1].dispatched == ("A", "A")
    assert ticks_of(events)[-1].rested_lanes == ()
    assert ticks_of(events)[-1].unresolved_criteria == ()
    assert all(
        port.issues[key].state_kind is WorkflowStateKind.COMPLETED for key in A_KEYS
    )
    # One criterion per fire, in the order the fires closed them: a walk that
    # closed both in one fire would converge for another reason entirely.
    assert port.workflow_writes == [
        ("A/check", LifecycleStage.DONE),
        ("A/second", LifecycleStage.DONE),
    ]
    # And a lane that is making progress is never put back: each of these fires
    # closed a criterion its lane owed, so neither of them ended the lane's turn
    # and the issue stayed where it was found (KOD-460).
    assert port.restored_states == []
    assert port.issues["A"].state_name == "In Progress"


@dataclass(frozen=True)
class TickMark:
    """What one tick's observation found, before that tick acted on its selection.

    The counts say where in each list the fire that tick launches begins; the
    record is what that fire will read its branches out of. Both are read at the
    observation and neither afterwards: the walk yields a tick's observation
    before the tick fires anything, so a fact read here is a fact about what the
    fire ENTERS ON, while the same fact read at the end of the walk is the fire's
    own output and comparing a fire with it says nothing (KOD-723).
    """

    acquisitions: int
    prompts: int
    lane_mints: int
    loop_names: int
    record: LaneRunState | None


async def walk_marking(harness, *, mark, **rest):
    """Every event of one bounded walk, with *mark* awaited at each observation.

    A lane fired twice in one invocation leaves two fires' workspace
    acquisitions, prompts and mints in one list each, and which entries belong
    to the fire a given tick launched is the whole question a resumed fire is
    read by. A tick's observation is yielded before that tick acts on its
    selection, so everything after tick n's mark is tick n's fire and no other.

    The mark is AWAITED, because some of what a tick found has to be read from
    the board rather than counted in the process: a lane's record above all.
    """
    marks: list[TickMark] = []
    events = []
    async with asyncio.timeout(WALK_BOUND_SECONDS):
        async for event in drive(harness, **rest):
            if isinstance(event, ScopeWalkEvent):
                marks.append(await mark())
            events.append(event)
    return events, marks


def lane_events_after(events, *, tick: int):
    """Every event of one tick's turn: after its observation, before the next.

    A fire's own events sit between two observations, so which fire a delivery
    or an iteration belongs to is read off that window rather than off a fixed
    index into the whole walk.
    """
    observed = [
        index for index, event in enumerate(events) if isinstance(event, ScopeWalkEvent)
    ]
    start = observed[tick - 1]
    end = observed[tick] if tick < len(observed) else len(events)
    return events[start + 1 : end]


async def test_a_budget_exhausted_lane_resumes_on_its_recorded_branch(monkeypatch):
    """The second fire of one invocation enters the way a second process does.

    Nothing about a lane is remembered between its fires: the record and the
    remote head of the branch it names are read again before each of them, so
    the fire that follows an exhausted budget takes exactly the path a fresh
    process takes. It checks the recorded loop branch out without cutting it,
    mints no name of either kind beside it, and is graded against the criterion
    its last fire left open rather than the one that fire closed (KOD-723).

    The branch it entered on is compared with the record read at the SECOND
    TICK'S OBSERVATION, before that fire acts. Compared with the record read at
    the end of the walk the statement would be self-fulfilling: the second fire
    writes the record, so whatever branch it ran on is the branch the record
    names by then — and this lane really does have two loop branches to tell
    apart, because the first fire's remediation round drew a second one and
    only the later is recorded.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    minted = mint_spy(monkeypatch)
    loop_names = loop_name_spy(monkeypatch)
    harness = budget_bound_lane(repos, port=port)

    async def mark() -> TickMark:
        return TickMark(
            acquisitions=len(harness.workspace.acquisitions),
            prompts=len(harness.executor.execution_prompts),
            lane_mints=len(minted),
            loop_names=len(loop_names),
            record=await recorded_so_far(port, "A"),
        )

    events, marks = await walk_marking(harness, job="converging-job", mark=mark)

    # The same three ticks and the same two fires the convergence case drives,
    # asserted again here because everything below is about the second of them.
    assert len(ticks_of(events)) == 3
    assert lane_failures(events) == ()
    assert ticks_of(events)[-1].dispatched == ("A", "A")
    assert ticks_of(events)[-1].ready == ()
    entered = marks[1]
    assert entered.record is not None

    # The premise of the whole case, off the first fire's own terminal event:
    # that fire ended because its iteration budget ran out with the roster
    # unmet, not because it finished or failed.
    assert [
        event.event.delivery.outcome
        for event in lane_events_after(events, tick=1)
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, LaneDeliveryEvent)
    ] == [WorkflowOutcome.remediation_budget_exhausted]

    # Two names were drawn in the whole invocation and the FIRST fire drew
    # both: the lane's own pair, and the fresh loop name its remediation round
    # takes. The second fire's slice of either spy is empty — the counts have
    # not moved since the mark it started from. The spies record rather than
    # raise, so the statement is these counts beside a walk that otherwise ran:
    # an uncalled spy would say the same about both fires.
    assert minted == ["A"]
    assert len(loop_names) == 2
    assert (entered.lane_mints, entered.loop_names) == (len(minted), len(loop_names))
    # The second fire checked the branch THE RECORD NAMED AT ITS ENTRY out, and
    # did not cut it: a fire that minted a second branch beside a recorded one
    # would lose the work the record names (KOD-684). Its acquisition is the
    # first one at or after its entry that NAMES a branch: the question step
    # opens a detached tree of its own before the loop.
    opened = opened_branch(harness.workspace.acquisitions, after=entered.acquisitions)
    assert opened["branch_name"] == opened["ref"] == entered.record.branch
    assert opened["create_branch"] is False
    # And it left the lane on that same branch rather than moving it elsewhere.
    assert (await lane_record(port, "A")).branch == entered.record.branch
    # And it implemented the criterion still owed, not the one already
    # satisfied: the roster is read from the board at entry, so a satisfied
    # criterion is not in it and no session is spent on it again.
    prompt = harness.executor.execution_prompts[entered.prompts]
    assert "A/second live Check  bytes" in prompt
    assert "A live Check  bytes" not in prompt


#: A lane of three criteria, and the keys of its roster.
THREE_CHECKS = {"A": ("check", "second", "third")}
A_THREE_KEYS = ("A/check", "A/second", "A/third")

#: The gradings each of the first two fires of that lane asks for, observed
#: rather than assumed: each closes one criterion, is graded red on what is
#: left, takes a remediation round for it and is graded once more, and the
#: one-iteration budget ends there.
#:
#: Exact and not generous, unlike the pool the converging fire is answered
#: from: an echo left over from one fire is the echo the NEXT fire's grading is
#: answered with, and it answers a question that fire never asked.
STALLING_FIRE_GRADINGS = 2


def three_criterion_lane(repos, *, port):
    """Lane A, three checks and a budget of one iteration: three fires.

    Each of the first two fires closes one criterion and then stalls on what is
    left, so each takes a remediation round — and a remediation round draws a
    FRESH loop branch and records it. That is what gives the three fires three
    different records to enter on, and it is the whole premise of the third
    fire: a reader that remembered the first record it managed to read would
    enter the third fire on the branch the first fire left, and the work the
    second fire committed would be lost.
    """
    return resumable(
        port=port,
        repos=repos,
        max_iterations=1,
        evaluations=[
            *(
                criteria_echo(keys=A_THREE_KEYS, passed={"A/check"})
                for _ in range(STALLING_FIRE_GRADINGS)
            ),
            *(
                criteria_echo(keys=("A/second", "A/third"), passed={"A/second"})
                for _ in range(STALLING_FIRE_GRADINGS)
            ),
            # The converging fire's pool, generous because how many gradings a
            # passing fire asks for is the graph's business: every one of them
            # answers the same way, and a leftover here is consumed by nobody.
            *(criteria_echo(keys=("A/third",), passed={"A/third"}) for _ in range(4)),
        ],
    )


async def test_a_third_fire_enters_on_the_record_the_tick_before_it_read(monkeypatch):
    """The record is read again before the THIRD fire, not remembered from before.

    Two fires cannot tell a reader that reads every time from one that remembers
    what it read: the first tick finds no record at all, so the second fire's
    reading is the first successful one either way. The third fire is where the
    two part. This lane takes three, each on a loop branch the fire before it
    recorded, so the branch the third fire enters on says which reading it stood
    on: the record as the tick that offered it held it, or a record two fires
    old.

    Compared against the record read at the THIRD TICK'S OBSERVATION, which the
    walk yields before that tick acts, and not against the record at the end of
    the walk — the third fire writes that one, so comparing a fire with its own
    output would state nothing (KOD-723).
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=THREE_CHECKS)
    minted = mint_spy(monkeypatch)
    loop_names = loop_name_spy(monkeypatch)
    harness = three_criterion_lane(repos, port=port)

    async def mark() -> TickMark:
        return TickMark(
            acquisitions=len(harness.workspace.acquisitions),
            prompts=len(harness.executor.execution_prompts),
            lane_mints=len(minted),
            loop_names=len(loop_names),
            record=await recorded_so_far(port, "A"),
        )

    events, marks = await walk_marking(harness, job="three-fire-job", mark=mark)

    # Four ticks and three fires: one per criterion, and the tick that finds the
    # gap empty and nothing left to offer.
    assert len(ticks_of(events)) == 4
    assert lane_failures(events) == ()
    assert ticks_of(events)[-1].dispatched == ("A", "A", "A")
    assert [tick.ready for tick in ticks_of(events)] == [("A",), ("A",), ("A",), ()]
    assert all(
        port.issues[key].state_kind is WorkflowStateKind.COMPLETED
        for key in A_THREE_KEYS
    )
    # One criterion per fire, in the order the fires closed them.
    assert port.workflow_writes == [
        ("A/check", LifecycleStage.DONE),
        ("A/second", LifecycleStage.DONE),
        ("A/third", LifecycleStage.DONE),
    ]
    entered = marks[2]
    assert entered.record is not None
    # The premise the third fire is read by: the record MOVED between the second
    # tick and the third, so a reading taken at the second tick and kept would
    # name a branch this fire has no business entering on.
    assert marks[1] is not None and marks[1].record is not None
    assert marks[1].record.branch != entered.record.branch
    # The lane's pair of names was drawn once, and each stalling fire's
    # remediation round drew a loop name beside it: three draws, no fire of them
    # minting a pair of its own.
    assert minted == ["A"]
    assert len(loop_names) == 3
    # And the third fire checked out the branch THE RECORD NAMED AT ITS ENTRY,
    # without cutting it, and left the lane there. Its acquisition is the first
    # one at or after its entry that NAMES a branch: the question step opens a
    # detached tree of its own before the loop.
    opened = opened_branch(harness.workspace.acquisitions, after=entered.acquisitions)
    assert opened["branch_name"] == opened["ref"] == entered.record.branch
    assert opened["create_branch"] is False
    assert (await lane_record(port, "A")).branch == entered.record.branch


# ---------------------------------------------------------------------------
# KOD-460 — a fire that closed none of the criteria its lane owed ends that
# lane's turn: the issue goes back where its open work stands, the lane rests,
# and the walk spends the rest of the invocation on the other lanes.
# ---------------------------------------------------------------------------

#: The gradings a fire that closes nothing asks for, observed rather than
#: assumed: the first grading is red, so the fire takes a remediation round and
#: is graded again, and the one-iteration budget ends there.
STALLED_FIRE_GRADINGS = 2

#: The same, for a stalling fire whose budget is TWO iterations: observed as
#: four, each of them red.
#:
#: Two iterations is what makes the peak and the tip differ. A loop allowed one
#: iteration commits once, so the best commit it produced is also the branch's
#: tip and no assertion over a published SHA could tell the two selections
#: apart; a loop allowed two commits twice, and the stall exit publishes the
#: earlier of them.
STALLED_TWO_ITERATION_GRADINGS = 4


async def test_a_fire_that_closes_nothing_puts_the_issue_back_and_the_walk_goes_on():
    """Lane A closes nothing, gives up its turn, and lane B still runs (KOD-460).

    Nothing A's fire produced satisfied the criterion A owed, so the tick after
    it reads that none of the identities A owed is closed and stops firing A:
    an identical second fire would say what the first said. What that leaves on
    the board is the pull request A's own stall exit opened, on A's record
    (KOD-776), and A's issue back in the state its open criterion is in — a
    state name read off that criterion on this tick, so the walk needs no
    configured vocabulary of its own to put an issue back.

    The invocation is not over: B is selected on the very next tick and closes
    its own criterion, which is the difference between one lane giving up and a
    walk giving up.

    A's budget is two iterations so that the pull request's head says which
    commit the stall exit chose: the loop commits twice, the best of the two is
    not the branch's tip, and the delivered head therefore tells the best
    iteration from the latest one by content.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A", "B"))
    # Somebody moved A's own issue while it was being worked. The put-back is
    # observable only against a board that holds the issue somewhere else: a
    # restore onto the state an issue is already in writes nothing at all.
    port.issues["A"] = port.issues["A"].model_copy(
        update={"state_name": "In Progress", "state_kind": WorkflowStateKind.STARTED}
    )
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        publisher = WalkRefPublisher(repos)
        merger = WalkMerger(repos)
        harness = resumable(
            port=port,
            repos=repos,
            lanes=("A", "B"),
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            merger=merger,
            ref_publisher=publisher,
            # Two iterations, so A's loop branch holds two commits and the best
            # of them is not the branch's tip. That is what lets the delivered
            # head below say WHICH commit the stall exit selected instead of
            # only which branch name it consolidated from.
            max_iterations=2,
            evaluations=[
                *(
                    criteria_echo(keys=("A/check",), passed=set())
                    for _ in range(STALLED_TWO_ITERATION_GRADINGS)
                ),
                *one_check_echoes("B", rounds=2),
            ],
        )
        with structlog.testing.capture_logs() as logs:
            events = await bounded_walk(harness, job="only-job", origin=FORGE_ORIGIN)

        # Four ticks: A's fire, the tick that reads it closed nothing and hands
        # the turn to B, the tick that offers B for its delivery alone and finds
        # its pull request already recorded, and the tick with nothing to offer.
        assert len(ticks_of(events)) == 4
        assert lane_failures(events) == ()
        assert ticks_of(events)[-1].dispatched == ("A", "B")
        assert list(ticks_of(events)[-1].dispatched).count("A") == 1
        # A rests from the tick that read its fire, and every later tick still
        # offers it nothing; B rests only once its delivery is on its record.
        assert [tick.rested_lanes for tick in ticks_of(events)] == [
            (),
            ("A",),
            ("A",),
            ("A", "B"),
        ]
        assert ticks_of(events)[-1].ready == ("A",)
        assert [
            event["lane"]
            for event in logs
            if event.get("event") == "scope_lane_plateaued"
        ] == ["A"]
        # The put-back, named exactly: one write, on the lane's own issue, to
        # the state name its open criterion carries. Nothing put a criterion
        # back and no lifecycle stage was written for A at all.
        assert port.restored_states == [("A", "Todo")]
        assert port.issues["A"].state_name == "Todo"
        assert port.issues["A/check"].state_kind is WorkflowStateKind.UNSTARTED
        assert port.workflow_writes == [("B/check", LifecycleStage.DONE)]
        # And the stall exit's pull request is on A's record, from A's own head.
        record = await lane_record(port, "A")
        assert record.pr is not None
        deliverable = recorded_branches(record=record).deliverable_branch
        assert [create["head"] for create in wire.creates] == [
            deliverable,
            recorded_branches(record=await lane_record(port, "B")).deliverable_branch,
        ]
        assert record.pr.number == wire._numbers[deliverable]
        # "From the best iteration" by CONTENT and not by branch name. The
        # stall exit published the best iteration's commit at the lane's own
        # best-iteration ref; the consolidation that produced the delivered
        # branch took THAT ref as its source and no other; and the head the
        # forge reports for the pull request stands at that same commit. A
        # stall exit that consolidated its loop branch instead would open a
        # pull request from a head this walk's repositories hold under another
        # branch entirely.
        landed = [
            call["commit_sha"]
            for call in publisher.calls
            if call["ref"].endswith("-best")
        ]
        assert [call["ref"] for call in publisher.calls] == [
            best_iteration_ref(deliverable)
        ]
        # Not vacuous: what was published is a commit this lane's loop made,
        # not the trunk the lane was cut from.
        assert landed[0] != TRUNK_SHA
        assert landed[0] in repos.branches[record.branch].shas
        # And it is the BEST commit rather than the latest one: the loop made
        # two, and what was published is the first. A selection that took the
        # loop branch as it stands would publish the other, so the head this
        # delivery was opened at discriminates the two by content and not by
        # the name the consolidation was asked for.
        assert (
            landed[0]
            == repos.branches[record.branch].shas[0]
            != repos.branches[record.branch].shas[-1]
        )
        # The head first, because the head is the content: a delivery opened
        # from a branch standing anywhere else carries other work whatever the
        # consolidation was asked for.
        assert repos.head_of(deliverable) == landed[0]
        assert [
            call["source_branch"]
            for call in merger.calls
            if call["feature_branch"] == deliverable
        ] == [best_iteration_ref(deliverable)]
        # The walk went on: B closed its own criterion in this same invocation.
        assert port.issues["B/check"].state_kind is WorkflowStateKind.COMPLETED

        # And "not offered again" is about THIS invocation. A later one enters
        # from the record and fires A once more — the issue is back where its
        # open work stands and the record names the branch that work is on — so
        # what the plateau ends is the lane's turn and not the lane.
        later = resumable(
            port=port,
            repos=repos,
            lanes=("A", "B"),
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=one_check_echoes("A", rounds=4),
        )
        again = await bounded_walk(later, job="later-job", origin=FORGE_ORIGIN)

        assert lane_failures(again) == ()
        assert ticks_of(again)[-1].dispatched == ("A",)
        assert port.issues["A/check"].state_kind is WorkflowStateKind.COMPLETED
        # The pull request the stall exit opened is the one that receives this
        # fire's work: nothing opened a second one for the lane.
        assert len(wire.creates) == 2
    finally:
        await forge.close()


async def test_a_put_back_that_cannot_be_written_rests_that_lane_and_the_walk_goes_on(
    monkeypatch,
):
    """The state write A's plateau asks for fails, and only A pays for it.

    The put-back is a write about one issue, made inside that lane's own
    boundary: a tracker that will not take it says nothing about the scope, so
    A rests, A's failure is on the observation, and B is still fired in the same
    invocation. Outside a boundary the same failure would end the run with the
    other lanes untouched.

    A is named once among the resting lanes: the boundary rests it where the
    write failed, and the plateau arm does not rest it a second time.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A", "B"))
    port.issues["A"] = port.issues["A"].model_copy(
        update={"state_name": "In Progress", "state_kind": WorkflowStateKind.STARTED}
    )
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
            evaluations=[
                *(
                    criteria_echo(keys=("A/check",), passed=set())
                    for _ in range(STALLED_FIRE_GRADINGS)
                ),
                *one_check_echoes("B", rounds=2),
            ],
        )

        async def unavailable(*, issue_key, state_name):
            raise TrackerUnavailableError(
                f"the state write for {issue_key} did not reach the board"
            )

        monkeypatch.setattr(port, "restore_workflow_state", unavailable)
        events = await bounded_walk(harness, job="only-job", origin=FORGE_ORIGIN)

        # The same four ticks the walk takes when the write lands: the failure
        # ends A's turn where the plateau already had.
        assert len(ticks_of(events)) == 4
        assert [tick.rested_lanes for tick in ticks_of(events)] == [
            (),
            ("A",),
            ("A",),
            ("A", "B"),
        ]
        failures = lane_failures(events)
        assert [failure.issue_key for failure in failures] == ["A"]
        assert failures[0].error.error_kind == "TrackerUnavailableError"
        assert (
            "the state write for A did not reach the board" in failures[0].error.error
        )
        # Nothing was written, and A's issue is where the fire found it.
        assert port.restored_states == []
        assert port.issues["A"].state_name == "In Progress"
        # The walk went on regardless: B was fired and closed its own criterion.
        assert ticks_of(events)[-1].dispatched == ("A", "B")
        assert port.issues["B/check"].state_kind is WorkflowStateKind.COMPLETED
    finally:
        await forge.close()


async def test_a_closed_lane_whose_record_cannot_be_read_is_not_offered_again(
    monkeypatch,
):
    """A lane whose own turn raised is rested, and resting is what stops it.

    Being fired no longer keeps a lane from being selected again (KOD-724), so
    the boundary's own rest carries the whole guarantee for a lane that failed
    before it ever launched: this one is a finished lane offered for its
    delivery alone whose record listing fails, and the tick after it has
    nothing left to offer. Were it offered again, the walk would spend the
    invocation on the same unreadable listing, which the bound here catches as
    a failure rather than a hang. The lane's refusal is stated in the log under
    its own name as well as on the observation.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A",))
    finish_by_hand(port, "A")
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        harness = resumable(
            port=port,
            repos=repos,
            lanes=("A",),
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
        )
        listing = port.list_comments

        async def unreadable(*, issue_key):
            if issue_key == "A":
                raise TrackerUnavailableError("the lane record listing failed")
            return await listing(issue_key=issue_key)

        monkeypatch.setattr(port, "list_comments", unreadable)
        with structlog.testing.capture_logs() as logs:
            events = await bounded_walk(harness, job="only-job", origin=FORGE_ORIGIN)

        # Two ticks: the turn that failed, and the one with nothing to offer.
        assert len(ticks_of(events)) == 2
        assert ticks_of(events)[-1].rested_lanes == ("A",)
        assert ticks_of(events)[-1].dispatched == ()
        failures = lane_failures(events)
        assert [failure.issue_key for failure in failures] == ["A"]
        assert failures[0].error.error_kind == "LaneRecordReadError"
        assert [
            event["lane"] for event in logs if event.get("event") == "scope_lane_failed"
        ] == ["A"]
        # Nothing was delivered for it either: the turn ended before a session.
        assert wire.creates == []
    finally:
        await forge.close()


# ---------------------------------------------------------------------------
# KOD-832 clauses 5 and 7 — a process killed mid-fire re-enters from the
# tracker alone: the lane that finished is not re-run, the lane killed in
# flight resumes on its recorded branch, and the lane that was blocked stands
# on the branch its blocker's record names.
# ---------------------------------------------------------------------------

#: The lane priorities that fix which lane each tick of the acceptance walk
#: offers first, and with them the order A, C, B.
#:
#: A goes first because it is the blocker B has to stand on, and its own
#: priority is what puts it there: effective priority flows from a blocked lane
#: back to its blocker, and B's is the lowest there is. C goes before B because
#: its own priority is the higher of the two, which is what makes the lane the
#: kill catches in flight the one with two criteria rather than the last lane
#: offered. Without them the order would be whatever the rows were built in,
#: since every issue of this board is created at the same instant.
ACCEPTANCE_PRIORITIES = {"A": IssuePriority.URGENT, "C": IssuePriority.HIGH}


async def test_a_killed_scope_run_re_enters_from_the_tracker_alone(monkeypatch):
    """Two processes over one board, one remote and one origin (KOD-832).

    Run one finishes and delivers A, then dies inside C's fire with C's first
    criterion crossed off and its second still owed. Nothing of that process
    survives but what it wrote down: the board, the repositories and the
    origin. Run two is built fresh over those three, and from them alone it
    reads what is left to do — A is not worked again, C resumes on the branch
    its record names and is graded only against the criterion it still owes,
    and B, whose blocker closed in the run before, is fired against the
    deliverable branch A's record names. No graph state was persisted by either
    process, so nothing was replayed to reach any of it.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(
        lanes=THREE_LANES,
        blocked={"B": ("A",)},
        checks={"C": ("check", "second")},
        priorities=ACCEPTANCE_PRIORITIES,
    )
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    # The wire raises on every capability it does not hold, a merge included.
    # A raise inside a lane's turn is contained and reported, and a raise
    # outside one is contained with a visibility nobody chose, so neither would
    # be read off the walk as such: the refusals are collected here instead.
    refused: list[str] = []

    def answering(request):
        try:
            return wire(request)
        except AssertionError:
            refused.append(f"{request.method} {request.url}")
            raise

    forge = _make_client(answering)
    try:
        first = resumable(
            port=port,
            repos=repos,
            lanes=THREE_LANES,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            max_iterations=2,
            evaluations=[
                *one_check_echoes("A"),
                *(criteria_echo(keys=C_KEYS, passed={"C/check"}) for _ in range(4)),
            ],
        )
        reached = False
        stream = drive(first, job="first-job", origin=FORGE_ORIGIN)
        async with asyncio.timeout(WALK_BOUND_SECONDS):
            async for event in stream:
                if (
                    isinstance(event, ScopeLaneEvent)
                    and event.lane_key == "C"
                    and isinstance(event.event, WorkflowIterationEvent)
                ):
                    reached = True
                    break
        # Where a process dies. Generator close is a ``BaseException`` the lane
        # boundary does not contain, so run one really ends inside C's fire and
        # what run two enters on is what the board and the remote already hold.
        await stream.aclose()

        # What the kill left, stated before run two is built: otherwise every
        # assertion below could be about a first run that never got that far.
        assert reached, "run one ended before lane C was in flight"
        assert port.issues["A/check"].state_kind is WorkflowStateKind.COMPLETED
        assert port.issues["C/check"].state_kind is WorkflowStateKind.COMPLETED
        assert port.issues["C/second"].state_kind is WorkflowStateKind.UNSTARTED
        assert port.issues["B/check"].state_kind is WorkflowStateKind.UNSTARTED
        killed = await lane_record(port, "C")
        assert killed.pr is None
        finished = recorded_branches(
            record=await lane_record(port, "A")
        ).deliverable_branch
        assert [create["head"] for create in wire.creates] == [finished]

        minted = mint_spy(monkeypatch)
        second = resumable(
            port=port,
            repos=repos,
            lanes=THREE_LANES,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            max_iterations=2,
            evaluations=[
                *(
                    criteria_echo(keys=("C/second",), passed={"C/second"})
                    for _ in range(2)
                ),
                *one_check_echoes("B"),
            ],
        )
        events = await bounded_walk(second, job="second-job", origin=FORGE_ORIGIN)

        # Six ticks, bounded and observed: A offered for a delivery its record
        # already carries and rested, C fired, C offered for the delivery that
        # fire earned and rested, B fired, B offered and rested, and the sixth
        # with nothing left to offer.
        assert len(ticks_of(events)) == 6
        assert lane_failures(events) == ()
        assert [tick.ready for tick in ticks_of(events)] == [
            ("C", "B"),
            ("C", "B"),
            ("B",),
            ("B",),
            (),
            (),
        ]
        assert ticks_of(events)[-1].dispatched == ("C", "B")
        assert ticks_of(events)[-1].rested_lanes == ("A", "C", "B")
        assert ticks_of(events)[-1].unresolved_criteria == ()

        # A is not worked again: no iteration, no session naming its subject,
        # and one pull request for it across both processes.
        assert [
            event.lane_key
            for event in events
            if isinstance(event, ScopeLaneEvent)
            and isinstance(event.event, WorkflowIterationEvent)
        ] == ["C", "B"]
        assert not [
            prompt
            for prompt in second.executor.execution_prompts
            if "Exact native subject A" in prompt
        ]
        assert [create["head"] for create in wire.creates].count(finished) == 1

        # C resumes: the recorded loop branch checked out and not cut, the
        # criterion its last fire closed absent from the roster, and one record
        # on the same branch naming both processes. The loop's tree is the
        # first acquisition that NAMES a branch, since the question step opens
        # a detached one of its own before it.
        opened = opened_branch(second.workspace.acquisitions)
        assert opened["branch_name"] == opened["ref"] == killed.branch
        assert opened["create_branch"] is False
        assert "C/second live Check  bytes" in second.executor.execution_prompts[0]
        assert "C live Check  bytes" not in second.executor.execution_prompts[0]
        resumed = await lane_record(port, "C")
        assert resumed.branch == killed.branch
        assert {item.run_id for item in resumed.associations} == {
            "first-job",
            "second-job",
        }

        # B is fired on the strength of a closed blocker and nothing else: this
        # test edits no issue between the runs, so what unlocked B is A's own
        # closure in run one, and what B stands on is the branch A's record
        # names rather than the trunk.
        assert bases_of(events)["B"] == finished
        assert finished not in TRUNK_BRANCHES
        records = {key: await lane_record(port, key) for key in THREE_LANES}
        delivered = {
            key: recorded_branches(record=record).deliverable_branch
            for key, record in records.items()
        }
        stacked = next(
            create for create in wire.creates if create["head"] == delivered["B"]
        )
        assert stacked["base"] == finished
        # One name was minted in this process, B's: the two lanes with records
        # entered from them.
        assert minted == ["B"]

        # Every lane ends with a pull request on its record, the one the origin
        # holds for that lane's own delivered head, and its checks were observed
        # at that same head.
        assert [key for key, record in records.items() if record.pr is None] == []
        assert {
            key: record.pr.number for key, record in records.items() if record.pr
        } == {key: wire._numbers[head] for key, head in delivered.items()}
        assert sorted(wire.watches) == sorted(
            f"/repos/owner/repo/commits/{head}/check-runs"
            for head in delivered.values()
        )

        # Nothing was merged by either process, and nothing could have been:
        # the wire was asked for no capability it does not hold, and the port a
        # lane opens pull requests through carries no merge to call.
        assert refused == []
        assert {name for name in vars(PRCreator) if not name.startswith("_")} == {
            "create_pr",
            "comment_on_pr",
        }

        # And none of it was replayed: the scope path persists no graph state,
        # so every graph a lane of this origin runs on holds no checkpointer.
        lane = second.engine._scoped_arm._lane_for(FORGE_ORIGIN)
        assert lane.graph.checkpointer is None
        assert lane.fire.native_graph.checkpointer is None
        assert lane.fire.implementation._quality_gate._checkpointer is None
    finally:
        await forge.close()
