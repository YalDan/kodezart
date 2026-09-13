"""Application tool bundles and open selectors cross the actual session boundary."""

import asyncio
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import ValidationError

from kodezart.api import dependencies
from kodezart.composition.jobs import build_job_queue
from kodezart.core.job_queue_settings import JobQueueSettings
from kodezart.main import create_app
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.subagents import AgentDefinition
from kodezart.types.domain.workflow import ExecutionContext, WorkflowSubmission
from kodezart.types.requests.agent import QueryRequest, WorkflowRequest
from tests.adapters.test_permission_boundary import sdk as sdk
from tests.fakes import FAKE_SESSION_TYPE, SUPPRESS_ALL_SKILLS, FakeJobQueue

BUNDLES = [
    (ToolPreset.EVALUATION, ["Read", "Glob", "Grep", "Bash"]),
    (ToolPreset.DELEGATED_EVALUATION, ["Read", "Glob", "Grep", "Bash", "Agent"]),
    (
        ToolPreset.AUTHORING,
        ["Read", "Glob", "Grep", "Bash", "Agent", "WebSearch", "WebFetch"],
    ),
    (ToolPreset.IMPLEMENTATION, ["Read", "Glob", "Grep", "Bash", "Edit", "Write"]),
]
OPEN = [
    "evaluation",
    "Bash(git status:*)",
    "mcp__fixture__*",
    "FutureTool",
    "Read",
    "Read",
]
SELECTIONS = [*BUNDLES, (OPEN, OPEN), ([], [])]


def context(model, selection):
    fields = {
        "prompt": "Inspect",
        "repo_path": "/fixture",
        "repo_url": None,
        "base_spec": trunk_base("main"),
        "permission_mode": PermissionMode.PLAN,
        "allowed_tools": selection,
    }
    fields.update(
        {"cache_key": "checkpoint-key"}
        if model is ExecutionContext
        else {"implied_base": None, "scope": None}
    )
    return model.model_validate(fields)


@pytest.mark.parametrize("selection,expected", SELECTIONS)
async def test_actual_sdk_expands_only_application_bundles(sdk, selection, expected):
    service, records, workspace, _ = sdk
    assert [
        event
        async for event in service.stream(
            prompt="Inspect",
            repo_path="/fixture",
            permission_mode=PermissionMode.PLAN,
            allowed_tools=selection,
            skills=SUPPRESS_ALL_SKILLS,
            session_type=FAKE_SESSION_TYPE,
            agents=(
                AgentDefinition(
                    name="lens",
                    description="Inspect",
                    prompt="Inspect",
                    tools=tuple(OPEN),
                ),
            ),
        )
    ] == []
    assert len(records) == 1
    assert records[0].options.allowed_tools == expected
    assert records[0].options.agents["lens"].tools == OPEN
    assert [call[0] for call in workspace.calls] == ["acquire", "release"]


@pytest.mark.parametrize("model", [ExecutionContext, WorkflowSubmission])
@pytest.mark.parametrize("selection,expected", SELECTIONS)
def test_checkpoint_and_json_keep_preset_distinct_from_explicit_list(
    model, selection, expected
):
    original = context(model, selection)
    serializer = JsonPlusSerializer()
    loaded = serializer.loads_typed(serializer.dumps_typed(original.model_dump()))
    for restored in (
        model.model_validate(loaded),
        model.model_validate_json(original.model_dump_json()),
    ):
        assert restored == original
        assert isinstance(restored.allowed_tools, list) is isinstance(selection, list)
        if isinstance(selection, ToolPreset):
            assert restored.allowed_tools is selection
        else:
            assert restored.allowed_tools == selection


@pytest.mark.parametrize("model", [ExecutionContext, WorkflowSubmission])
@pytest.mark.parametrize(
    "invalid", ["Read", "unknown_preset", None, {"preset": "evaluation"}]
)
def test_unsupported_whole_value_is_refused_by_actual_context(model, invalid):
    with pytest.raises(ValidationError, match="allowed_tools"):
        context(model, invalid)


@pytest.mark.parametrize("selection,expected", SELECTIONS)
async def test_actual_queue_keeps_selection_until_native_execution(
    sdk, selection, expected
):
    service, records, _, _ = sdk
    seen = []

    class Engine:
        async def run(self, **kwargs):
            seen.append(kwargs["allowed_tools"])
            async for event in service.stream(
                prompt=kwargs["prompt"],
                repo_path=kwargs["repo_path"],
                permission_mode=kwargs["permission_mode"],
                allowed_tools=kwargs["allowed_tools"],
                skills=SUPPRESS_ALL_SKILLS,
                session_type=FAKE_SESSION_TYPE,
            ):
                yield event

    queue = build_job_queue(settings=JobQueueSettings(), workflow_engine=Engine())
    await queue.start()
    try:
        record = await queue.submit(
            lane="fixture", request=context(WorkflowSubmission, selection)
        )
        async with asyncio.timeout(5):
            assert [event async for event in queue.attach(job_id=record.job_id)] == []
        assert seen == [selection]
        assert records[0].options.allowed_tools == expected
    finally:
        await queue.stop()


@pytest.mark.parametrize("endpoint", ["query", "fire", "workflow"])
@pytest.mark.parametrize("selection", [None, OPEN, [], "evaluation"])
async def test_http_remains_an_explicit_open_list_contract(sdk, endpoint, selection):
    service, records, _, _ = sdk
    queue = FakeJobQueue(events=[])
    app = create_app()
    app.dependency_overrides.update(
        {
            dependencies.get_agent_runner: lambda: service,
            dependencies.get_skills: lambda: SUPPRESS_ALL_SKILLS,
            dependencies.get_job_queue: lambda: queue,
        }
    )
    body = {"prompt": "Inspect", "repoPath": "/fixture"}
    if selection is not None:
        body["allowedTools"] = selection
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(f"/api/v1/agent/{endpoint}", json=body)
    if isinstance(selection, str):
        assert response.status_code == 422
        assert response.json()["detail"] == [
            {
                "type": "list_type",
                "loc": ["body", "allowedTools"],
                "msg": "Input should be a valid list",
                "input": selection,
            }
        ]
        assert records == queue.submissions == []
        return
    assert response.status_code == (202 if endpoint == "fire" else 200)
    expected = (
        selection
        if selection is not None
        else (
            ["Read", "Glob", "Grep", "Bash"]
            if endpoint == "query"
            else ["Read", "Glob", "Grep", "Bash", "Edit", "Write"]
        )
    )
    actual = (
        records[0].options.allowed_tools
        if endpoint == "query"
        else queue.submissions[0][1].allowed_tools
    )
    assert actual == expected and isinstance(actual, list)


def test_actual_http_schemas_match_the_captured_previous_boundary():
    expected = json.loads(
        Path(__file__).with_name("tool_request_schemas.json").read_text()
    )
    assert {
        model.__name__: model.model_json_schema()
        for model in (QueryRequest, WorkflowRequest)
    } == expected


async def test_actual_tracker_dispatch_requests_the_implementation_bundle():
    from tests.fakes import FakeTrackerPort, make_tracker_issue
    from tests.services.test_fire_dispatcher import dispatcher

    tracker = FakeTrackerPort(issues=[make_tracker_issue("subject")])
    fire, queue, _ = dispatcher(tracker)
    await fire.run_pass()
    assert len(queue.submissions) == 1
    assert queue.submissions[0][1].allowed_tools is ToolPreset.IMPLEMENTATION


async def test_unknown_whole_value_cannot_reach_native_sdk(sdk):
    service, records, workspace, _ = sdk
    with pytest.raises(KeyError, match="not_a_preset"):
        _ = [
            event
            async for event in service.stream(
                prompt="Inspect",
                repo_path="/fixture",
                permission_mode=PermissionMode.PLAN,
                allowed_tools="not_a_preset",
                skills=SUPPRESS_ALL_SKILLS,
                session_type=FAKE_SESSION_TYPE,
            )
        ]
    assert records == []
    assert [call[0] for call in workspace.calls] == ["acquire", "release"]
