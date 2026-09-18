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
from kodezart.config.app import AppConfig
from kodezart.config.job_queue import JobQueueSettings
from kodezart.config.write_back import WriteBackSettings
from kodezart.core.checkpointer import make_checkpointer
from kodezart.core.errors import TrackerUnavailableError
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import (
    FireSpecEntryError,
    GitSourceReadError,
    ScopePlanRefusalError,
    ScopeReadError,
)
from kodezart.domain.fire_spec import criterion_field_bodies
from kodezart.handlers.agent_handler import AgentHandler
from kodezart.services.agent_service import AgentService
from kodezart.services.lane_records import LaneRecordReader
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
    ChangesetDigest,
    ConsolidationOutcome,
    ConsolidationStatus,
)
from kodezart.types.domain.job import JobState
from kodezart.types.domain.native_delivery import LaneDeliveryEvent
from kodezart.types.domain.operation import LifecycleStage, RepoEntry, ScopeLabel
from kodezart.types.domain.persist import PersistResult, PersistSource
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeLaneEvent, ScopeWalkEvent
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.ticket_review import TicketReviewMode
from kodezart.types.domain.tracker import WorkflowStateKind
from kodezart.types.requests.agent import WorkflowRequest
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
    FakeGitService,
    FakeRefPublisher,
    FakeRepoCache,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_prompt_provider,
    make_tracker_issue,
)
from tests.lane_fixture import TRUNK_BRANCHES, TRUNK_SHA, LaneRepo, criteria_echo

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
        # The board reads its markers under the operation the engine writes
        # them under; a port with no prefixes could answer for no lane.
        marker_prefixes=native_operation().marker_prefixes,
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
    persister=None,
    git=None,
    source=None,
    workspace=None,
    max_iterations=1,
):
    """The composed engine over external doubles.

    *persister*, *git*, *source* and *workspace* default to today's
    no-commit doubles; a test about what a walk leaves on the board supplies
    repositories that actually commit, so every recorded fact comes from an
    observation of one.
    """
    port = port or board(lanes=lanes)
    executor = ObservedNativeExecutor(
        evaluations
        or [
            native_evaluation(checks={f"{key}/check": f"{key} live Check  bytes"})
            for key in lanes
            for _ in range(2)
        ]
    )
    git = git if git is not None else RemoteGit()
    # The workspace reports its identity through the same Git double the rest
    # of the fixture reads, so a prepared tree and the head it was cut at are
    # one repository's answer rather than two doubles'.
    workspace = FakeWorkspaceProvider(git=git) if workspace is None else workspace
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor,
        workspace=workspace,
        persister=persister if persister is not None else FakeChangePersister(),
    )
    artifacts = FakeArtifactPersister()
    saver = saver or InMemorySaver()
    # Pair the fake filesystem/Git boundary with its immutable-source double.
    # The production builder, native owner and graph remain actual consumers.
    with pytest.MonkeyPatch.context() as external:
        external.setattr(
            "kodezart.composition.engine.SubprocessGitSourceReader",
            NativeSourceReader if source is None else (lambda: source),
        )
        engine = build_workflow_engine(
            operation=native_operation(),
            config=AppConfig(
                write_back=WriteBackSettings(max_verify_rounds=2),
                ticket_review_mode=TicketReviewMode.REVIEWED,
                max_iterations=max_iterations,
                retry_max_attempts=1,
                retry_initial_interval=0.1,
            ),
            repositories=(RepoEntry(url=origin, trunk=trunk),),
            agent_service=service,
            git=git,
            cache=FakeRepoCache(),
            workspace=workspace,
            merger=FakeBranchMerger(
                consolidation_outcomes=[
                    ConsolidationOutcome(
                        status=ConsolidationStatus.FAST_FORWARDED,
                        feature_tip_sha="a" * 40,
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
    harness = runtime(
        port=board(lanes=("A", "B"), blocked={"B": ("A",)}), lanes=("A", "B")
    )
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
        payloads = []
        async with asyncio.timeout(15):
            async for item in handler.attach_job(job_id=record.job_id):
                payloads.append(item)
                if (
                    item["type"] == "scope_lane"
                    and item["laneKey"] == "A"
                    and item["event"]["type"] == "lane_delivery"
                ):
                    # A delivered, so the deliverable ref a delivery records
                    # exists; B resolves its base from the blocker it names.
                    harness.port.recorded_work_refs["A"] = [
                        WorkRef(
                            issue_id="A",
                            role=WorkRefRole.DELIVERABLE,
                            branch="recorded-A",
                            pushed_head_sha="a" * 40,
                            recorded_at=FIXTURE_EPOCH,
                        )
                    ]
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
        finished = await queue.get(job_id=record.job_id)
        assert finished.state is JobState.TERMINAL
        assert finished.outcome is None
        assert list(queue._records) == [record.job_id]
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
    owed_again(harness.port, check="materially changed Check")
    fresh = runtime(port=harness.port, saver=harness.saver)
    with pytest.raises(FireSpecEntryError, match="evaluated snapshot"):
        _ = [event async for event in drive(fresh)]
    assert fresh.executor.schema_calls == []


async def test_same_job_checkpoint_replay_does_not_mint_another_branch():
    harness = runtime()
    _ = [event async for event in drive(harness)]
    owed_again(harness.port)
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
    owed_again(harness.port)
    fresh = runtime(port=harness.port, saver=harness.saver, trunk="other-trunk")
    with pytest.raises(ScopeReadError, match="different scope request"):
        _ = [event async for event in drive(fresh)]
    assert fresh.executor.schema_calls == []


async def test_distinct_queue_jobs_never_alias_a_lane_checkpoint():
    harness = runtime()
    _ = [event async for event in drive(harness, job="first-job")]
    owed_again(harness.port)
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


def owed_again(port, *, lane="A", check=None):
    """Put a lane's criterion back in Todo, the way the product does.

    A lane whose every criterion its run crossed off is closed by the
    tracker's rollup, and the walker offers no closed lane. What re-opens one
    in production is the amendment write-back: the criterion returns to the
    unstarted state with its Evidence cleared, carrying the amended Check
    when the amendment changed the text.
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
    # The loop ran before this pause and crossed its criterion off, so the
    # lane is closed and no walk would offer it again. What the resume this
    # fixture sets up is about is the checkpoint, so the lane is made owed
    # again the way the product makes one owed again.
    owed_again(harness.port)
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
                # A's criterion is already Done: its own evaluation step
                # crossed it off, so nothing here has to close the blocker.
                assert port.issues["A/check"].state_kind is WorkflowStateKind.COMPLETED
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
    assert final.unresolved_criteria == ()
    # Exactly the two criterion sub-issues moved, and neither subject did.
    assert port.workflow_writes == [
        ("A/check", LifecycleStage.DONE),
        ("B/check", LifecycleStage.DONE),
    ]
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
        observation = events[-1]["observation"]
        assert observation["skippedLanes"] == ["A"]
        assert observation["unresolvedCriteria"] == []
        assert harness.port.issues["A/check"].state_kind is WorkflowStateKind.COMPLETED
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
        addressed = ScopeLaneEvent(
            lane_key="A", event=LaneDeliveryEvent(delivery=phase)
        )
        assert (
            ScopeLaneEvent.model_validate_json(addressed.model_dump_json()) == addressed
        )
        lane = harness.engine._scoped_arm._lane_for(origin)
        final = await lane.graph.aget_state(checkpoint_config())
        assert final.values["delivery"] == phase
        assert events[-1].observation.unresolved_criteria == ()
        assert harness.port.workflow_writes == [("A/check", LifecycleStage.DONE)]
        # The sha the lane's own loop branch stands at, as the repository
        # double answers it, rather than a value spelled here.
        assert parse_criterion_evidence(
            harness.port.issues["A/check"].body
        ).graded_sha == await NativeSourceReader().resolve_commit(cwd="", ref="ralph/A")
    finally:
        await forge.close()


@pytest.mark.parametrize("change", ["repository", "path", "scope"])
async def test_same_job_checkpoint_refuses_incompatible_request_identity(change):
    harness = runtime()
    _ = [event async for event in drive(harness)]
    owed_again(harness.port)
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
    # The loop crossed its criterion off before this pause, so the lane is
    # made owed again the way the product does it — carrying the amended
    # Check where the amendment is what this case is about.
    owed_again(harness.port, check="amended before resumed review" if amended else None)
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

    def __init__(self, *, remote: str = "origin") -> None:
        self.remote = remote
        self.branches: dict[str, LaneRepo] = {}
        #: The refs a delivery published, which a later lane resolves a base
        #: from: they carry no commits of this walk and hold one sha each.
        self.delivered: dict[str, str] = {}
        # Before the first commit of the walk the trees are the trunk's, which
        # is what the repository this walk was cut from holds.
        self.committing = LaneRepo(branch="trunk", remote=remote)

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
        return self.repos.current.head

    async def remote_branch_sha(self, cwd, remote, branch):
        self.calls.append(("remote_branch_sha", cwd, remote, branch))
        if branch in TRUNK_BRANCHES:
            return TRUNK_SHA
        if remote != self.repos.remote:
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


async def lane_record(port, key: str):
    """The record this walk left on one lane's issue, read back fresh."""
    _, record = await LaneRecordReader(tracker=port, operation=native_operation()).read(
        issue_key=key, lane_key=key
    )
    return record


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
            port.recorded_work_refs[event.lane_key] = [
                deliverable_of(repos, event.lane_key)
            ]

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
