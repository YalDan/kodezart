"""Real request composition, controller and native graphs with external doubles."""

import asyncio
from dataclasses import dataclass

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.chains.criteria import TrackerCriteria
from kodezart.composition.engine import build_workflow_engine
from kodezart.composition.jobs import build_job_queue
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
from kodezart.types.domain.agent import WorkflowCompleteEvent, WorkflowIterationEvent
from kodezart.types.domain.branch import WorkRef, WorkRefRole, trunk_base
from kodezart.types.domain.job import JobState
from kodezart.types.domain.operation import RepoEntry, ScopeLabel
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeLaneEvent, ScopeWalkEvent
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.ticket_review import TicketReviewMode
from kodezart.types.requests.agent import WorkflowRequest
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
        return ("b" if branch == "trunk" else "a") * 40


@dataclass
class Harness:
    engine: object
    port: FakeTrackerPort
    executor: NativeExecutor
    service: AgentService
    artifacts: FakeArtifactPersister
    saver: InMemorySaver


def runtime(*, port=None, lanes=("A",), saver=None, evaluations=None, trunk="trunk"):
    port = port or board(lanes=lanes)
    executor = NativeExecutor(
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
        repositories=(RepoEntry(url=ORIGIN, trunk=trunk),),
        agent_service=service,
        git=RemoteGit(),
        cache=FakeRepoCache(),
        workspace=workspace,
        merger=FakeBranchMerger(),
        artifact_persister=artifacts,
        ref_publisher=FakeRefPublisher(),
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        github_api=None,
        checkpointer=saver,
        criteria=TrackerCriteria(tracker=port),
        scope_tracker=port,
    )
    return Harness(engine, port, executor, service, artifacts, saver)


def drive(harness, *, job="scope-job", scope=SCOPE, origin=ORIGIN):
    return harness.engine.run(
        prompt="Request prose is not the native subject",
        repo_path=None,
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
        assert observations[-1]["dispatched"] == ("A",)
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
