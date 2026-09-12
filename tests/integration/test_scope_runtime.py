"""Real request composition, controller and native graphs with external doubles."""

import asyncio
import json
from dataclasses import dataclass

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.chains.criteria import TrackerCriteria
from kodezart.composition.engine import build_workflow_engine
from kodezart.composition.jobs import build_job_queue
from kodezart.core.checkpointer import make_checkpointer
from kodezart.core.config import AppConfig
from kodezart.core.errors import TrackerUnavailableError
from kodezart.core.job_queue_settings import JobQueueSettings
from kodezart.domain.errors import (
    FireSpecEntryError,
    ScopePlanRefusalError,
    ScopeReadError,
)
from kodezart.handlers.agent_handler import AgentHandler
from kodezart.services.agent_service import AgentService
from kodezart.services.scope_runtime import _lane_checkpoint_key
from kodezart.types.domain.agent import (
    NodeSessionStartedEvent,
    ResultEvent,
    SystemEvent,
    WorkflowCompleteEvent,
    WorkflowIterationEvent,
)
from kodezart.types.domain.branch import WorkRef, WorkRefRole, trunk_base
from kodezart.types.domain.consolidation import (
    ConsolidationOutcome,
    ConsolidationStatus,
)
from kodezart.types.domain.job import JobState
from kodezart.types.domain.native_delivery import LaneDeliveryEvent
from kodezart.types.domain.operation import RepoEntry, ScopeLabel
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeLaneEvent, ScopeWalkEvent
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.ticket_review import TicketReviewMode
from kodezart.types.domain.tracker import WorkflowStateKind
from kodezart.types.requests.agent import WorkflowRequest
from tests.api.v1.test_jobs import _build_app
from tests.chains.test_native_fire import NativeExecutor, native_evaluation
from tests.fakes import (
    FIXTURE_EPOCH,
    SUPPRESS_ALL_SKILLS,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeChangePersister,
    FakeGitService,
    FakeRefPublisher,
    FakeRepoCache,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_prompt_provider,
    make_tracker_issue,
)

ORIGIN = "file:///scope-repository.git"
SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="scoped-project")
STAGED = "criteria-staged"


def board(*, lanes=("A",), blocked=None, approved=True):
    rows = []
    for key in lanes:
        rows.extend(
            [
                make_tracker_issue(
                    key,
                    body=f"Exact native subject {key}  with spaces\n",
                    blocked_by=(blocked or {}).get(key, ()),
                    issue_labels=frozenset({STAGED}),
                ),
                make_tracker_issue(
                    f"{key}/check",
                    parent_key=key,
                    issue_labels=frozenset({"criterion"}),
                    body=f"**Check:** {key} live Check  bytes\n**Evidence:** —",
                ),
            ]
        )
    return FakeTrackerPort(
        issues=rows,
        scope_memberships={SCOPE: tuple(lanes)},
        criteria_stage_label_key=STAGED,
        scope_label_members={
            ScopeRef(kind=ScopeKind.ISSUE, key=key): frozenset({ScopeLabel.APPROVED})
            for key in lanes
            if approved
        },
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
    engine: object
    port: FakeTrackerPort
    executor: NativeExecutor
    service: AgentService
    artifacts: FakeArtifactPersister
    saver: InMemorySaver


def runtime(
    *,
    port=None,
    lanes=("A",),
    saver=None,
    evaluations=None,
    trunk="trunk",
    origin=ORIGIN,
    forge=None,
):
    port = port or board(lanes=lanes)
    executor = ObservedNativeExecutor(
        evaluations
        or [
            native_evaluation(checks={f"{key}/check": f"{key} live Check  bytes"})
            for key in lanes
            for _ in range(2)
        ]
    )
    workspace = FakeWorkspaceProvider()
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor,
        workspace=workspace,
        persister=FakeChangePersister(),
    )
    artifacts = FakeArtifactPersister()
    saver = saver or InMemorySaver()
    engine = build_workflow_engine(
        config=AppConfig(
            ticket_review_mode=TicketReviewMode.REVIEWED,
            max_iterations=1,
            retry_max_attempts=1,
            retry_initial_interval=0.1,
        ),
        repositories=(RepoEntry(url=origin, trunk=trunk),),
        agent_service=service,
        git=RemoteGit(),
        cache=FakeRepoCache(),
        workspace=workspace,
        merger=FakeBranchMerger(
            consolidation_outcomes=[
                ConsolidationOutcome(
                    status=ConsolidationStatus.FAST_FORWARDED, feature_tip_sha="a" * 40
                )
                for _ in lanes
            ],
        ),
        artifact_persister=artifacts,
        ref_publisher=FakeRefPublisher(),
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        github_api=forge,
        checkpointer=saver,
        criteria=TrackerCriteria(tracker=port),
        scope_tracker=port,
    )
    return Harness(engine, port, executor, service, artifacts, saver)


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


async def test_request_queue_constructor_reaches_real_native_graph_without_child_jobs():
    harness = runtime(port=board(lanes=("A", "B"), blocked={"B": ("A",)}))
    queue = build_job_queue(settings=JobQueueSettings(), workflow_engine=harness.engine)
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
        async with asyncio.timeout(15):
            payloads = [item async for item in handler.attach_job(job_id=record.job_id)]
        assert not [item for item in payloads if item["type"] == "error"]
        nested = [item for item in payloads if item["type"] == "scope_lane"]
        iterations = [
            item for item in nested if item["event"]["type"] == "workflow_iteration"
        ]
        assert [item["laneKey"] for item in iterations] == ["A"]
        assert (
            iterations[0]["event"]["evaluation"]["criteriaResults"][0]["criterionId"]
            == "A/check"
        )
        observations = [
            item["observation"] for item in payloads if item["type"] == "scope_walk"
        ]
        assert observations[-1]["dispatched"] == ["A"]
        assert set(observations[-1]["unresolvedCriteria"]) == {"A/check", "B/check"}
        assert "workflow_complete" not in {item["type"] for item in payloads}
        finished = await queue.get(job_id=record.job_id)
        assert finished.state is JobState.TERMINAL
        assert finished.outcome is None
        assert list(queue._records) == [record.job_id]
        assert harness.port.claim_writes == []
        assert harness.port.issue_writes == []
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
    async for event in drive(harness):
        events.append(event)
        if isinstance(event, ScopeLaneEvent) and isinstance(
            event.event, WorkflowIterationEvent
        ):
            if event.lane_key != "A":
                continue
            if change == "approval":
                port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="B")] = (
                    frozenset()
                )
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


async def test_unapproved_scope_observation_cannot_equal_closed_scope():
    port = board(approved=False)
    harness = runtime(port=port)
    events = [event async for event in drive(harness)]
    assert len(events) == 1
    observation = events[0].observation
    assert observation.unapproved_lanes == ("A",)
    assert observation.unresolved_criteria == ("A/check",)
    assert observation.dispatched == ()
    assert harness.executor.schema_calls == []
    assert not any(isinstance(event, WorkflowCompleteEvent) for event in events)


async def test_existing_plan_barrier_prevents_any_lane_effect():
    port = board()
    port.issues["decision"] = make_tracker_issue(
        "decision", parent_key="A", issue_labels=frozenset({"decision"})
    )
    harness = runtime(port=port)
    with pytest.raises(ScopePlanRefusalError) as caught:
        _ = [event async for event in drive(harness)]
    assert caught.value.open_decisions == ("decision",)
    assert harness.executor.schema_calls == []


async def test_completed_checkpoint_is_revalidated_before_it_can_be_replayed():
    harness = runtime()
    _ = [event async for event in drive(harness)]
    harness.port.issues["A/check"] = harness.port.issues["A/check"].model_copy(
        update={"body": "**Check:** materially changed Check\n**Evidence:** —"}
    )
    fresh = runtime(port=harness.port, saver=harness.saver)
    with pytest.raises(FireSpecEntryError, match="evaluated snapshot"):
        _ = [event async for event in drive(fresh)]
    assert fresh.executor.schema_calls == []


async def test_same_job_checkpoint_replay_does_not_mint_another_branch():
    harness = runtime()
    _ = [event async for event in drive(harness)]
    fresh = runtime(port=harness.port, saver=harness.saver)
    events = [event async for event in drive(fresh)]
    assert fresh.executor.schema_calls == []
    assert [
        event.observation.dispatched
        for event in events
        if isinstance(event, ScopeWalkEvent)
    ][-1] == ("A",)


async def test_recorded_branch_without_checkpoint_refuses_instead_of_reminting():
    port = board()
    port.recorded_work_refs["A"] = [
        WorkRef(
            issue_id="A",
            role=WorkRefRole.DELIVERABLE,
            branch="existing-branch",
            recorded_at=FIXTURE_EPOCH,
        )
    ]
    harness = runtime(port=port)
    with pytest.raises(ScopeReadError, match="cross-job reentry"):
        _ = [event async for event in drive(harness)]
    assert harness.executor.schema_calls == []


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
        async for event in drive(harness):
            if isinstance(event, ScopeLaneEvent) and isinstance(
                event.event, WorkflowIterationEvent
            ):
                monkeypatch.setattr(port, "scope_issues", current_scope)
    assert len(harness.executor.execution_prompts) == 1
    assert "Exact native subject B" not in harness.executor.execution_prompts[0]
    assert port.claim_writes == []


async def test_changed_resolved_base_refuses_the_same_job_checkpoint():
    harness = runtime()
    _ = [event async for event in drive(harness)]
    fresh = runtime(port=harness.port, saver=harness.saver, trunk="other-trunk")
    with pytest.raises(ScopeReadError, match="different scope request"):
        _ = [event async for event in drive(fresh)]
    assert fresh.executor.schema_calls == []


async def test_distinct_queue_jobs_never_alias_a_lane_checkpoint():
    harness = runtime()
    _ = [event async for event in drive(harness, job="first-job")]
    fresh = runtime(port=harness.port, saver=harness.saver)
    _ = [event async for event in drive(fresh, job="second-job")]
    assert fresh.executor.execution_prompts
    assert sum("slug" in props for props in fresh.executor.schema_calls) == 1


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
    events = [event async for event in drive(harness)]
    assert harness.executor.schema_calls == []
    assert events[-1].observation.unapproved_lanes == ("A",)
    assert events[-1].observation.unresolved_criteria == ("A/check",)


def lane_of(harness):
    return harness.engine._scoped_arm._lane_for(ORIGIN)


def checkpoint_config():
    return {"configurable": {"thread_id": _lane_checkpoint_key("scope-job", "A")}}


async def pause_before_delivery(harness):
    lane = lane_of(harness)
    lane.graph.interrupt_before_nodes = ["deliver"]
    events = []
    with pytest.raises(ScopeReadError, match="no final delivery phase"):
        async for event in drive(harness):
            events.append(event)
    snapshot = await lane.graph.aget_state(checkpoint_config())
    assert snapshot.next == ("deliver",)
    assert not any(
        isinstance(event, ScopeLaneEvent) and isinstance(event.event, LaneDeliveryEvent)
        for event in events
    )
    return snapshot, events


async def test_resolved_checkpointer_round_trips_pause_identity_and_existing_branch():
    async with make_checkpointer(":memory:") as saver:
        harness = runtime(saver=saver)
        paused, events = await pause_before_delivery(harness)
        metadata = paused.metadata
        assert isinstance(metadata["scope_lane_request"], str)
        assert json.loads(metadata["scope_lane_request"])["job"] == "scope-job"
        original = RunIdentity.model_validate_json(metadata["scope_lane_run_identity"])
        observed = [
            event.event.invocation.run
            for event in events
            if isinstance(event, ScopeLaneEvent)
            and isinstance(event.event, NodeSessionStartedEvent)
        ]
        assert observed and all(run == original for run in observed)
        original_branch = paused.values["feature_branch"]
        assert original_branch and paused.values["feature_tip_sha"]
        fresh = runtime(port=harness.port, saver=saver)
        replayed = [event async for event in drive(fresh)]
        assert fresh.executor.schema_calls == []
        final = await lane_of(fresh).graph.aget_state(checkpoint_config())
        assert final.next == ()
        assert final.values["feature_branch"] == original_branch
        assert (
            final.metadata["scope_lane_run_identity"]
            == metadata["scope_lane_run_identity"]
        )
        assert any(
            isinstance(event, ScopeLaneEvent)
            and isinstance(event.event, LaneDeliveryEvent)
            for event in replayed
        )
        assert replayed[-1].observation.skipped_lanes == ("A",)
        assert replayed[-1].observation.unresolved_criteria == ("A/check",)


@pytest.mark.parametrize("change", ["check", "membership", "owed-state", "outage"])
async def test_fresh_engine_paused_resume_requires_current_native_obligations(
    change, monkeypatch
):
    harness = runtime()
    await pause_before_delivery(harness)
    port = harness.port
    if change == "check":
        port.issues["A/check"] = port.issues["A/check"].model_copy(
            update={"body": "**Check:** changed paused Check\n**Evidence:** —"}
        )
    elif change == "membership":
        port.issues["A/new"] = make_tracker_issue(
            "A/new",
            parent_key="A",
            issue_labels=frozenset({"criterion"}),
            body="**Check:** new owed Check\n**Evidence:** —",
        )
    elif change == "owed-state":
        # Another obligation keeps the lane eligible, while the judged roster changes.
        port.issues["A/check"] = port.issues["A/check"].model_copy(
            update={"state_kind": WorkflowStateKind.COMPLETED}
        )
        port.issues["A/new"] = make_tracker_issue(
            "A/new",
            parent_key="A",
            issue_labels=frozenset({"criterion"}),
            body="**Check:** remaining owed Check\n**Evidence:** —",
        )
    fresh = runtime(port=port, saver=harness.saver)
    if change == "outage":
        original = port.scope_issues

        async def current_unavailable(*, ref):
            if ref.kind is ScopeKind.ISSUE:
                raise TrackerUnavailableError("current criterion authority unavailable")
            return await original(ref=ref)

        probe = fresh.engine._scoped_arm._probe_for(ORIGIN)
        probes = 0

        async def no_open_delivery(*, repo_url, issue_key):
            nonlocal probes
            probes += 1
            if probes == 2:
                # Outage starts after the last admission read, at the launch boundary.
                monkeypatch.setattr(port, "scope_issues", current_unavailable)
            return False

        monkeypatch.setattr(probe, "open_delivery_exists", no_open_delivery)
    with pytest.raises(FireSpecEntryError):
        _ = [event async for event in drive(fresh)]
    assert fresh.executor.schema_calls == []
    assert (await lane_of(fresh).graph.aget_state(checkpoint_config())).next == (
        "deliver",
    )


async def test_current_closed_blocker_unlocks_next_lane_using_its_actual_work_ref():
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    harness = runtime(port=port, lanes=("A", "B"))
    events = []
    async for event in drive(harness):
        events.append(event)
        if isinstance(event, ScopeLaneEvent) and isinstance(
            event.event, LaneDeliveryEvent
        ):
            if event.lane_key == "A":
                port.issues["A/check"] = port.issues["A/check"].model_copy(
                    update={"state_kind": WorkflowStateKind.COMPLETED}
                )
                port.recorded_work_refs["A"] = [
                    WorkRef(
                        issue_id="A",
                        role=WorkRefRole.DELIVERABLE,
                        branch="recorded-A",
                        pushed_head_sha="a" * 40,
                        recorded_at=FIXTURE_EPOCH,
                    )
                ]
    iterations = [
        event.lane_key
        for event in events
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, WorkflowIterationEvent)
    ]
    assert iterations == ["A", "B"]
    assert port.issues["A"].state_kind is WorkflowStateKind.UNSTARTED
    final = events[-1].observation
    assert final.unresolved_criteria == ("B/check",)
    assert port.workflow_writes == []
    b_checkpoint = await lane_of(harness).graph.aget_state(
        {"configurable": {"thread_id": _lane_checkpoint_key("scope-job", "B")}}
    )
    binding = json.loads(b_checkpoint.metadata["scope_lane_request"])
    assert json.loads(binding["base"])["base_branch"] == "recorded-A"


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
        observation = events[-1]["observation"]
        assert observation["skippedLanes"] == ["A"]
        assert observation["unresolvedCriteria"] == ["A/check"]
        assert all(event["type"] != "workflow_complete" for event in events)
        status = (await app.client.get(f"/api/v1/jobs/{job_id}")).json()
        assert status["state"] == "terminal" and status["outcome"] is None


async def test_actual_scope_composition_retains_completed_native_delivery_record():
    from kodezart.types.domain.native_delivery import CompletedLaneDelivery
    from tests.adapters.test_github_api import _make_client
    from tests.chains.test_native_delivery import ForgeWire

    origin = "https://github.com/owner/repo"
    wire = ForgeWire()

    def scope_wire(request):
        if (
            request.method == "GET"
            and request.url.path.endswith("/pulls")
            and "head" not in request.url.params
        ):
            wire.requests.append(request)
            return httpx.Response(200, json=[] if wire.pr is None else [wire.pr])
        return wire(request)

    forge = _make_client(scope_wire)
    try:
        harness = runtime(origin=origin, forge=forge, trunk="main")
        events = [event async for event in drive(harness, origin=origin)]
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
        assert phase.result.final_commit_sha == "a" * 40
        assert phase.result.checks_passed is True
        assert not phase.result.remediation_pending
        lane = harness.engine._scoped_arm._lane_for(origin)
        final = await lane.graph.aget_state(checkpoint_config())
        assert final.values["delivery"] == phase
        assert events[-1].observation.unresolved_criteria == ("A/check",)
        assert harness.port.workflow_writes == []
    finally:
        await forge.close()


@pytest.mark.parametrize("change", ["repository", "path", "scope"])
async def test_same_job_checkpoint_refuses_incompatible_request_identity(change):
    harness = runtime()
    _ = [event async for event in drive(harness)]
    origin = "file:///another-repository.git" if change == "repository" else ORIGIN
    scope = (
        ScopeRef(kind=ScopeKind.PROJECT, key="another-scope")
        if change == "scope"
        else SCOPE
    )
    harness.port.scope_memberships[scope] = ("A",)
    fresh = runtime(port=harness.port, saver=harness.saver, origin=origin)
    with pytest.raises(ScopeReadError, match="different scope request"):
        _ = [
            event
            async for event in drive(
                fresh,
                origin=origin,
                scope=scope,
                path="/tmp/another-repo" if change == "path" else None,
            )
        ]
    assert fresh.executor.schema_calls == []


@pytest.mark.parametrize("amended", [False, True])
async def test_nested_fire_resume_reuses_branch_and_original_attributed_run(amended):
    harness = runtime()
    lane = lane_of(harness)
    lane.fire.native_graph.interrupt_before_nodes = ["review_against_ticket"]
    before = []
    with pytest.raises(ScopeReadError, match="no final delivery phase"):
        async for event in drive(harness):
            before.append(event)
    paused = await lane.graph.aget_state(checkpoint_config(), subgraphs=True)
    child = paused.tasks[0].state
    assert child.next == ("review_against_ticket",)
    branch = child.values["feature_branch"]
    original_identity = RunIdentity.model_validate_json(
        paused.metadata["scope_lane_run_identity"]
    )
    if amended:
        harness.port.issues["A/check"] = harness.port.issues["A/check"].model_copy(
            update={"body": "**Check:** amended before resumed review\n**Evidence:** —"}
        )
    harness.port.issues["A"] = harness.port.issues["A"].model_copy(
        update={"body": "A later subject body must not replace the frozen subject"}
    )
    fresh = runtime(port=harness.port, saver=harness.saver)
    after = [event async for event in drive(fresh)]
    assert fresh.executor.execution_prompts == []
    assert not any("slug" in props for props in fresh.executor.schema_calls)
    reviews = fresh.executor.evaluation_prompts
    assert len(reviews) == 1
    assert "A later subject body" not in reviews[0]
    if amended:
        assert "amended before resumed review" in reviews[0]
    identities = fresh.executor.run_identities
    assert identities and all(identity == original_identity for identity in identities)
    assert any(isinstance(event, ScopeLaneEvent) for event in after)
    final = await lane_of(fresh).graph.aget_state(checkpoint_config())
    assert final.next == () and final.values["feature_branch"] == branch
    assert final.values["fire_spec"].body == "Exact native subject A  with spaces\n"
