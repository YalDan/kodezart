"""The existing donor base validator closes the actual submission boundary."""

import pytest
from httpx import ASGITransport, AsyncClient

from kodezart.core.config import AppConfig
from kodezart.main import create_app
from kodezart.types.domain.branch import BaseSpec
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeAgentRunner, FakeJobQueue


@pytest.mark.parametrize("route", ["fire", "workflow"])
@pytest.mark.parametrize("base", ["missing", "empty", "recorded"])
async def test_base_input_is_validated_before_domain_queue_submission(route, base):
    queue = FakeJobQueue(events=[])
    runner = FakeAgentRunner(events=[])
    app = create_app()
    app.state.config = AppConfig()
    app.state.job_queue = queue
    app.state.agent_service = runner
    app.state.skills = SUPPRESS_ALL_SKILLS
    body = {"prompt": "run", "repoPath": "/tmp/fixture"}
    if base != "missing":
        body["baseBranch"] = ""
    if base == "recorded":
        body["baseSpec"] = {"inputs": [], "baseBranch": "recorded-base"}
        body["impliedBase"] = {"inputs": [], "baseBranch": "implied-base"}
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(f"/api/v1/agent/{route}", json=body)
    assert runner.calls == []
    if base == "empty":
        assert response.status_code == 422
        assert response.json()["detail"][0]["msg"] == (
            "Value error, baseBranch must not be empty when baseSpec is absent"
        )
        assert queue.submissions == []
    else:
        assert response.status_code == (202 if route == "fire" else 200)
        (submission,) = [row[1] for row in queue.submissions]
        assert submission.base_spec.base_branch == (
            "main" if base == "missing" else "recorded-base"
        )
        assert submission.implied_base == (
            None if base == "missing" else BaseSpec.model_validate(body["impliedBase"])
        )
