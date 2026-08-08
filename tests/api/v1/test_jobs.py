"""ASGITransport tests for the job lifecycle: queue, fire, status, stream."""

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
import structlog
from httpx import ASGITransport, AsyncClient, Response
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from kodezart.adapters.asyncio_job_queue import AsyncioJobQueue
from kodezart.adapters.langgraph_run_state_reader import LangGraphRunStateReader
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.core.config import AppConfig
from kodezart.core.constants import DEFAULT_LANE
from kodezart.core.protocols import JobQueue, JobRegistry
from kodezart.domain.errors import QueueFullError
from kodezart.domain.thread_id import (
    ralph_thread_id,
    ticket_thread_id,
    workflow_thread_id,
)
from kodezart.main import create_app
from kodezart.services.agent_service import AgentService
from kodezart.services.job_service import JobService
from kodezart.types.domain import job as job_types
from kodezart.types.domain.agent import (
    AgentEvent,
    AssistantTextEvent,
    WorkflowCompleteEvent,
)
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.job import JobState
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run import RunState
from kodezart.types.requests.agent import WorkflowRequest
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeBranchMerger,
    FakeChangePersister,
    FakeGitService,
    FakeQualityGate,
    FakeRepoCache,
    FakeTicketGenerator,
    FakeWorkspaceProvider,
    PassThroughGate,
    ScriptedFakeExecutor,
    make_passing_evaluation,
    make_prompt_provider,
)

_BODY: dict[str, object] = {"prompt": "fix", "repoPath": "/tmp/fake"}


# ---------------------------------------------------------------------------
# Engine doubles
# ---------------------------------------------------------------------------


class GatedWorkflowEngine:
    """WorkflowEngine that holds each run open until its gate is released.

    Records start/finish order so arrival-order and interleaving claims
    are observable rather than inferred from timing.
    """

    def __init__(self, *, events: list[AgentEvent] | None = None) -> None:
        self.started: list[str] = []
        self.finished: list[str] = []
        self.cache_keys: list[str] = []
        self._events: list[AgentEvent] = events if events is not None else []
        self._gates: dict[str, asyncio.Event] = {}

    def gate(self, prompt: str) -> asyncio.Event:
        if prompt not in self._gates:
            self._gates[prompt] = asyncio.Event()
        return self._gates[prompt]

    def release(self, prompt: str) -> None:
        self.gate(prompt).set()

    async def run(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        base_branch: str,
        permission_mode: str,
        allowed_tools: list[str],
        cache_key: str,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.started.append(prompt)
        self.cache_keys.append(cache_key)
        await self.gate(prompt).wait()
        for event in self._events:
            yield event
        self.finished.append(prompt)


class ChattyWorkflowEngine:
    """WorkflowEngine that emits a fixed script and returns immediately."""

    def __init__(self, events: list[AgentEvent]) -> None:
        self._events = events
        self.cache_keys: list[str] = []

    async def run(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        base_branch: str,
        permission_mode: str,
        allowed_tools: list[str],
        cache_key: str,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.cache_keys.append(cache_key)
        for event in self._events:
            yield event


def _complete_event(outcome: WorkflowOutcome) -> WorkflowCompleteEvent:
    return WorkflowCompleteEvent(
        feature_branch="kodezart/x-12345678",
        ralph_branch="kodezart/x-12345678-ralph-abcdef01",
        total_iterations=1,
        accepted=True,
        outcome=outcome,
        merged=True,
    )


async def _drain(queue: AsyncioJobQueue, job_id: str) -> list[AgentEvent]:
    return [event async for event in queue.attach(job_id=job_id)]


async def _wait_terminal(
    queue: AsyncioJobQueue, job_id: str, *, ticks: int = 400
) -> None:
    """Yield to the loop until *job_id* reaches TERMINAL, through the port."""
    for _ in range(ticks):
        record = await queue.get(job_id=job_id)
        if record is not None and record.state is JobState.TERMINAL:
            return
        await asyncio.sleep(0)
    msg = f"job {job_id} never reached TERMINAL"
    raise AssertionError(msg)


async def _wait_truncated(
    queue: AsyncioJobQueue, job_id: str, *, ticks: int = 400
) -> None:
    """Yield to the loop until *job_id*'s record is marked truncated."""
    for _ in range(ticks):
        record = await queue.get(job_id=job_id)
        if record is not None and record.truncated:
            return
        await asyncio.sleep(0)
    msg = f"job {job_id} was never marked truncated"
    raise AssertionError(msg)


async def _until(predicate: object, *, ticks: int = 200) -> None:
    """Yield to the loop until *predicate* holds. Fails loudly on timeout."""
    check = predicate
    assert callable(check)
    for _ in range(ticks):
        if check():
            return
        await asyncio.sleep(0)
    msg = "condition never became true"
    raise AssertionError(msg)


def _make_queue(
    engine: object,
    *,
    max_concurrent_runs_per_lane: int = 1,
    max_depth_per_lane: int = 64,
    terminal_retention_seconds: float = 86400.0,
    event_buffer_retention_seconds: float = 900.0,
    event_buffer_capacity: int = 512,
) -> AsyncioJobQueue:
    return AsyncioJobQueue(
        engine=engine,
        max_concurrent_runs_per_lane=max_concurrent_runs_per_lane,
        max_depth_per_lane=max_depth_per_lane,
        terminal_retention_seconds=terminal_retention_seconds,
        event_buffer_retention_seconds=event_buffer_retention_seconds,
        event_buffer_capacity=event_buffer_capacity,
    )


def _worker_tasks(queue: AsyncioJobQueue) -> list[asyncio.Task[None]]:
    """The dispatcher's live worker tasks across every lane."""
    return [worker for lane in queue._lanes.values() for worker in lane.workers]


def _request(prompt: str) -> WorkflowRequest:
    return WorkflowRequest(prompt=prompt, repo_path="/tmp/fake")


# ---------------------------------------------------------------------------
# HTTP fixtures
# ---------------------------------------------------------------------------


def _real_engine(checkpointer: InMemorySaver | None = None) -> RalphWorkflowEngine:
    service = AgentService(
        executor=FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    return RalphWorkflowEngine(
        service=service,
        quality_gate=FakeQualityGate(
            events=[AssistantTextEvent(text="done", model="m")],
            evaluation=make_passing_evaluation(),
            total_iterations=1,
            last_commit_sha="a" * 40,
        ),
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        checkpointer=checkpointer,
        artifact_persister=None,
    )


class GatedQualityGate:
    """QualityGate whose Nth invocation blocks until released.

    Holds the outer workflow graph open *inside* the node that called it,
    so a checkpoint read lands genuinely mid-run instead of after the
    fact.  Every invocation that is not gated behaves exactly like
    ``FakeQualityGate``, which it delegates to.
    """

    def __init__(self, *, gate_on_call: int) -> None:
        self.calls: int = 0
        self.entered: asyncio.Event = asyncio.Event()
        self._gate_on_call = gate_on_call
        self._release: asyncio.Event = asyncio.Event()
        self._inner = FakeQualityGate(
            events=[AssistantTextEvent(text="done", model="m")],
            evaluation=make_passing_evaluation(),
            total_iterations=1,
            last_commit_sha="a" * 40,
        )

    def release(self) -> None:
        self._release.set()

    async def run(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        feature_branch: str,
        ralph_branch: str,
        base_branch: str,
        permission_mode: str,
        allowed_tools: list[str],
        acceptance_criteria: list[str],
        cache_key: str,
        repo_visibility: RepoVisibility = RepoVisibility.UNKNOWN,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.calls += 1
        if self.calls == self._gate_on_call:
            self.entered.set()
            await self._release.wait()
        async for event in self._inner.run(
            prompt=prompt,
            repo_path=repo_path,
            repo_url=repo_url,
            feature_branch=feature_branch,
            ralph_branch=ralph_branch,
            base_branch=base_branch,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            acceptance_criteria=acceptance_criteria,
            cache_key=cache_key,
            repo_visibility=repo_visibility,
        ):
            yield event


def _mid_run_engine(
    checkpointer: InMemorySaver,
    quality_gate: GatedQualityGate,
) -> RalphWorkflowEngine:
    """Engine whose first post-merge review fails, forcing one fix round.

    The scripted executor answers the review schema: failing first, then
    passing, so the graph reaches ``fix_code`` — where *quality_gate*
    holds it open.
    """
    service = AgentService(
        executor=ScriptedFakeExecutor(
            eval_results=[
                {
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": "The fix compiles without errors",
                            "passed": False,
                            "reasoning": "Review found a gap.",
                        },
                        {
                            "criterionId": "AC-2",
                            "criterion": "All existing tests pass",
                            "passed": False,
                            "reasoning": "Review found a gap.",
                        },
                        {
                            "criterionId": "AC-3",
                            "criterion": "Linting passes with no new warnings",
                            "passed": False,
                            "reasoning": "Review found a gap.",
                        },
                    ],
                },
                {
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": "The fix compiles without errors",
                            "passed": True,
                            "reasoning": "Fixed.",
                        },
                        {
                            "criterionId": "AC-2",
                            "criterion": "All existing tests pass",
                            "passed": True,
                            "reasoning": "Fixed.",
                        },
                        {
                            "criterionId": "AC-3",
                            "criterion": "Linting passes with no new warnings",
                            "passed": True,
                            "reasoning": "Fixed.",
                        },
                    ],
                },
            ],
        ),
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    return RalphWorkflowEngine(
        service=service,
        quality_gate=quality_gate,
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        checkpointer=checkpointer,
        max_fix_rounds=1,
        artifact_persister=None,
    )


class _JobApp:
    """The wired app plus the pieces a test asserts against."""

    def __init__(
        self,
        client: AsyncClient,
        queue: AsyncioJobQueue,
        engine: object,
    ) -> None:
        self.client = client
        self.queue = queue
        self.engine = engine


async def _build_app(
    engine: object,
    *,
    checkpointer: InMemorySaver | None = None,
    run_state_available: bool = True,
    max_depth_per_lane: int = 64,
) -> AsyncGenerator[_JobApp, None]:
    app = create_app()
    app.state.skills = SUPPRESS_ALL_SKILLS
    app.state.agent_service = AgentService(
        executor=FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    app.state.workflow_engine = engine
    queue = _make_queue(engine, max_depth_per_lane=max_depth_per_lane)
    app.state.job_queue = queue
    await queue.start()
    reader = (
        LangGraphRunStateReader(checkpointer=checkpointer)
        if checkpointer is not None and run_state_available
        else None
    )
    app.state.job_service = JobService(registry=queue, run_state_reader=reader)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield _JobApp(client, queue, engine)
    finally:
        await queue.stop()


# loop_scope="function" pins these fixtures to the test's event loop so the
# dispatcher's worker tasks run while a request is in flight; the project
# default fixture loop scope is "session".
@pytest_asyncio.fixture(loop_scope="function")
async def job_app() -> AsyncGenerator[_JobApp, None]:
    async for wired in _build_app(_real_engine()):
        yield wired


@pytest_asyncio.fixture(loop_scope="function")
async def checkpointed_app() -> AsyncGenerator[_JobApp, None]:
    saver = InMemorySaver()
    async for wired in _build_app(_real_engine(saver), checkpointer=saver):
        yield wired


@pytest_asyncio.fixture(loop_scope="function")
async def checkpointerless_app() -> AsyncGenerator[_JobApp, None]:
    async for wired in _build_app(_real_engine(), run_state_available=False):
        yield wired


async def _collect_sse(response: Response) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


# ---------------------------------------------------------------------------
# KOD-61: fire, queue and dispatcher
# ---------------------------------------------------------------------------


async def test_fire_returns_202_with_exactly_the_job_handle(
    job_app: _JobApp,
) -> None:
    """POST /agent/fire returns 202 with exactly seven camelCase keys."""
    response = await job_app.client.post("/api/v1/agent/fire", json=_BODY)

    assert response.status_code == 202
    payload = response.json()
    assert set(payload) == {
        "jobId",
        "lane",
        "state",
        "queuePosition",
        "submittedAt",
        "statusUrl",
        "streamUrl",
    }
    assert payload["state"] == "queued"
    assert payload["lane"] == DEFAULT_LANE
    assert payload["queuePosition"] == 1
    submitted = datetime.fromisoformat(str(payload["submittedAt"]))
    assert submitted.tzinfo is not None
    assert submitted.utcoffset() is not None
    assert submitted.utcoffset().total_seconds() == 0
    job_id = payload["jobId"]
    assert payload["statusUrl"] == f"/api/v1/jobs/{job_id}"
    assert payload["streamUrl"] == f"/api/v1/jobs/{job_id}/stream"


async def test_fire_returns_before_the_engine_has_run() -> None:
    """Execution happens from the queue, never inside the HTTP request."""
    engine = GatedWorkflowEngine()
    async for wired in _build_app(engine):
        response = await wired.client.post("/api/v1/agent/fire", json=_BODY)
        assert response.status_code == 202
        job_id = response.json()["jobId"]

        # The POST returned while the run is still gated open.
        await _until(lambda: engine.started == ["fix"])
        record = await wired.queue.get(job_id=job_id)
        assert record is not None
        assert record.state is JobState.RUNNING
        assert engine.finished == []

        engine.release("fix")
        await _until(lambda: engine.finished == ["fix"])


async def test_fire_then_status_resolves_the_same_job_id(job_app: _JobApp) -> None:
    """Enqueue and status are symbiotic: the id fire returns, status answers for."""
    fired = await job_app.client.post("/api/v1/agent/fire", json=_BODY)
    job_id = fired.json()["jobId"]

    status = await job_app.client.get(f"/api/v1/jobs/{job_id}")

    assert status.status_code == 200
    assert status.json()["jobId"] == job_id


async def test_same_lane_fires_run_sequentially_in_arrival_order() -> None:
    """Two back-to-back fires at default config serialize in arrival order."""
    engine = GatedWorkflowEngine()
    async for wired in _build_app(engine):
        first = await wired.client.post(
            "/api/v1/agent/fire",
            json={"prompt": "first", "repoPath": "/tmp/fake"},
        )
        second = await wired.client.post(
            "/api/v1/agent/fire",
            json={"prompt": "second", "repoPath": "/tmp/fake"},
        )
        third = await wired.client.post(
            "/api/v1/agent/fire",
            json={"prompt": "third", "repoPath": "/tmp/fake"},
        )
        assert first.json()["queuePosition"] == 1
        # "first" already occupies the single worker, so the next two wait
        # in arrival order at 1-based positions 1 and 2.
        assert second.json()["queuePosition"] == 1
        assert third.json()["queuePosition"] == 2

        await _until(lambda: engine.started == ["first"])
        assert engine.started == ["first"], "no second run while the lane is busy"

        engine.release("first")
        await _until(lambda: engine.started == ["first", "second"])
        assert engine.finished == ["first"]
        engine.release("second")
        await _until(lambda: engine.started == ["first", "second", "third"])
        engine.release("third")
        await _until(lambda: engine.finished == ["first", "second", "third"])


async def test_distinct_lanes_do_not_block_each_other() -> None:
    """Lanes are open strings; a second lane interleaves with the first."""
    engine = GatedWorkflowEngine()
    queue = _make_queue(engine)
    await queue.start()
    try:
        await queue.submit(lane=DEFAULT_LANE, request=_request("workflow-job"))
        await queue.submit(lane="reports", request=_request("reports-job"))

        await _until(lambda: set(engine.started) == {"workflow-job", "reports-job"})

        engine.release("workflow-job")
        engine.release("reports-job")
        await _until(lambda: len(engine.finished) == 2)
    finally:
        await queue.stop()


async def test_concurrency_above_one_is_honored_and_warns_at_start() -> None:
    """A value above 1 interleaves the same lane AND warns from the dispatcher."""
    engine = GatedWorkflowEngine()
    queue = _make_queue(engine, max_concurrent_runs_per_lane=2)
    with structlog.testing.capture_logs() as logs:
        await queue.start()
    warnings = [
        entry for entry in logs if entry["event"] == "job_queue_concurrency_above_one"
    ]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["max_concurrent_runs_per_lane"] == 2

    try:
        await queue.submit(lane=DEFAULT_LANE, request=_request("a"))
        await queue.submit(lane=DEFAULT_LANE, request=_request("b"))
        await _until(lambda: set(engine.started) == {"a", "b"})
        engine.release("a")
        engine.release("b")
        await _until(lambda: len(engine.finished) == 2)
    finally:
        await queue.stop()


async def test_default_concurrency_emits_no_warning() -> None:
    """The default of 1 is the normal case and says nothing."""
    queue = _make_queue(GatedWorkflowEngine())
    with structlog.testing.capture_logs() as logs:
        await queue.start()
    await queue.stop()
    assert [e for e in logs if e["event"] == "job_queue_concurrency_above_one"] == []


async def test_full_lane_raises_queue_full_error() -> None:
    """Capacity is queue_max_depth_per_lane; overflow is a typed rejection."""
    engine = GatedWorkflowEngine()
    queue = _make_queue(engine, max_depth_per_lane=1)
    await queue.start()
    try:
        await queue.submit(lane=DEFAULT_LANE, request=_request("running"))
        await _until(lambda: engine.started == ["running"])
        await queue.submit(lane=DEFAULT_LANE, request=_request("queued"))
        with pytest.raises(QueueFullError, match="capacity"):
            await queue.submit(lane=DEFAULT_LANE, request=_request("rejected"))
    finally:
        engine.release("running")
        engine.release("queued")
        await queue.stop()


async def test_fire_into_a_full_lane_returns_429() -> None:
    """The fire endpoint maps QueueFullError to 429 with a typed body."""
    engine = GatedWorkflowEngine()
    async for wired in _build_app(engine, max_depth_per_lane=1):
        await wired.client.post("/api/v1/agent/fire", json=_BODY)
        await _until(lambda: engine.started == ["fix"])
        await wired.client.post("/api/v1/agent/fire", json=_BODY)
        rejected = await wired.client.post("/api/v1/agent/fire", json=_BODY)

        assert rejected.status_code == 429
        body = rejected.json()
        assert body["success"] is False
        assert "capacity" in str(body["error"])
        engine.release("fix")


async def test_registry_reports_queued_running_terminal_transitions() -> None:
    """Read through the JobRegistry port directly, without any HTTP."""
    engine = GatedWorkflowEngine()
    queue = _make_queue(engine)
    await queue.start()
    try:
        first = await queue.submit(lane=DEFAULT_LANE, request=_request("first"))
        await _until(lambda: engine.started == ["first"])
        await queue.submit(lane=DEFAULT_LANE, request=_request("second"))
        third = await queue.submit(lane=DEFAULT_LANE, request=_request("third"))

        queued = await queue.get(job_id=third.job_id)
        assert queued is not None
        assert queued.state is JobState.QUEUED
        assert queued.queue_position == 2

        running = await queue.get(job_id=first.job_id)
        assert running is not None
        assert running.state is JobState.RUNNING
        assert running.queue_position is None

        engine.release("first")
        await _until(lambda: engine.started == ["first", "second"])

        # The still-queued job is re-indexed to 1-based position 1.
        promoted = await queue.get(job_id=third.job_id)
        assert promoted is not None
        assert promoted.state is JobState.QUEUED
        assert promoted.queue_position == 1

        terminal = await queue.get(job_id=first.job_id)
        assert terminal is not None
        assert terminal.state is JobState.TERMINAL
        assert terminal.queue_position is None
    finally:
        engine.release("second")
        engine.release("third")
        await queue.stop()


async def test_dispatcher_writes_the_terminal_outcome_onto_the_record() -> None:
    """JobRecord.outcome equals the outcome on the terminal event."""
    engine = ChattyWorkflowEngine([_complete_event(WorkflowOutcome.ci_passed)])
    queue = _make_queue(engine)
    await queue.start()
    try:
        record = await queue.submit(lane=DEFAULT_LANE, request=_request("fix"))
        await _drain(queue, record.job_id)
        final = await queue.get(job_id=record.job_id)
        assert final is not None
        assert final.outcome is WorkflowOutcome.ci_passed
    finally:
        await queue.stop()


async def test_job_id_is_the_langgraph_thread_id(checkpointed_app: _JobApp) -> None:
    """The job's id addresses its own checkpoint."""
    fired = await checkpointed_app.client.post("/api/v1/agent/fire", json=_BODY)
    job_id = fired.json()["jobId"]

    await _wait_terminal(checkpointed_app.queue, job_id)

    status = await checkpointed_app.client.get(f"/api/v1/jobs/{job_id}")
    assert status.json()["run"] is not None


async def test_replay_buffer_overflow_marks_the_record_truncated() -> None:
    """Dropping the oldest frame is never a silent gap."""
    events: list[AgentEvent] = [
        AssistantTextEvent(text=f"line {index}", model="m") for index in range(4)
    ]
    engine = ChattyWorkflowEngine(events)
    queue = _make_queue(engine, event_buffer_capacity=1)
    await queue.start()
    try:
        record = await queue.submit(lane=DEFAULT_LANE, request=_request("fix"))
        with structlog.testing.capture_logs() as logs:
            await _drain(queue, record.job_id)
        final = await queue.get(job_id=record.job_id)
        assert final is not None
        assert final.truncated is True
        truncation_logs = [
            entry for entry in logs if entry["event"] == "job_event_buffer_truncated"
        ]
        assert len(truncation_logs) == 1
    finally:
        await queue.stop()


async def test_terminal_records_are_evicted_after_the_retention_window() -> None:
    """The registry cannot grow without bound."""
    engine = ChattyWorkflowEngine([_complete_event(WorkflowOutcome.ci_passed)])
    queue = _make_queue(
        engine,
        terminal_retention_seconds=0.05,
        event_buffer_retention_seconds=0.05,
    )
    await queue.start()
    try:
        record = await queue.submit(lane=DEFAULT_LANE, request=_request("fix"))
        await _drain(queue, record.job_id)
        assert await queue.get(job_id=record.job_id) is not None
        await asyncio.sleep(0.15)
        assert await queue.get(job_id=record.job_id) is None
    finally:
        await queue.stop()


# ---------------------------------------------------------------------------
# KOD-61: retention is two independent windows (founder ruling 2026-08-08)
# ---------------------------------------------------------------------------


async def test_buffer_is_dropped_while_the_record_is_still_retained() -> None:
    """The expensive buffer goes early; the cheap record outlives it."""
    engine = ChattyWorkflowEngine(
        [
            AssistantTextEvent(text="expensive frame", model="m"),
            _complete_event(WorkflowOutcome.ci_passed),
        ],
    )
    queue = _make_queue(
        engine,
        terminal_retention_seconds=30.0,
        event_buffer_retention_seconds=0.05,
    )
    await queue.start()
    try:
        record = await queue.submit(lane=DEFAULT_LANE, request=_request("fix"))
        assert len(await _drain(queue, record.job_id)) == 2

        # Before the buffer window elapses: frames still replayable.
        assert len(await _drain(queue, record.job_id)) == 2

        with structlog.testing.capture_logs() as logs:
            await asyncio.sleep(0.15)

        # Buffer gone, record still answerable through the registry.
        assert await _drain(queue, record.job_id) == []
        surviving = await queue.get(job_id=record.job_id)
        assert surviving is not None
        assert surviving.outcome is WorkflowOutcome.ci_passed
        # Dropped frames are never a silent gap.
        assert surviving.truncated is True
        drops = [e for e in logs if e["event"] == "job_event_buffer_dropped"]
        assert len(drops) == 1
        assert drops[0]["frames"] == 2
    finally:
        await queue.stop()


async def test_zero_buffer_retention_drops_the_buffer_at_terminal() -> None:
    """0.0 is legal: the buffer goes as soon as the job goes terminal."""
    engine = ChattyWorkflowEngine([_complete_event(WorkflowOutcome.ci_passed)])
    queue = _make_queue(
        engine,
        terminal_retention_seconds=30.0,
        event_buffer_retention_seconds=0.0,
    )
    await queue.start()
    try:
        record = await queue.submit(lane=DEFAULT_LANE, request=_request("fix"))
        await _wait_terminal(queue, record.job_id)
        await _wait_truncated(queue, record.job_id)
        assert await _drain(queue, record.job_id) == []
    finally:
        await queue.stop()


def test_retention_defaults_are_the_ruled_windows() -> None:
    """24h for the record, 15 minutes for the buffer, from AppConfig."""
    config = AppConfig()
    assert config.queue_terminal_retention_seconds == 86400.0
    assert config.queue_event_buffer_retention_seconds == 900.0
    assert config.queue_event_buffer_capacity == 512
    assert config.queue_max_depth_per_lane == 64
    assert config.queue_max_concurrent_runs_per_lane == 1


def test_each_retention_field_says_which_object_it_governs() -> None:
    """The separation is the point, so it is legible at the config surface."""
    fields = AppConfig.model_fields
    record_description = fields["queue_terminal_retention_seconds"].description
    buffer_description = fields["queue_event_buffer_retention_seconds"].description
    assert record_description is not None
    assert buffer_description is not None
    assert "JOB RECORD" in record_description
    assert "REPLAY BUFFER" in buffer_description


def test_a_buffer_outliving_its_record_is_rejected_at_boot() -> None:
    """Incoherent config fails loud rather than being clamped."""
    with pytest.raises(ValidationError, match="cannot outlive"):
        AppConfig(
            queue_terminal_retention_seconds=120.0,
            queue_event_buffer_retention_seconds=121.0,
        )


def test_equal_retention_windows_are_accepted() -> None:
    """The bound is <=, not <: a buffer may live exactly as long."""
    config = AppConfig(
        queue_terminal_retention_seconds=120.0,
        queue_event_buffer_retention_seconds=120.0,
    )
    assert config.queue_event_buffer_retention_seconds == 120.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("queue_terminal_retention_seconds", 59.0),
        ("queue_terminal_retention_seconds", 604801.0),
        ("queue_event_buffer_retention_seconds", -1.0),
        ("queue_event_buffer_retention_seconds", 86401.0),
    ],
)
def test_retention_bounds_are_enforced(field: str, value: float) -> None:
    """Both windows carry the ruled bounds."""
    with pytest.raises(ValidationError):
        AppConfig(**{field: value})


async def test_stop_marks_in_flight_jobs_terminal_and_leaves_no_worker() -> None:
    """Shutdown stops accepting, cancels workers, terminates in-flight jobs."""
    engine = GatedWorkflowEngine()
    queue = _make_queue(engine)
    await queue.start()
    in_flight = await queue.submit(lane=DEFAULT_LANE, request=_request("hanging"))
    queued = await queue.submit(lane=DEFAULT_LANE, request=_request("dropped"))
    await _until(lambda: engine.started == ["hanging"])

    await queue.stop()

    for record_id in (in_flight.job_id, queued.job_id):
        record = await queue.get(job_id=record_id)
        assert record is not None
        assert record.state is JobState.TERMINAL
    assert _worker_tasks(queue) == []

    with pytest.raises(QueueFullError, match="not accepting"):
        await queue.submit(lane=DEFAULT_LANE, request=_request("after-stop"))


async def test_lifespan_wires_and_stops_the_dispatcher() -> None:
    """AsyncioJobQueue and the checkpointer are lifespan-managed."""
    app = create_app()
    async with app.router.lifespan_context(app):
        queue = app.state.job_queue
        assert isinstance(queue, AsyncioJobQueue)
        assert hasattr(app.state, "checkpointer")
        assert hasattr(app.state, "job_service")
        assert len(_worker_tasks(queue)) == 1
    assert _worker_tasks(queue) == []


# ---------------------------------------------------------------------------
# KOD-61: /agent/workflow wire shape and attachable stream
# ---------------------------------------------------------------------------


async def test_workflow_leads_with_a_job_accepted_frame(job_app: _JobApp) -> None:
    """The first SSE frame carries the reconnect handle."""
    async with job_app.client.stream(
        "POST",
        "/api/v1/agent/workflow",
        json=_BODY,
    ) as response:
        assert response.status_code == 200
        events = await _collect_sse(response)

    first = events[0]
    assert first["type"] == "job_accepted"
    assert set(first) == {
        "type",
        "jobId",
        "lane",
        "queuePosition",
        "statusUrl",
        "streamUrl",
    }
    assert first["lane"] == DEFAULT_LANE
    assert first["queuePosition"] == 1
    job_id = first["jobId"]
    assert first["statusUrl"] == f"/api/v1/jobs/{job_id}"
    assert first["streamUrl"] == f"/api/v1/jobs/{job_id}/stream"
    # Everything after the leading frame is the run's own output.
    assert [e["type"] for e in events[1:]].count("job_accepted") == 0
    terminal = next(e for e in events if e["type"] == "workflow_complete")
    # `outcome` is required and non-nullable, so it survives exclude_none.
    assert terminal["outcome"] == WorkflowOutcome.review_passed_no_pr_adapter.value


async def test_jobs_stream_replays_the_buffer_then_goes_live() -> None:
    """Submit, let frames accumulate, then attach and see them replayed."""
    engine = GatedWorkflowEngine(
        events=[
            AssistantTextEvent(text="early", model="m"),
            _complete_event(WorkflowOutcome.ci_passed),
        ],
    )
    async for wired in _build_app(engine):
        fired = await wired.client.post("/api/v1/agent/fire", json=_BODY)
        job_id = fired.json()["jobId"]
        await _until(lambda: engine.started == ["fix"])
        engine.release("fix")
        await _until(lambda: engine.finished == ["fix"])

        async with wired.client.stream(
            "GET",
            f"/api/v1/jobs/{job_id}/stream",
        ) as response:
            assert response.status_code == 200
            events = await _collect_sse(response)

        assert [e["type"] for e in events] == ["assistant_text", "workflow_complete"]


async def test_jobs_stream_unknown_job_is_404(job_app: _JobApp) -> None:
    response = await job_app.client.get("/api/v1/jobs/nope/stream")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# KOD-49: job status endpoint
# ---------------------------------------------------------------------------


async def test_status_of_a_queued_job_reports_1_based_position() -> None:
    engine = GatedWorkflowEngine()
    async for wired in _build_app(engine):
        await wired.client.post("/api/v1/agent/fire", json=_BODY)
        await _until(lambda: engine.started == ["fix"])
        second = await wired.client.post("/api/v1/agent/fire", json=_BODY)
        job_id = second.json()["jobId"]

        status = await wired.client.get(f"/api/v1/jobs/{job_id}")

        assert status.status_code == 200
        payload = status.json()
        assert payload["state"] == "queued"
        assert payload["queuePosition"] == 1
        engine.release("fix")


async def test_status_of_a_running_job_reports_checkpointed_progress(
    checkpointed_app: _JobApp,
) -> None:
    """lastCompletedNode plus iteration, fix-round and CI progress."""
    fired = await checkpointed_app.client.post("/api/v1/agent/fire", json=_BODY)
    job_id = fired.json()["jobId"]
    await _wait_terminal(checkpointed_app.queue, job_id)

    payload = (await checkpointed_app.client.get(f"/api/v1/jobs/{job_id}")).json()

    assert set(payload) == {
        "jobId",
        "lane",
        "state",
        "queuePosition",
        "submittedAt",
        "outcome",
        "truncated",
        "runStateAvailable",
        "run",
    }
    assert payload["runStateAvailable"] is True
    run = payload["run"]
    assert run["lastCompletedNode"] == "complete"
    assert run["totalIterations"] == 1
    assert run["fixRoundsUsed"] == 0
    assert run["ciPassed"] is None
    assert run["ciSummary"] is None
    assert run["featureBranch"].startswith("kodezart/")
    assert "-ralph-" in run["ralphBranch"]
    assert "currentNode" not in run


async def test_status_reports_progress_while_the_graph_is_paused_mid_run() -> None:
    """A checkpoint read mid-graph names an intermediate node, not the last.

    The engine is a real ``RalphWorkflowEngine``; its quality gate blocks
    on the fix round, so the graph is genuinely suspended inside
    ``fix_code`` while the status endpoint answers.
    """
    saver = InMemorySaver()
    gate = GatedQualityGate(gate_on_call=2)
    async for wired in _build_app(
        _mid_run_engine(saver, gate),
        checkpointer=saver,
    ):
        fired = await wired.client.post("/api/v1/agent/fire", json=_BODY)
        job_id = fired.json()["jobId"]

        # Suspended inside fix_code: the loop and the failed review are
        # committed, the fix round is not.
        await _until(gate.entered.is_set)

        payload = (await wired.client.get(f"/api/v1/jobs/{job_id}")).json()
        assert payload["state"] == "running"
        assert payload["outcome"] is None
        assert payload["runStateAvailable"] is True
        run = payload["run"]
        assert run is not None
        assert run["lastCompletedNode"] == "review_against_ticket"
        assert run["lastCompletedNode"] != "complete"
        assert run["totalIterations"] == 1
        assert run["fixRoundsUsed"] == 0
        assert run["ciPassed"] is None
        assert run["reviewPassed"] is False
        assert run["merged"] is True
        assert run["featureBranch"].startswith("kodezart/")

        # Releasing the gate advances exactly those counters, which is
        # what proves the read above was mid-flight and not a final state.
        gate.release()
        await _wait_terminal(wired.queue, job_id)

        final = (await wired.client.get(f"/api/v1/jobs/{job_id}")).json()
        assert final["state"] == "terminal"
        assert final["run"]["lastCompletedNode"] == "complete"
        assert final["run"]["fixRoundsUsed"] == 1
        assert final["run"]["totalIterations"] == 2
        assert final["run"]["reviewPassed"] is True


async def test_status_of_a_terminal_job_carries_the_outcome_discriminator(
    checkpointed_app: _JobApp,
) -> None:
    fired = await checkpointed_app.client.post("/api/v1/agent/fire", json=_BODY)
    job_id = fired.json()["jobId"]
    await _wait_terminal(checkpointed_app.queue, job_id)

    payload = (await checkpointed_app.client.get(f"/api/v1/jobs/{job_id}")).json()

    assert payload["state"] == "terminal"
    assert payload["outcome"] == WorkflowOutcome.review_passed_no_pr_adapter.value
    assert payload["truncated"] is False
    assert payload["run"]["prUrl"] is None
    assert payload["run"]["prNumber"] is None


async def test_checkpointerless_deployment_answers_run_state_unavailable(
    checkpointerless_app: _JobApp,
) -> None:
    """No checkpointer configured: runStateAvailable false, run null, status 200."""
    fired = await checkpointerless_app.client.post("/api/v1/agent/fire", json=_BODY)
    job_id = fired.json()["jobId"]

    status = await checkpointerless_app.client.get(f"/api/v1/jobs/{job_id}")

    assert status.status_code == 200
    payload = status.json()
    assert payload["runStateAvailable"] is False
    assert payload["run"] is None


async def test_checkpointed_job_with_no_checkpoint_yet_is_a_distinct_answer() -> None:
    """Checkpointer present but this job has no checkpoint: true + null, 200."""
    engine = GatedWorkflowEngine()
    async for wired in _build_app(engine, checkpointer=InMemorySaver()):
        fired = await wired.client.post("/api/v1/agent/fire", json=_BODY)
        job_id = fired.json()["jobId"]

        status = await wired.client.get(f"/api/v1/jobs/{job_id}")

        assert status.status_code == 200
        payload = status.json()
        assert payload["runStateAvailable"] is True
        assert payload["run"] is None
        engine.release("fix")


async def test_status_of_an_unknown_job_is_404_with_a_typed_body(
    job_app: _JobApp,
) -> None:
    response = await job_app.client.get("/api/v1/jobs/missing-id")

    assert response.status_code == 404
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"] == "job not found: missing-id"


# ---------------------------------------------------------------------------
# KOD-49: one shared thread-id derivation, one LangGraph read seam
# ---------------------------------------------------------------------------

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "kodezart"
_DERIVATION_MODULE = "domain/thread_id.py"
_READER_MODULE = "adapters/langgraph_run_state_reader.py"


def _sources() -> dict[str, str]:
    """Every module under ``src/kodezart``, keyed by package-relative path."""
    return {
        path.relative_to(_SRC_ROOT).as_posix(): path.read_text()
        for path in sorted(_SRC_ROOT.rglob("*.py"))
    }


def test_thread_ids_come_from_one_shared_derivation() -> None:
    """The suffix rule is written once and called from both sides."""
    assert workflow_thread_id("j0b") == "j0b"
    assert ralph_thread_id("j0b") == "j0b-ralph"
    assert ticket_thread_id("j0b") == "j0b-ticket"

    callers = {
        name
        for name, text in _sources().items()
        if "thread_id(" in text and name != _DERIVATION_MODULE
    }
    assert callers == {
        _READER_MODULE,
        "chains/ralph_workflow.py",
        "chains/ralph_loop.py",
        "chains/ticket_generation.py",
    }


def test_no_module_interpolates_a_thread_id_of_its_own() -> None:
    """Three modules each building their own suffix is what this forbids."""
    offenders = [
        f"{name}: {line.strip()}"
        for name, text in _sources().items()
        if name != _DERIVATION_MODULE
        for line in text.splitlines()
        if "thread_id" in line and 'f"' in line
    ]
    assert offenders == []


def test_the_derivation_is_pure() -> None:
    """No I/O, no LangGraph, no config — so a chain can call it inward."""
    text = _sources()[_DERIVATION_MODULE]
    imports = [
        line
        for line in text.splitlines()
        if line.startswith(("import ", "from ")) or " import " in line
    ]
    assert imports == []


def test_aget_tuple_appears_only_in_the_run_state_reader() -> None:
    """The reader is the only seam onto LangGraph checkpoint reads."""
    readers = {name for name, text in _sources().items() if "aget_tuple" in text}
    assert readers == {_READER_MODULE}


def test_neither_service_nor_reader_branches_on_checkpointer_backend() -> None:
    """Backend independence is static: no postgres/memory branch either side."""
    sources = _sources()
    for module in ("services/job_service.py", _READER_MODULE):
        lowered = sources[module].lower()
        assert "postgres" not in lowered
        assert ":memory:" not in lowered


# ---------------------------------------------------------------------------
# KOD-52: one queue, two producers, never a second dispatch path
# ---------------------------------------------------------------------------

_ADAPTER_MODULE = "adapters/asyncio_job_queue.py"
_WIRING_MODULE = "main.py"


def test_exactly_one_lane_queue_construction_site() -> None:
    """One dispatcher owns the only per-lane asyncio.Queue."""
    sites = [
        f"{name}:{number}"
        for name, text in _sources().items()
        for number, line in enumerate(text.splitlines(), start=1)
        if "asyncio.Queue(" in line
    ]
    assert all(site.startswith(f"{_ADAPTER_MODULE}:") for site in sites), sites
    lane_queues = [
        line
        for name, text in _sources().items()
        for line in text.splitlines()
        if "asyncio.Queue(maxsize=" in line
    ]
    assert len(lane_queues) == 1


def test_a_workflow_run_is_only_ever_started_by_the_dispatcher() -> None:
    """No execution entry point for a run outside the dispatcher."""
    starters = {
        name
        for name, text in _sources().items()
        if "engine.run(" in text or "_engine.run(" in text
    }
    assert starters == {_ADAPTER_MODULE}


def test_producers_depend_on_the_ports_never_on_the_adapter() -> None:
    """Endpoints, handlers and services name the protocols only."""
    users = {
        name
        for name, text in _sources().items()
        if "AsyncioJobQueue" in text and name != _ADAPTER_MODULE
    }
    assert users == {_WIRING_MODULE}

    for name, text in _sources().items():
        if not name.startswith(("api/", "handlers/", "services/")):
            continue
        assert "asyncio_job_queue" not in text, name


def test_run_state_is_its_own_leaf_module_not_a_third_job_type() -> None:
    """job.py answers for the handle; run.py answers for the execution."""
    own_types = {
        name
        for name, value in vars(job_types).items()
        if isinstance(value, type) and value.__module__ == job_types.__name__
    }
    assert own_types == {"JobState", "JobRecord"}
    assert RunState.__module__ == "kodezart.types.domain.run"


async def test_one_adapter_satisfies_both_ports() -> None:
    """JobQueue and JobRegistry are ISP-split but share one implementation."""
    queue = _make_queue(ChattyWorkflowEngine([]))
    assert isinstance(queue, JobQueue)
    assert isinstance(queue, JobRegistry)


async def test_attach_yields_domain_events_never_serialized_dicts() -> None:
    """The port's contract is AgentEvent; serialization happens in the handler."""
    engine = ChattyWorkflowEngine(
        [
            AssistantTextEvent(text="hello", model="m"),
            _complete_event(WorkflowOutcome.ci_passed),
        ],
    )
    queue = _make_queue(engine)
    await queue.start()
    try:
        record = await queue.submit(lane=DEFAULT_LANE, request=_request("fix"))
        events = await _drain(queue, record.job_id)
        assert [type(event) for event in events] == [
            AssistantTextEvent,
            WorkflowCompleteEvent,
        ]
    finally:
        await queue.stop()


async def test_truncation_flag_reaches_the_status_payload() -> None:
    """A truncated replay buffer is visible on the job status response."""
    engine = ChattyWorkflowEngine(
        [AssistantTextEvent(text=f"line {index}", model="m") for index in range(4)],
    )
    app = create_app()
    app.state.skills = SUPPRESS_ALL_SKILLS
    app.state.agent_service = AgentService(
        executor=FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    app.state.workflow_engine = engine
    queue = _make_queue(engine, event_buffer_capacity=1)
    app.state.job_queue = queue
    await queue.start()
    app.state.job_service = JobService(registry=queue, run_state_reader=None)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            fired = await client.post("/api/v1/agent/fire", json=_BODY)
            job_id = fired.json()["jobId"]
            await _wait_terminal(queue, job_id)
            payload = (await client.get(f"/api/v1/jobs/{job_id}")).json()
            assert payload["truncated"] is True
    finally:
        await queue.stop()
