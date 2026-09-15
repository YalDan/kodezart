"""Acceptance schemas and reconnect links agree with real registered routes."""

import json
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from kodezart.api import dependencies as deps
from kodezart.api.v1.endpoints import agent, jobs
from kodezart.handlers.job_handler import JobHandler
from kodezart.services.job_service import JobService
from kodezart.types.domain.agent import AssistantTextEvent, JobAcceptedEvent
from kodezart.types.responses.job import FireAcceptedResponse
from tests.api.v1.test_dependencies import BODY, events
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeAgentRunner, FakeJobQueue


@pytest.mark.parametrize("model", [FireAcceptedResponse, JobAcceptedEvent])
@pytest.mark.parametrize(
    "field,value",
    [
        ("jobId", ""),
        ("jobId", " \t"),
        ("lane", ""),
        ("lane", "\n"),
        ("queuePosition", 0),
        ("queuePosition", -1),
        ("queuePosition", True),
        ("queuePosition", 1.5),
        ("statusUrl", ""),
        ("streamUrl", ""),
        ("statusUrl", "arbitrary"),
        ("streamUrl", "https://other.invalid/jobs/1"),
        ("statusUrl", "//other.invalid/jobs/1"),
        ("streamUrl", "/jobs/1?query"),
        ("statusUrl", "/jobs/1#fragment"),
        ("streamUrl", "/jobs/1\n"),
        ("statusUrl", "/jobs\\1"),
    ],
)
def test_both_acceptance_schemas_reject_malformed_shared_fields(model, field, value):
    payload = {
        "jobId": "job-1",
        "lane": "default",
        "queuePosition": 1,
        "statusUrl": "/jobs/job-1",
        "streamUrl": "/jobs/job-1/stream",
    }
    if model is FireAcceptedResponse:
        payload.update(state="queued", submittedAt=datetime.now(UTC).isoformat())
    payload[field] = value
    with pytest.raises(ValidationError) as caught:
        model.model_validate_json(json.dumps(payload))
    assert caught.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("endpoint", ["fire", "workflow"])
@pytest.mark.parametrize(
    "mounts,root_path",
    [
        ([], ""),
        ([("/mounted", None)], ""),
        ([("/mounted", "service")], ""),
        ([], "/proxy"),
        ([("/outer", "outer"), ("/inner", "inner")], "/proxy"),
    ],
)
@pytest.mark.parametrize("handle", ["job-1", "has space", "literal%2F?", "café-東京#"])
async def test_acceptance_links_reach_current_routes(
    endpoint, mounts, root_path, handle
):
    class Queue(FakeJobQueue):
        async def submit(self, **kwargs):
            original = await super().submit(**kwargs)
            record = original.model_copy(update={"job_id": handle})
            self.records.pop(original.job_id)
            self.records[handle] = record
            return record

    frame = AssistantTextEvent(text="observed", model="fixture")
    queue = Queue(events=[frame])
    app = FastAPI()
    # Deliberately register job paths independently of the agent prefix.
    # There is no AppConfig or app.state wiring to reconstruct these paths from.
    app.include_router(agent.router, prefix="/run/v9")
    app.include_router(jobs.router, prefix="/lookup/v2")
    handler = JobHandler(service=JobService(registry=queue, run_state_reader=None))
    app.dependency_overrides.update(
        {
            deps.get_agent_runner: lambda: FakeAgentRunner(events=[frame]),
            deps.get_skills: lambda: SUPPRESS_ALL_SKILLS,
            deps.get_job_queue: lambda: queue,
            deps.get_job_registry: lambda: queue,
            deps.get_job_handler: lambda: handler,
        }
    )
    for path, name in reversed(mounts):
        parent = FastAPI()
        parent.mount(path, app, name=name)
        app = parent
    prefix = root_path + "".join(path for path, _ in mounts)
    async with AsyncClient(
        transport=ASGITransport(app=app, root_path=root_path),
        base_url="https://service.invalid",
    ) as client:
        response = await client.post(f"{prefix}/run/v9/{endpoint}", json=BODY)
        assert response.status_code == (202 if endpoint == "fire" else 200)
        accepted = response.json() if endpoint == "fire" else events(response)[0]
        common = {"jobId", "lane", "queuePosition", "statusUrl", "streamUrl"}
        assert set(accepted) == common | (
            {"state", "submittedAt"} if endpoint == "fire" else {"type"}
        )
        assert accepted["jobId"] == handle
        assert accepted["statusUrl"].startswith(prefix + "/lookup/v2/")
        assert accepted["streamUrl"] == accepted["statusUrl"] + "/stream"
        status = await client.get(accepted["statusUrl"])
        assert status.status_code == 200
        assert status.json()["jobId"] == handle
        stream = await client.get(accepted["streamUrl"])
        assert stream.status_code == 200
        assert events(stream) == [frame.model_dump(by_alias=True)]
        assert queue.attached == [handle] * (1 if endpoint == "fire" else 2)
