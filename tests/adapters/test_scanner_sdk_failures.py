"""Native SDK failures meet the actual scanner's typed, bounded contract."""

import asyncio

import pytest
from claude_agent_sdk import (
    ClaudeSDKError,
    CLIConnectionError,
    CLINotFoundError,
    ProcessError,
    RateLimitEvent,
    RateLimitInfo,
    ResultError,
    ResultMessage,
)

from kodezart.adapters.claude_agent_executor import ClaudeAgentExecutor
from kodezart.adapters.claude_client_executor import ClaudeClientExecutor
from kodezart.core.backoff import RetryPolicy
from kodezart.domain.errors import AgentSDKError, OutboundContentBlockedError
from kodezart.types.domain.gating import (
    ContentClass,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from tests.adapters.test_judgment_scanner import scanner_for
from tests.fakes import DEFAULT_SETTING_SOURCES, NO_KNOWLEDGE_GRANT
from tests.outbound import make_admission


def result(**changes):
    return ResultMessage(
        **(
            {
                "subtype": "success",
                "duration_ms": 1,
                "duration_api_ms": 1,
                "is_error": False,
                "num_turns": 1,
                "session_id": "audit",
                "structured_output": {"findings": []},
            }
            | changes
        )
    )


def result_error(subtype="success", *, reason=None, status=None, **data):
    return ResultError(
        "budget rate timeout refusal: prose must never classify this error",
        {"subtype": subtype, "terminal_reason": reason, "api_error_status": status}
        | data,
        exit_code=1,
    )


def native_executor(monkeypatch, entry, scripts):
    """Only the external SDK boundary is scripted; both real adapters run."""
    entered = []
    closed = []
    options = []

    async def messages(index):
        for action in scripts[min(index, len(scripts) - 1)]:
            if isinstance(action, BaseException):
                raise action
            if isinstance(action, asyncio.Event):
                await action.wait()
            else:
                yield action

    if entry == "client":

        class Client:
            def __init__(self, **kwargs):
                options.append(kwargs["options"])

            async def __aenter__(self):
                self.index = len(entered)
                entered.append(self.index)
                return self

            async def __aexit__(self, *_args):
                closed.append(self.index)

            async def query(self, _prompt):
                pass

            def receive_response(self):
                return messages(self.index)

        monkeypatch.setattr(
            "kodezart.adapters.claude_client_executor.ClaudeSDKClient", Client
        )
        executor = ClaudeClientExecutor(
            setting_sources=DEFAULT_SETTING_SOURCES,
            knowledge_grant=NO_KNOWLEDGE_GRANT,
        )
    else:

        async def query(**kwargs):
            options.append(kwargs["options"])
            index = len(entered)
            entered.append(index)
            try:
                async for message in messages(index):
                    yield message
            finally:
                closed.append(index)

        monkeypatch.setattr("kodezart.adapters.claude_agent_executor.query", query)
        executor = ClaudeAgentExecutor(
            setting_sources=DEFAULT_SETTING_SOURCES,
            knowledge_grant=NO_KNOWLEDGE_GRANT,
        )
    return executor, entered, closed, options


EXCEPTIONS = [
    (CLIConnectionError("offline"), "transport_error", True),
    (CLINotFoundError(), "not_configured", False),
    (ProcessError("stopped", exit_code=1), "execution_error", False),
    (ClaudeSDKError("rate limit timeout budget"), "execution_error", False),
    (result_error("error_max_budget_usd"), "budget_exhausted", False),
    (result_error("error_max_turns"), "budget_exhausted", False),
    (result_error("error_max_structured_output_retries"), "malformed_verdict", False),
    (result_error("error_during_execution"), "execution_error", False),
    (result_error("budget_timeout_rate_refusal"), "execution_error", False),
    (result_error(reason="api_error", status=429), "rate_limited", True),
    (result_error(reason="api_error", status=500), "transport_error", True),
    (result_error(reason="api_error", status=529), "transport_error", True),
    (result_error(reason="api_error", status=504), "timeout", True),
    (result_error(reason="api_error", status=401), "execution_error", False),
    (result_error(reason="api_error", status=403), "execution_error", False),
    (result_error(reason="api_error", status=599), "execution_error", False),
    (result_error(reason="aborted_streaming", status=429), "execution_error", False),
    (result_error(stop_reason="refusal"), "refusal", False),
    (
        AgentSDKError("opaque failure", error_kind="CLIConnectionError"),
        "execution_error",
        False,
    ),
]


@pytest.mark.parametrize("entry", ["client", "query"])
@pytest.mark.parametrize("failure,expected,retryable", EXCEPTIONS)
async def test_native_sdk_exception_is_typed_and_retries_only_known_transients(
    monkeypatch, entry, failure, expected, retryable
):
    executor, entered, closed, options = native_executor(
        monkeypatch, entry, [[failure]]
    )
    observed = await scanner_for(executor, retry_max_attempts=3).scan(
        content="Ordinary explanatory prose.", destination=OutboundDestination.PR_BODY
    )
    assert observed.failure.value == expected
    assert observed.hits == ()
    assert entered == closed == list(range(3 if retryable else 1))
    assert all(option.allowed_tools == [] for option in options)
    assert all(option.resume is None for option in options)


RESULTS = [
    ({}, None),
    ({"is_error": True, "subtype": "error_max_turns"}, "budget_exhausted"),
    ({"is_error": True, "subtype": "error_max_budget_usd"}, "budget_exhausted"),
    (
        {"is_error": True, "subtype": "error_max_structured_output_retries"},
        "malformed_verdict",
    ),
    ({"is_error": True, "subtype": "error_during_execution"}, "execution_error"),
    ({"is_error": True, "subtype": "some_budget_error"}, "execution_error"),
    ({"is_error": True, "subtype": "RATE_LIMIT"}, "execution_error"),
    ({"is_error": True, "subtype": "future_timeout"}, "execution_error"),
    ({"is_error": True, "subtype": "unblocked"}, "execution_error"),
    ({"is_error": True, "subtype": "cost"}, "execution_error"),
    ({"stop_reason": "refusal"}, "refusal"),
    ({"is_error": True, "stop_reason": "refusal"}, "refusal"),
    ({"structured_output": None}, "empty_response"),
]


@pytest.mark.parametrize("entry", ["client", "query"])
@pytest.mark.parametrize("changes,expected", RESULTS)
async def test_native_result_fields_are_exact_and_never_inferred_from_prose(
    monkeypatch, entry, changes, expected
):
    executor, entered, closed, _ = native_executor(
        monkeypatch, entry, [[result(**changes)]]
    )
    observed = await scanner_for(executor, retry_max_attempts=3).scan(
        content="Ordinary explanatory prose.", destination=OutboundDestination.PR_BODY
    )
    assert (None if observed.failure is None else observed.failure.value) == expected
    assert observed.hits == ()
    assert entered == closed == [0]


@pytest.mark.parametrize("entry", ["client", "query"])
@pytest.mark.parametrize(
    "failure",
    [CLIConnectionError("offline"), result_error(reason="api_error", status=429)],
)
async def test_transient_native_failure_can_recover_in_a_fresh_attempt(
    monkeypatch, entry, failure
):
    executor, entered, closed, _ = native_executor(
        monkeypatch, entry, [[failure], [result()]]
    )
    observed = await scanner_for(executor, retry_max_attempts=3).scan(
        content="Ordinary prose.", destination=OutboundDestination.PR_BODY
    )
    assert observed.failure is None
    assert entered == closed == [0, 1]


@pytest.mark.parametrize("entry", ["client", "query"])
@pytest.mark.parametrize("status,expected", [(429, "rate_limited"), (504, "timeout")])
async def test_error_result_then_native_exception_retains_the_native_status(
    monkeypatch, entry, status, expected
):
    executor, entered, closed, _ = native_executor(
        monkeypatch,
        entry,
        [
            [
                result(
                    is_error=True, api_error_status=status, terminal_reason="api_error"
                ),
                result_error(reason="api_error", status=status),
            ]
        ],
    )
    observed = await scanner_for(executor).scan(
        content="Ordinary prose.", destination=OutboundDestination.PR_BODY
    )
    assert observed.failure.value == expected
    assert entered == closed == [0]


@pytest.mark.parametrize("entry", ["client", "query"])
async def test_native_rejected_rate_event_still_controls_the_result(monkeypatch, entry):
    warning = RateLimitEvent(
        rate_limit_info=RateLimitInfo(status="rejected"),
        uuid="native-warning",
        session_id="audit",
    )
    executor, entered, closed, _ = native_executor(
        monkeypatch, entry, [[warning, result()]]
    )
    observed = await scanner_for(executor).scan(
        content="Ordinary prose.", destination=OutboundDestination.PR_BODY
    )
    assert observed.failure.value == "rate_limited"
    assert entered == closed == [0]


@pytest.mark.parametrize("entry", ["client", "query"])
async def test_per_attempt_deadline_settles_each_native_session(monkeypatch, entry):
    executor, entered, closed, _ = native_executor(
        monkeypatch, entry, [[asyncio.Event()]]
    )
    observed = await scanner_for(
        executor, retry_max_attempts=2, timeout_seconds=0.02
    ).scan(content="Ordinary prose.", destination=OutboundDestination.PR_BODY)
    assert observed.failure.value == "timeout"
    assert entered == closed == [0, 1]


@pytest.mark.parametrize("entry", ["client", "query"])
async def test_caller_cancellation_closes_native_session_without_retry(
    monkeypatch, entry
):
    executor, entered, closed, _ = native_executor(
        monkeypatch, entry, [[asyncio.Event()]]
    )
    task = asyncio.create_task(
        scanner_for(executor, retry_max_attempts=3).scan(
            content="Ordinary prose.", destination=OutboundDestination.PR_BODY
        )
    )
    try:
        async with asyncio.timeout(5):
            while not entered:
                await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert entered == closed == [0]


@pytest.mark.parametrize("entry", ["client", "query"])
async def test_backoff_cancellation_never_starts_another_native_attempt(
    monkeypatch, entry
):
    executor, entered, closed, _ = native_executor(
        monkeypatch, entry, [[CLIConnectionError("offline")]]
    )
    task = asyncio.create_task(
        scanner_for(executor, retry=RetryPolicy(attempts=3, initial_delay=30)).scan(
            content="Ordinary prose.", destination=OutboundDestination.PR_BODY
        )
    )
    try:
        async with asyncio.timeout(5):
            while not closed:
                await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert entered == closed == [0]


@pytest.mark.parametrize("entry", ["client", "query"])
async def test_local_credentials_block_before_a_native_session(monkeypatch, entry):
    executor, entered, closed, _ = native_executor(monkeypatch, entry, [[result()]])
    gate = make_admission(scanner_for(executor))
    observed = await gate.gate(
        content="credential ghp_" + "a" * 36,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert observed.verdict is GateVerdict.BLOCKED
    assert observed.failure is None
    assert entered == closed == []


@pytest.mark.parametrize("entry", ["client", "query"])
async def test_absent_prompt_configuration_starts_no_native_session(monkeypatch, entry):
    executor, entered, closed, _ = native_executor(monkeypatch, entry, [[result()]])
    observed = await scanner_for(executor, private_surface=None).scan(
        content="Ordinary prose.", destination=OutboundDestination.PR_BODY
    )
    assert observed.failure.value == "not_configured"
    assert entered == closed == []


@pytest.mark.parametrize("entry", ["client", "query"])
@pytest.mark.parametrize("visibility", [RepoVisibility.PUBLIC, RepoVisibility.UNKNOWN])
async def test_native_unknown_execution_failure_blocks_the_actual_pr_writer(
    monkeypatch, entry, visibility
):
    from tests.chains.test_outbound_gating import make_engine, run_engine
    from tests.fakes import FakePRCreator, FakeVisibilityResolver

    executor, entered, closed, _ = native_executor(
        monkeypatch, entry, [[result_error("error_during_execution")]]
    )
    creator = FakePRCreator()
    engine = make_engine(
        pr_creator=creator,
        gate=make_admission(scanner_for(executor, retry_max_attempts=3)),
        visibility_resolver=FakeVisibilityResolver(visibility),
    )
    with pytest.raises(OutboundContentBlockedError) as caught:
        await run_engine(engine)
    assert caught.value.failure.value == "execution_error"
    assert entered == closed == [0]
    assert creator.calls == []


@pytest.mark.parametrize("entry", ["client", "query"])
@pytest.mark.parametrize(
    "status,expected",
    [
        (429, "rate_limited"),
        (500, "transport_error"),
        (529, "transport_error"),
        (504, "timeout"),
    ],
)
async def test_native_result_without_later_exception_retains_transient_status(
    monkeypatch, entry, status, expected
):
    executor, entered, closed, _ = native_executor(
        monkeypatch,
        entry,
        [[result(is_error=True, api_error_status=status, terminal_reason="api_error")]],
    )
    observed = await scanner_for(executor).scan(
        content="Ordinary prose.", destination=OutboundDestination.PR_BODY
    )
    assert observed.failure.value == expected
    assert entered == closed == [0]


@pytest.mark.parametrize("entry", ["client", "query"])
@pytest.mark.parametrize("case", range(3))
async def test_internal_failure_preserves_actual_handler_and_result_json(
    monkeypatch, entry, case
):
    import json
    from pathlib import Path

    from kodezart.adapters._sdk_mapping import map_message
    from kodezart.handlers.agent_handler import AgentHandler
    from kodezart.services.agent_service import AgentService
    from kodezart.types.domain.session import SessionFailureKind
    from kodezart.types.requests.agent import QueryRequest
    from tests.fakes import SUPPRESS_ALL_SKILLS, FakeWorkspaceProvider

    # Captured from the actual prior daeb1cb mapper, before this new internal fact.
    old = json.loads(Path(__file__).with_name("scanner_result_wire.json").read_text())[
        case
    ]
    message = result(**old["changes"])
    (mapped,) = map_message(message)
    expected_kind = [None, SessionFailureKind.RATE_LIMITED, SessionFailureKind.REFUSAL][
        case
    ]
    assert mapped.failure_kind is expected_kind
    assert mapped.model_dump_json(by_alias=True) == old["json"]
    executor, entered, closed, _ = native_executor(monkeypatch, entry, [[message]])
    workspace = FakeWorkspaceProvider()
    handler = AgentHandler(
        service=AgentService(
            executor=executor, workspace=workspace, git_base_url="https://github.com"
        ),
        skills=SUPPRESS_ALL_SKILLS,
    )
    frames = [
        frame
        async for frame in handler.stream_query(
            QueryRequest(prompt="Inspect", repo_path="/fixture")
        )
    ]
    assert [frame for frame in frames if frame["type"] == "result"] == [old["public"]]
    assert entered == closed == [0]
    assert [call[0] for call in workspace.calls] == ["acquire", "release"]


async def test_unclassified_executor_error_cannot_become_a_clean_verdict():
    from tests.adapters.test_judgment_scanner import ScriptedAuditExecutor, audit_result

    executor = ScriptedAuditExecutor(
        [audit_result([], is_error=True, subtype="budget")]
    )
    observed = await scanner_for(executor, retry_max_attempts=3).scan(
        content="Ordinary prose.", destination=OutboundDestination.PR_BODY
    )
    assert observed.failure.value == "execution_error"
    assert observed.hits == ()
    assert len(executor.calls) == 1
