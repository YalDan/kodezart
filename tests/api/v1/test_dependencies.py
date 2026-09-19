"""Public routes exercise typed overrides and their declared response contracts."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.exceptions import ResponseValidationError
from httpx import ASGITransport, AsyncClient

from kodezart.api import dependencies as deps
from kodezart.domain.errors import QueueFullError
from kodezart.handlers.job_handler import JobHandler
from kodezart.main import create_app
from kodezart.services.job_service import JobService
from kodezart.types.domain.agent import AssistantTextEvent
from kodezart.types.domain.job import JobState
from kodezart.types.domain.workflow import WorkflowSubmission
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeAgentRunner, FakeJobQueue

BODY = {"prompt": "Do the work", "repoPath": "/fixture"}


def events(response):
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


@pytest.fixture
def boundary():
    app = create_app()
    frame = AssistantTextEvent(text="observed", model="fixture")
    runner = FakeAgentRunner(events=[frame])
    queue = FakeJobQueue(events=[frame])
    job_handler = JobHandler(service=JobService(registry=queue, run_state_reader=None))
    app.dependency_overrides.update(
        {
            deps.get_agent_runner: lambda: runner,
            deps.get_skills: lambda: SUPPRESS_ALL_SKILLS,
            deps.get_job_queue: lambda: queue,
            deps.get_job_registry: lambda: queue,
            deps.get_job_handler: lambda: job_handler,
        }
    )
    # No service, skills or queue is planted on app.state: only these actual
    # dependency overrides can make the HTTP routes reach the supplied objects.
    return SimpleNamespace(app=app, runner=runner, queue=queue, frame=frame)


async def test_fire_and_status_use_overrides_preserving_wire_contract(boundary):
    async with AsyncClient(
        transport=ASGITransport(app=boundary.app), base_url="http://test"
    ) as client:
        fired = await client.post("/api/v1/agent/fire", json=BODY)
        assert fired.status_code == 202
        accepted = fired.json()
        assert set(accepted) == {
            "jobId",
            "lane",
            "state",
            "queuePosition",
            "submittedAt",
            "statusUrl",
            "streamUrl",
        }
        assert accepted["state"] == "queued"
        assert accepted["queuePosition"] == 1
        assert accepted["statusUrl"] == "/api/v1/jobs/job-0001"
        assert accepted["streamUrl"] == "/api/v1/jobs/job-0001/stream"
        status = await client.get(accepted["statusUrl"])
        assert status.status_code == 200
        assert status.json() == {
            "jobId": accepted["jobId"],
            "lane": accepted["lane"],
            "state": "queued",
            "queuePosition": 1,
            "submittedAt": accepted["submittedAt"],
            "outcome": None,
            "truncated": False,
            "runStateAvailable": False,
            "run": None,
        }
    submission = boundary.queue.submissions[0][1]
    assert isinstance(submission, WorkflowSubmission)
    assert submission.prompt == BODY["prompt"]
    assert submission.repo_path == BODY["repoPath"]
    assert submission.base_spec.base_branch == "main"
    assert boundary.queue.attached == boundary.runner.calls == []


async def test_configured_route_prefix_controls_resolving_handle_urls(
    boundary, monkeypatch
):
    monkeypatch.setenv("KODEZART_HTTP__API_V1_PREFIX", "/alternate")
    app = create_app()
    app.dependency_overrides.update(boundary.app.dependency_overrides)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/alternate/agent/fire", json=BODY)
        assert response.status_code == 202
        assert response.json()["statusUrl"] == "/alternate/jobs/job-0001"
        assert response.json()["streamUrl"] == "/alternate/jobs/job-0001/stream"
        assert (await client.get(response.json()["statusUrl"])).status_code == 200
        assert (await client.get(response.json()["streamUrl"])).status_code == 200


async def test_workflow_and_attach_preserve_sse_frames(boundary):
    async with AsyncClient(
        transport=ASGITransport(app=boundary.app), base_url="http://test"
    ) as client:
        workflow = await client.post("/api/v1/agent/workflow", json=BODY)
        assert workflow.status_code == 200
        assert workflow.headers["content-type"] == "text/event-stream; charset=utf-8"
        frames = events(workflow)
        assert frames[0]["type"] == "job_accepted"
        assert frames[0]["jobId"] == "job-0001"
        assert frames[1:] == [boundary.frame.model_dump(by_alias=True)]
        attached = await client.get(frames[0]["streamUrl"])
        assert attached.status_code == 200
        assert attached.headers["content-type"] == workflow.headers["content-type"]
        assert events(attached) == frames[1:]
    assert boundary.queue.attached == ["job-0001", "job-0001"]
    assert len(boundary.queue.submissions) == 1


async def test_query_has_no_queue_dependency(boundary):
    def forbidden_queue():
        raise AssertionError("a query must not resolve the workflow queue")

    boundary.app.dependency_overrides[deps.get_job_queue] = forbidden_queue
    boundary.app.dependency_overrides[deps.get_job_registry] = forbidden_queue
    async with AsyncClient(
        transport=ASGITransport(app=boundary.app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/agent/query", json=BODY)
    assert response.status_code == 200
    assert events(response) == [boundary.frame.model_dump(by_alias=True)]
    assert len(boundary.runner.calls) == 1
    assert boundary.runner.calls[0]["skills"] == SUPPRESS_ALL_SKILLS
    assert boundary.queue.submissions == []


@pytest.mark.parametrize("endpoint", ["fire", "workflow"])
async def test_queue_refusal_keeps_429_json_envelope(boundary, endpoint):
    class Full(FakeJobQueue):
        async def submit(self, **kwargs):
            raise QueueFullError("the lane is full")

    boundary.app.dependency_overrides[deps.get_job_queue] = lambda: Full()
    async with AsyncClient(
        transport=ASGITransport(app=boundary.app), base_url="http://test"
    ) as client:
        response = await client.post(f"/api/v1/agent/{endpoint}", json=BODY)
    assert response.status_code == 429
    assert response.headers["content-type"] == "application/json"
    payload = response.json()
    assert set(payload) == {"success", "timestamp", "data", "error"}
    assert payload["success"] is False
    assert payload["error"] == "the lane is full"
    assert payload["data"] is None


@pytest.mark.parametrize("suffix", ["", "/stream"])
async def test_unknown_job_keeps_404_json_envelope(boundary, suffix):
    async with AsyncClient(
        transport=ASGITransport(app=boundary.app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/v1/jobs/missing{suffix}")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    payload = response.json()
    assert set(payload) == {"success", "timestamp", "data", "error"}
    assert payload["error"] == "job not found: missing"
    assert payload["success"] is False and payload["data"] is None
    assert boundary.queue.attached == []


async def test_invalid_success_is_refused_by_declared_response_model(boundary):
    class BrokenHandler:
        async def get_status(self, **kwargs):
            return {"jobId": "missing-the-required-fields"}

    boundary.app.dependency_overrides[deps.get_job_handler] = lambda: BrokenHandler()
    async with AsyncClient(
        transport=ASGITransport(app=boundary.app), base_url="http://test"
    ) as client:
        with pytest.raises(ResponseValidationError):
            await client.get("/api/v1/jobs/broken")


@pytest.mark.parametrize("endpoint", ["fire", "workflow", "query"])
async def test_invalid_request_is_422_without_starting_work(boundary, endpoint):
    async with AsyncClient(
        transport=ASGITransport(app=boundary.app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/v1/agent/{endpoint}", json={**BODY, "prompt": ""}
        )
    assert response.status_code == 422
    assert boundary.queue.submissions == boundary.runner.calls == []


async def test_cancelled_attachment_closes_stream_without_deleting_job(boundary):
    entered, released = asyncio.Event(), asyncio.Event()

    class LiveQueue(FakeJobQueue):
        async def attach(self, **kwargs):
            try:
                yield boundary.frame
                entered.set()
                await asyncio.Event().wait()
            finally:
                released.set()

    queue = LiveQueue()
    boundary.app.dependency_overrides[deps.get_job_queue] = lambda: queue
    boundary.app.dependency_overrides[deps.get_job_registry] = lambda: queue
    async with AsyncClient(
        transport=ASGITransport(app=boundary.app), base_url="http://test"
    ) as client:
        task = asyncio.create_task(client.post("/api/v1/agent/workflow", json=BODY))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(released.wait(), 5)
            assert queue.records["job-0001"].state is JobState.QUEUED
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_openapi_names_success_error_and_stream_contracts(boundary):
    paths = boundary.app.openapi()["paths"]
    fire = paths["/api/v1/agent/fire"]["post"]["responses"]
    status = paths["/api/v1/jobs/{job_id}"]["get"]["responses"]
    assert fire["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/FireAcceptedResponse"
    }
    assert status["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/JobStatusResponse"
    }
    for responses, code in [(fire, "429"), (status, "404")]:
        assert responses[code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/BaseResponse"
        }
    for path, method in [
        ("/api/v1/agent/query", "post"),
        ("/api/v1/agent/workflow", "post"),
        ("/api/v1/jobs/{job_id}/stream", "get"),
    ]:
        assert set(paths[path][method]["responses"]["200"]["content"]) == {
            "text/event-stream"
        }
