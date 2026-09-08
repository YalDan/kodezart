"""Shared timing at actual HTTP, MCP and content-session boundaries."""

import asyncio
from random import Random

import httpx
import pytest
import structlog

from kodezart.composition.forge import build_forge_client
from kodezart.composition.gating import build_outbound_gate
from kodezart.composition.tracker import build_tracker
from kodezart.core.backoff import RetryPolicy
from kodezart.core.config import AppConfig
from kodezart.core.errors import McpCallUnansweredError, McpCredentialRefusedError
from kodezart.core.logging import get_logger
from kodezart.domain.errors import ForgeAPIError, RateLimitError, TransientAPIError
from kodezart.types.domain.agent import RateLimitWarningEvent
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
    ScanFailureKind,
    WriterShape,
)
from kodezart.types.domain.privacy import PrivateSurface
from kodezart.types.domain.session import SessionFailureKind
from tests.adapters.test_ci_rerun import REPO, SHA, ActionsAPI
from tests.adapters.test_github_api import _make_client
from tests.adapters.test_judgment_scanner import (
    FIXTURE_PRIVATE_SURFACE,
    NO_SKILLS,
    PROSE,
    ScriptedAuditExecutor,
    audit_result,
    scanner_for,
)
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_fire_dispatcher import operation_config
from tests.tracker.conftest import CLAIMED_ISSUE, fixture_server
from tests.tracker.test_linear_mcp_tracker import tracker_over

FIXTURE_TOKEN = "fixture"


@pytest.fixture
def waits(monkeypatch):
    delays = []
    original = asyncio.sleep

    async def record(seconds):
        if seconds:
            delays.append(seconds)
        await original(0)

    monkeypatch.setattr(asyncio, "sleep", record)
    return delays


class MidpointRandom(Random):
    def __init__(self):
        super().__init__(0)
        self.calls = []

    def uniform(self, low, high):
        self.calls.append((low, high))
        return (low + high) / 2


async def create_pr(client):
    return await client.create_pr(
        repo_url=REPO, title="Change", body="Body", head="feature", base="trunk"
    )


async def test_github_header_replaces_backoff_and_final_failure_keeps_rng_logging(
    waits,
):
    responses = iter(
        [
            httpx.Response(429, headers={"retry-after": "0.25"}),
            httpx.Response(503),
            httpx.Response(
                429, headers={"retry-after": "7", "x-ratelimit-reset": "12"}
            ),
        ]
    )
    calls = []

    def respond(request):
        calls.append(request)
        return next(responses)

    client = _make_client(respond, max_retries=2, retry_backoff_factor=2)
    rng = MidpointRandom()
    client._rng = rng
    try:
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(RateLimitError) as err,
        ):
            await create_pr(client)
    finally:
        await client._client.aclose()
    assert len(calls) == 3
    assert waits == pytest.approx([0.2625, 4.2])
    assert [low for low, _ in rng.calls] == [0, 0, 0]
    assert [high for _, high in rng.calls] == pytest.approx([0.025, 0.4, 0.7])
    assert err.value.retry_after == 7 and err.value.resets_at == 12
    retries = [entry for entry in logs if entry["event"] == "github_api_retry"]
    assert [row["attempt"] for row in retries] == [1, 2, 3]
    assert retries[-1]["wait_seconds"] == pytest.approx(7.35)


@pytest.mark.parametrize("failure", ["server", "transport"])
async def test_github_policy_factor_and_jitter_drive_actual_retries(failure, waits):
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) < 3:
            if failure == "transport":
                raise httpx.ReadError("temporary", request=request)
            return httpx.Response(503)
        return httpx.Response(201, json={"html_url": REPO + "/pull/1", "number": 1})

    client = _make_client(
        respond, retry=RetryPolicy(attempts=3, initial_delay=2, factor=3, jitter=0.5)
    )
    rng = MidpointRandom()
    client._rng = rng
    try:
        assert await create_pr(client) == (REPO + "/pull/1", 1)
    finally:
        await client._client.aclose()
    assert len(calls) == 3
    assert waits == [2.5, 7.5]
    assert rng.calls == [(0, 1), (0, 3)]


@pytest.mark.parametrize("failure", ["forbidden", "malformed", "invalid_url"])
async def test_github_permanent_failures_do_not_spend_retry_budget(failure, waits):
    calls = []

    def respond(request):
        calls.append(request)
        if failure == "invalid_url":
            raise httpx.InvalidURL("invalid fixture")
        if failure == "forbidden":
            return httpx.Response(403)
        return httpx.Response(201, json={"wrong": "shape"})

    client = _make_client(respond, max_retries=4)
    try:
        with pytest.raises(ForgeAPIError):
            await create_pr(client)
    finally:
        await client._client.aclose()
    assert len(calls) == 1
    assert waits == []


@pytest.mark.parametrize("failure", ["server", "unanswered"])
async def test_actions_rerun_post_is_not_replayed(failure, waits):
    server = ActionsAPI()

    def respond(request):
        answer = server(request)
        if request.method == "POST":
            if failure == "unanswered":
                raise httpx.ReadError("answer lost", request=request)
            return httpx.Response(503)
        return answer

    client = _make_client(respond, max_retries=4)
    try:
        with pytest.raises(TransientAPIError):
            await client.rerun_checks(repo_url=REPO, ref=SHA)
    finally:
        await client._client.aclose()
    assert len(server.writes) == 1
    assert server.attempts[101] == 2
    assert waits == []


@pytest.mark.parametrize("attempts", [1, 3])
async def test_linear_policy_counts_total_attempts_and_backoff(attempts, waits):
    server = fixture_server()
    server._transient_failures["get_issue"] = 10
    tracker = tracker_over(
        server, retry=RetryPolicy(attempts=attempts, initial_delay=0.5, factor=3)
    )
    with pytest.raises(TransientAPIError):
        await tracker.read_issue(issue_key=CLAIMED_ISSUE)
    assert len(server.tool_calls("get_issue")) == attempts
    assert waits == ([] if attempts == 1 else [0.5, 1.5])


@pytest.mark.parametrize("kind", ["credential", "unanswered_write"])
async def test_linear_permanent_or_unsafe_retry_refuses_immediately(kind, waits):
    server = fixture_server()
    calls = []

    class RefusingCaller:
        async def call_tool(self, *, name, arguments):
            calls.append((name, arguments))
            if kind == "credential":
                raise McpCredentialRefusedError("refused", server_name="fixture")
            raise McpCallUnansweredError("lost", server_name="fixture")

    tracker = tracker_over(server, caller=RefusingCaller(), max_retries=4)
    expected = (
        McpCredentialRefusedError if kind == "credential" else McpCallUnansweredError
    )
    with pytest.raises(expected):
        await tracker.post_comment(issue_key=CLAIMED_ISSUE, body="one mutation")
    assert len(calls) == 1
    assert waits == []


@pytest.mark.parametrize("kind", ["timeout", "rate_limit", "transport"])
async def test_content_scanner_shared_policy_retries_only_typed_transient_results(
    kind, waits
):
    executor = ScriptedAuditExecutor(
        [RateLimitWarningEvent(status="rejected"), audit_result([])]
        if kind == "rate_limit"
        else [],
        raises={"transport": OSError("temporary"), "timeout": TimeoutError()}.get(kind),
    )
    scanner = scanner_for(
        executor, retry=RetryPolicy(attempts=3, initial_delay=0.25, factor=3)
    )
    result = await scanner.scan(content=PROSE, destination=OutboundDestination.PR_BODY)
    assert result.failure in {
        ScanFailureKind.TIMEOUT,
        ScanFailureKind.RATE_LIMITED,
        ScanFailureKind.TRANSPORT_ERROR,
    }
    assert len(executor.calls) == 3
    assert waits == [0.25, 0.75]


@pytest.mark.parametrize("subtype", ["refusal", "budget_exhausted"])
async def test_content_scanner_permanent_result_never_retries(subtype, waits):
    executor = ScriptedAuditExecutor(
        [
            audit_result(None, failure_kind=SessionFailureKind.REFUSAL)
            if subtype == "refusal"
            else audit_result(
                None, is_error=True, failure_kind=SessionFailureKind.BUDGET_EXHAUSTED
            )
        ]
    )
    scanner = scanner_for(executor, retry_max_attempts=4)
    result = await scanner.scan(content=PROSE, destination=OutboundDestination.PR_BODY)
    assert result.failure in {ScanFailureKind.REFUSAL, ScanFailureKind.BUDGET_EXHAUSTED}
    assert len(executor.calls) == 1
    assert waits == []


async def test_each_content_attempt_has_its_own_timeout_and_closes_stream(waits):
    closed = []

    class TimedExecutor(ScriptedAuditExecutor):
        async def _emit(self):
            try:
                await asyncio.Event().wait()
                yield audit_result([])
            finally:
                closed.append(len(self.calls))

    executor = TimedExecutor([])
    scanner = scanner_for(executor, retry_max_attempts=3, timeout_seconds=0.01)
    result = await scanner.scan(content=PROSE, destination=OutboundDestination.PR_BODY)
    assert result.failure is ScanFailureKind.TIMEOUT
    assert len(executor.calls) == 3
    assert closed == [1, 2, 3]
    assert waits == [0.01, 0.02]


@pytest.mark.parametrize("boundary", ["github", "linear", "content"])
@pytest.mark.parametrize("phase", ["attempt", "backoff"])
async def test_cancellation_does_not_spend_another_attempt(
    boundary, phase, monkeypatch
):
    entered = asyncio.Event()
    calls = []

    async def fail_or_wait():
        calls.append(boundary)
        if phase == "attempt":
            entered.set()
            await asyncio.Event().wait()

    async def paused_sleep(seconds):
        if seconds:
            entered.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(asyncio, "sleep", paused_sleep)
    client = None
    if boundary == "github":

        async def respond(request):
            await fail_or_wait()
            return httpx.Response(503)

        client = _make_client(respond, max_retries=4)
        action = create_pr(client)
    elif boundary == "linear":

        class Caller:
            async def call_tool(self, *, name, arguments):
                await fail_or_wait()
                raise TransientAPIError("temporary")

        tracker = tracker_over(
            fixture_server(), caller=Caller(), max_retries=4, retry_backoff_factor=1
        )
        action = tracker.read_issue(issue_key=CLAIMED_ISSUE)
    else:

        class Executor(ScriptedAuditExecutor):
            async def _emit(self):
                await fail_or_wait()
                raise OSError("temporary")
                yield audit_result([])

        scanner = scanner_for(Executor([]), retry_max_attempts=4)
        action = scanner.scan(content=PROSE, destination=OutboundDestination.PR_BODY)
    task = asyncio.create_task(action)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert calls == [boundary]
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if client is not None:
            await client._client.aclose()


async def test_composed_forge_preserves_operator_retry_units(monkeypatch, waits):
    calls = []
    original_client = httpx.AsyncClient

    def respond(request):
        calls.append(request)
        return httpx.Response(503)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond), **kwargs
        ),
    )
    client = build_forge_client(
        config=AppConfig(
            github_token=FIXTURE_TOKEN,
            forge_api_max_retries=2,
            forge_api_retry_backoff_factor=0.5,
        )
    )
    assert client is not None
    client._rng = MidpointRandom()
    try:
        with pytest.raises(TransientAPIError):
            await create_pr(client)
    finally:
        await client._client.aclose()
    assert len(calls) == 3
    assert waits == [0.525, 1.05]


async def test_composed_tracker_preserves_operator_retry_units(waits):
    server = fixture_server()
    server._transient_failures["get_issue"] = 10
    config = AppConfig(
        tracker={"max_retries": 2, "retry_backoff_factor": 0.5},
    )
    tracker, _ = build_tracker(
        backend=config.tracker.backend,
        retry=RetryPolicy(
            attempts=config.tracker.max_retries + 1,
            initial_delay=config.tracker.retry_backoff_factor,
        ),
        operation=operation_config(),
        caller=server,
    )
    with pytest.raises(TransientAPIError):
        await tracker.read_issue(issue_key=CLAIMED_ISSUE)
    assert len(server.tool_calls("get_issue")) == 3
    assert waits == [0.5, 1]


async def test_composed_content_scanner_preserves_total_attempt_units(waits, tmp_path):
    executor = ScriptedAuditExecutor([], raises=OSError("temporary"))
    gate = await build_outbound_gate(
        config=AppConfig(
            agentic_content_scanner_enabled=True,
            content_scan_retry_max_attempts=3,
            content_scan_retry_initial_interval=0.5,
            content_audit_working_dir=str(tmp_path),
        ),
        operation=operation_config().model_copy(
            update={
                "private_surface": PrivateSurface(description=FIXTURE_PRIVATE_SURFACE)
            }
        ),
        executor=executor,
        prompts=load_registry(bindings={"private_surface": FIXTURE_PRIVATE_SURFACE}),
        skills=NO_SKILLS,
        log=get_logger(__name__),
    )
    result = await gate.gate(
        content=PROSE,
        destination=OutboundDestination.PR_BODY,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        content_class=ContentClass.AUTHORED,
    )
    assert result.failure is ScanFailureKind.TRANSPORT_ERROR
    assert len(executor.calls) == 3
    assert waits == [0.5, 1]
