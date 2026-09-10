"""Recorded tracker identity survives real queue and workflow boundaries."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest

from kodezart.adapters.asyncio_job_queue import AsyncioJobQueue
from kodezart.composition.engine import OriginRoutedWorkflowEngine
from kodezart.core.protocols import OutboundContentGate
from kodezart.handlers.agent_handler import AgentHandler
from kodezart.services.agent_service import AgentService
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.fire_context import FireContextAssembler
from kodezart.services.fire_dispatcher import FireDispatcher
from kodezart.types.domain.agent import AgentEvent, ErrorEvent, WorkflowPREvent
from kodezart.types.domain.dispatch import DispatchOutcome
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    OutboundDestination,
    RedactionCategory,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.operation import CheckStep, RepoEntry
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.requests.agent import WorkflowRequest
from tests.chains.test_ralph_workflow import _make_engine, _stalled_gate
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeChangePersister,
    FakeDeliveryProbe,
    FakeGitService,
    FakePRCreator,
    FakeRepoCache,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_tracker_issue,
)
from tests.outbound import LiteralJudgment, make_admission
from tests.services.test_fire_dispatcher import (
    ASSET_FETCH_TIMEOUT_SECONDS,
    ASSET_MAX_BYTES,
    ASSET_MAX_COUNT,
    HOLDER,
    INTEGRATION_DIR,
    LANE,
    LEASE_SECONDS,
    PAGE_SIZE,
    REMOTE,
    TRUNK,
    lane_cooldown,
    operation_config,
)

ISSUE_KEY = "K-1"
REPO_URL = "https://github.com/example/repository"
DECOY_PROMPT = "Tracker issue: DECOY-999\n\nImplement the described change."
GENERATED_DESCRIPTION = "Test PR description."
SETTLE_SECONDS = 10.0


class RecordingGate:
    """Record the complete write before applying the existing gate."""

    def __init__(self, *, block_identity: bool = False) -> None:
        patterns = (
            {RedactionCategory.CREDENTIALS: [f"Tracker issue: {ISSUE_KEY}"]}
            if block_identity
            else {}
        )
        self._inner = make_admission(LiteralJudgment(patterns))
        self.bodies: list[str] = []

    async def gate(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
        destination: OutboundDestination,
        content_class: ContentClass,
    ) -> GateDecision:
        if destination is OutboundDestination.PR_BODY:
            self.bodies.append(content)
        return await self._inner.gate(
            content=content,
            visibility=visibility,
            shape=shape,
            destination=destination,
            content_class=content_class,
        )


@dataclass(frozen=True)
class WorkflowHarness:
    queue: AsyncioJobQueue
    dispatcher: FireDispatcher
    handler: AgentHandler
    creator: FakePRCreator


@asynccontextmanager
async def workflow_harness(
    gate: OutboundContentGate, *, stalled: bool = False
) -> AsyncIterator[WorkflowHarness]:
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue(ISSUE_KEY, body=DECOY_PROMPT)],
    )
    git = FakeGitService(remote_branch_shas={TRUNK: "b" * 40})
    creator = FakePRCreator()
    engine = _make_engine(
        git=git,
        pr_creator=creator,
        outbound_gate=gate,
        quality_gate=_stalled_gate() if stalled else None,
    )
    router = OriginRoutedWorkflowEngine(
        forge_arm=engine,
        forge_less_arm=_make_engine(),
    )
    queue = AsyncioJobQueue(
        engine=router,
        max_concurrent_runs_per_lane=1,
        max_depth_per_lane=64,
        terminal_retention_seconds=86400.0,
        event_buffer_retention_seconds=900.0,
        event_buffer_capacity=512,
    )
    fire = FireDispatcher(
        tracker=tracker,
        queue=queue,
        registry=queue,
        delivery=FakeDeliveryProbe(),
        operation=operation_config(
            repos=[
                RepoEntry(
                    url=REPO_URL,
                    trunk=TRUNK,
                    checks=[CheckStep(name="check", command="make check")],
                ),
            ],
        ),
        repo_url=REPO_URL,
        lane=LANE,
        holder=HOLDER,
        claim_lease_seconds=LEASE_SECONDS,
        query_page_size=PAGE_SIZE,
        cooldown=lane_cooldown(),
        assembler=FireContextAssembler(
            tracker=tracker,
            gate=PassThroughGate(),
            max_count=ASSET_MAX_COUNT,
            max_bytes=ASSET_MAX_BYTES,
            fetch_timeout_seconds=ASSET_FETCH_TIMEOUT_SECONDS,
        ),
        resolver=BaseResolver(tracker=tracker, git=git, remote=REMOTE),
        cache=FakeRepoCache(),
        trunk=TRUNK,
        integration_workspace_dir=INTEGRATION_DIR,
    )
    handler = AgentHandler(
        service=AgentService(
            git_base_url="https://github.com",
            executor=FakeAgentExecutor(events=[]),
            workspace=FakeWorkspaceProvider(),
            persister=FakeChangePersister(),
        ),
        skills=SUPPRESS_ALL_SKILLS,
        queue=queue,
    )
    await queue.start()
    try:
        yield WorkflowHarness(
            queue=queue, dispatcher=fire, handler=handler, creator=creator
        )
    finally:
        await queue.stop()


async def finish(queue: AsyncioJobQueue, job_id: str) -> list[AgentEvent]:
    async with asyncio.timeout(SETTLE_SECONDS):
        return [event async for event in queue.attach(job_id=job_id)]


@pytest.mark.parametrize("stalled", [False, True])
async def test_dispatched_identity_reaches_the_complete_gated_pr_body(
    stalled: bool,
) -> None:
    gate = RecordingGate()
    async with workflow_harness(gate, stalled=stalled) as harness:
        report = await harness.dispatcher.run_pass()
        assert report.outcome is DispatchOutcome.fire_enqueued
        assert report.claimed_issue_key == ISSUE_KEY
        assert report.job_id is not None
        events = await finish(harness.queue, report.job_id)
        assert not [event for event in events if isinstance(event, ErrorEvent)]
        (created,) = harness.creator.calls
        assert created["method"] == "create_pr"
        body = str(created["body"])
        assert body.endswith(f"\n\nTracker issue: {ISSUE_KEY}")
        assert "DECOY-999" not in body
        assert created["base"] == TRUNK
        assert gate.bodies == [body]
        assert any(isinstance(event, WorkflowPREvent) for event in events)
        if not stalled:
            assert body == f"{GENERATED_DESCRIPTION}\n\nTracker issue: {ISSUE_KEY}"


@pytest.mark.parametrize("stalled", [False, True])
async def test_the_appended_identity_can_block_the_entire_pr_write(
    stalled: bool,
) -> None:
    gate = RecordingGate(block_identity=True)
    async with workflow_harness(gate, stalled=stalled) as harness:
        report = await harness.dispatcher.run_pass()
        assert report.job_id is not None
        events = await finish(harness.queue, report.job_id)
        assert harness.creator.calls == []
        (body,) = gate.bodies
        assert body.endswith(f"Tracker issue: {ISSUE_KEY}")
        (error,) = [event for event in events if isinstance(event, ErrorEvent)]
        assert error.error_kind == "OutboundContentBlockedError"
        record = await harness.queue.get(job_id=report.job_id)
        assert record is not None
        assert record.outcome is WorkflowOutcome.engine_error


@pytest.mark.parametrize("issue_key", [None, "EXT/42"])
async def test_http_request_identity_is_explicit_and_never_parsed_from_prompt(
    issue_key: str | None,
) -> None:
    gate = RecordingGate()
    async with workflow_harness(gate) as harness:
        request = WorkflowRequest(
            prompt=DECOY_PROMPT,
            repo_url=REPO_URL,
            base_branch=TRUNK,
            issue_key=issue_key,
        )
        record = await harness.handler.submit_workflow(request, lane=LANE)
        events = await finish(harness.queue, record.job_id)
        assert not [event for event in events if isinstance(event, ErrorEvent)]
        (created,) = harness.creator.calls
        expected = (
            GENERATED_DESCRIPTION
            if issue_key is None
            else f"{GENERATED_DESCRIPTION}\n\nTracker issue: {issue_key}"
        )
        assert created["body"] == expected
        assert gate.bodies == [expected]
