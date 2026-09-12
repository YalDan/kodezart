"""Neutral permissions cross real HTTP, service, checkpoint and SDK boundaries."""

import asyncio
from unittest.mock import patch

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import ValidationError

from kodezart.api import dependencies
from kodezart.main import create_app
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.workflow import ExecutionContext, WorkflowSubmission
from tests.fakes import (
    EXECUTOR_MODULES,
    FAKE_SESSION_TYPE,
    SUPPRESS_ALL_SKILLS,
    FakeJobQueue,
    FakeWorkspaceProvider,
    _recording_client,
    _recording_query,
    executor_for,
)

MODES = [
    (PermissionMode.INTERACTIVE, "default"),
    (PermissionMode.ACCEPT_EDITS, "acceptEdits"),
    (PermissionMode.PLAN, "plan"),
    (PermissionMode.UNATTENDED, "bypassPermissions"),
]
TOOLS = ["Read", "Bash(git status:*)", "mcp__fixture__read", "FutureTool"]


@pytest.fixture(params=EXECUTOR_MODULES)
def sdk(request):
    module = request.param
    records = []
    persistent = module.endswith("claude_client_executor")
    target = "ClaudeSDKClient" if persistent else "query"
    replacement = (
        _recording_client(records, ()) if persistent else _recording_query(records, ())
    )
    workspace = FakeWorkspaceProvider()
    executor = executor_for(module)
    service = AgentService(
        git_base_url="https://github.com", executor=executor, workspace=workspace
    )
    with patch(f"{module}.{target}", replacement):
        yield service, records, workspace, executor


@pytest.mark.parametrize(("mode", "wire"), MODES)
async def test_application_permission_reaches_each_sdk_as_its_existing_option(
    sdk, mode, wire
):
    service, records, workspace, _ = sdk
    assert [
        event
        async for event in service.stream(
            prompt="Inspect the source",
            repo_path="/fixture",
            permission_mode=mode,
            allowed_tools=TOOLS,
            skills=SUPPRESS_ALL_SKILLS,
            session_type=FAKE_SESSION_TYPE,
        )
    ] == []
    assert len(records) == 1
    options = records[0].options
    assert isinstance(options, ClaudeAgentOptions)
    assert options.permission_mode == wire
    assert options.allowed_tools == TOOLS
    assert [call[0] for call in workspace.calls] == ["acquire", "release"]


@pytest.mark.parametrize("endpoint", ["query", "fire", "workflow"])
@pytest.mark.parametrize("wire", [None, "plan", "bypassPermissions"])
async def test_existing_http_values_and_defaults_translate_before_application_entry(
    sdk, endpoint, wire
):
    service, records, workspace, _ = sdk
    queue = FakeJobQueue(events=[])
    app = create_app()
    app.dependency_overrides.update(
        {
            dependencies.get_agent_runner: lambda: service,
            dependencies.get_skills: lambda: SUPPRESS_ALL_SKILLS,
            dependencies.get_job_queue: lambda: queue,
        }
    )
    body = {"prompt": "Inspect", "repoPath": "/fixture", "allowedTools": TOOLS}
    if wire is not None:
        body["permissionMode"] = wire
    effective_wire = wire or ("plan" if endpoint == "query" else "bypassPermissions")
    expected = (
        PermissionMode.PLAN if effective_wire == "plan" else PermissionMode.UNATTENDED
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(f"/api/v1/agent/{endpoint}", json=body)
    assert response.status_code == (202 if endpoint == "fire" else 200)
    if endpoint == "query":
        assert queue.submissions == []
        assert len(records) == 1
        assert records[0].options.permission_mode == effective_wire
        assert records[0].options.allowed_tools == TOOLS
        assert [call[0] for call in workspace.calls] == ["acquire", "release"]
    else:
        assert records == []
        submission = queue.submissions[0][1]
        assert submission.permission_mode is expected
        assert submission.allowed_tools == TOOLS
    assert "permissionMode" not in response.text


@pytest.mark.parametrize("endpoint", ["query", "fire", "workflow"])
@pytest.mark.parametrize(
    "wire", ["default", "acceptEdits", "interactive", "unattended", "auto", "invalid"]
)
async def test_http_does_not_widen_its_existing_two_mode_contract(sdk, endpoint, wire):
    service, records, workspace, _ = sdk
    queue = FakeJobQueue(events=[])
    app = create_app()
    app.dependency_overrides.update(
        {
            dependencies.get_agent_runner: lambda: service,
            dependencies.get_skills: lambda: SUPPRESS_ALL_SKILLS,
            dependencies.get_job_queue: lambda: queue,
        }
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/v1/agent/{endpoint}",
            json={"prompt": "Inspect", "repoPath": "/fixture", "permissionMode": wire},
        )
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "type": "literal_error",
            "loc": ["body", "permissionMode"],
            "msg": "Input should be 'plan' or 'bypassPermissions'",
            "input": wire,
            "ctx": {"expected": "'plan' or 'bypassPermissions'"},
        }
    ]
    assert records == queue.submissions == workspace.calls == []


@pytest.mark.parametrize(
    "mode", ["default", "acceptEdits", "bypassPermissions", "plan", "invalid", None]
)
async def test_untyped_adapter_input_refuses_before_sdk_construction(sdk, mode):
    _, records, _, executor = sdk
    with pytest.raises(ValueError, match="Invalid permission mode"):
        [
            event
            async for event in executor.stream(
                prompt="Inspect",
                cwd="/fixture",
                permission_mode=mode,
                allowed_tools=TOOLS,
                skills=SUPPRESS_ALL_SKILLS,
                session_type=FAKE_SESSION_TYPE,
            )
        ]
    assert records == []


@pytest.mark.parametrize("model", [ExecutionContext, WorkflowSubmission])
@pytest.mark.parametrize(
    "mode", ["default", "acceptEdits", "bypassPermissions", "invalid"]
)
def test_domain_construction_rejects_vendor_modes_and_unknown_values(model, mode):
    fields = {
        "prompt": "Inspect",
        "repo_path": "/fixture",
        "repo_url": None,
        "base_spec": trunk_base("main"),
        "permission_mode": mode,
        "allowed_tools": TOOLS,
    }
    if model is WorkflowSubmission:
        fields.update(implied_base=None, scope=None)
    else:
        fields.update(cache_key="checkpoint-key")
    with pytest.raises(ValidationError, match="permission_mode"):
        model.model_validate(fields)


@pytest.mark.parametrize(("mode", "wire"), MODES)
def test_checkpoint_config_restores_the_same_domain_permission(mode, wire):
    context = ExecutionContext(
        prompt="Inspect",
        repo_path="/fixture",
        base_spec=trunk_base("main"),
        cache_key="checkpoint-key",
        permission_mode=mode,
        allowed_tools=TOOLS,
    )
    serializer = JsonPlusSerializer()
    loaded = serializer.loads_typed(serializer.dumps_typed(context.model_dump()))
    restored = ExecutionContext.from_configurable({"configurable": loaded})
    assert restored == context
    assert restored.permission_mode is mode
    assert ExecutionContext.model_validate_json(context.model_dump_json()) == context


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_cancellation_still_closes_sdk_and_releases_the_workspace(module):
    entered = asyncio.Event()
    closed = []
    options_seen = []

    class Client:
        def __init__(self, *, options):
            options_seen.append(options)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(True)

        async def query(self, prompt):
            entered.set()
            await asyncio.Event().wait()

        async def receive_response(self):
            if False:
                yield None

    async def query(*, prompt, options):
        options_seen.append(options)
        try:
            entered.set()
            await asyncio.Event().wait()
            yield None
        finally:
            closed.append(True)

    workspace = FakeWorkspaceProvider()
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor_for(module),
        workspace=workspace,
    )

    async def run():
        return [
            event
            async for event in service.stream(
                prompt="Inspect",
                repo_path="/fixture",
                permission_mode=PermissionMode.UNATTENDED,
                allowed_tools=TOOLS,
                skills=SUPPRESS_ALL_SKILLS,
                session_type=FAKE_SESSION_TYPE,
            )
        ]

    persistent = module.endswith("claude_client_executor")
    target = "ClaudeSDKClient" if persistent else "query"
    with patch(f"{module}.{target}", Client if persistent else query):
        task = asyncio.create_task(run())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    assert options_seen[0].permission_mode == "bypassPermissions"
    assert closed == [True]
    assert [call[0] for call in workspace.calls] == ["acquire", "release"]


@pytest.mark.parametrize(("mode", "wire"), MODES)
def test_opt_in_harness_probe_uses_the_same_sdk_translation(tmp_path, mode, wire):
    from tests.probes.test_harness_capabilities import session_options

    options = session_options(
        cwd=tmp_path, permission_mode=mode, allowed_tools=TOOLS, max_turns=3
    )
    assert options.permission_mode == wire
    assert options.allowed_tools == TOOLS
    assert options.max_turns == 3


def test_the_probe_alternative_preserves_its_interactive_sdk_mode(tmp_path):
    from tests.probes.test_harness_capabilities import (
        UNGATED_PERMISSION_MODE,
        session_options,
    )

    options = session_options(
        cwd=tmp_path,
        permission_mode=UNGATED_PERMISSION_MODE,
        allowed_tools=TOOLS,
        max_turns=3,
    )
    assert options.permission_mode == "default"
