"""Common delivery reaches the real forge adapter through its native contract."""

import json

import httpx
import pytest

from kodezart.core.config import AppConfig
from kodezart.domain.errors import DeliveryRouteUnavailableError, ForgeAPIError
from kodezart.types.domain.outcome import WorkflowOutcome
from tests.adapters.test_github_api import _completed_run, _empty_runs, _make_client
from tests.chains.test_delivery_runtime import BASE, HEAD, SHA, deliver, setup


@pytest.mark.parametrize("verdict", [True, None])
async def test_actual_forge_opens_and_observes_once_without_closing(verdict):
    requests = []
    pr_state = "open"
    stored = []

    def handler(request):
        requests.append(request)
        if (
            request.method == "GET"
            and request.url.path == "/repos/example/project/pulls"
        ):
            return httpx.Response(200, json=stored)
        if (
            request.method == "POST"
            and request.url.path == "/repos/example/project/pulls"
        ):
            payload = json.loads(request.content)
            stored.append(
                {
                    "html_url": "https://github.com/example/project/pull/19",
                    "number": 19,
                    "state": pr_state,
                    "title": payload["title"],
                    "body": payload["body"],
                    "head": {"ref": payload["head"]},
                    "base": {"ref": payload["base"]},
                }
            )
            return httpx.Response(201, json=stored[0])
        if (
            request.method == "GET"
            and request.url.path == f"/repos/example/project/commits/{HEAD}/check-runs"
        ):
            return _completed_run() if verdict is True else _empty_runs()
        if (
            request.method == "GET"
            and request.url.path == "/repos/example/project/actions/workflows"
        ):
            return httpx.Response(200, json={"total_count": 0, "workflows": []})
        raise AssertionError(
            f"unexpected forge capability {request.method} {request.url.path}"
        )

    client = _make_client(
        handler, ci_no_checks_grace_polls=1, ci_no_workflows_grace_polls=1
    )
    fixture = setup(forge=client, query=client, editor=client, monitor=client)
    try:
        result = await deliver(fixture.coordinator)
    finally:
        await client.close()
    assert result.pr.url == "https://github.com/example/project/pull/19"
    assert result.pr.number == 19
    assert result.pr.state == pr_state == "open"
    assert result.checks_passed is verdict
    assert result.outcome is (
        WorkflowOutcome.ci_passed
        if verdict is True
        else WorkflowOutcome.ci_not_configured
    )
    writes = [request for request in requests if request.method != "GET"]
    assert len(writes) == 1
    payload = json.loads(writes[0].content)
    assert payload["head"] == HEAD
    assert payload["base"] == BASE
    assert payload["title"] == "Authored title"
    assert "Recorded caveat" in payload["body"]
    assert "Tracker issue: subject/42" in payload["body"]
    assert sum("check-runs" in request.url.path for request in requests) == 1


async def test_actual_forge_red_cannot_publish_a_success_record():
    writes = []

    def handler(request):
        if (
            request.method == "GET"
            and request.url.path == "/repos/example/project/pulls"
        ):
            return httpx.Response(200, json=[])
        if (
            request.method == "POST"
            and request.url.path == "/repos/example/project/pulls"
        ):
            writes.append(request.url.path)
            return httpx.Response(
                201,
                json={
                    "html_url": "https://github.com/example/project/pull/20",
                    "number": 20,
                },
            )
        if request.method == "GET" and "check-runs" in request.url.path:
            payload = _completed_run("failure").json()
            payload["check_runs"][0]["head_sha"] = SHA
            return httpx.Response(200, json=payload)
        raise AssertionError(
            f"unconnected red route wrote {request.method} {request.url.path}"
        )

    client = _make_client(handler)
    try:
        with pytest.raises(DeliveryRouteUnavailableError) as error:
            await deliver(
                setup(
                    forge=client,
                    query=client,
                    editor=client,
                    monitor=client,
                    observations=client,
                    config=AppConfig(delivery_red_rerun_max_attempts=0),
                ).coordinator
            )
    finally:
        await client.close()
    assert error.value.pr_number == 20
    assert error.value.checks_passed is False
    assert writes == ["/repos/example/project/pulls"]


async def test_actual_forge_create_refusal_never_starts_a_watch():
    requests = []

    def handler(request):
        requests.append(request)
        if (
            request.method == "GET"
            and request.url.path == "/repos/example/project/pulls"
        ):
            return httpx.Response(200, json=[])
        return httpx.Response(422, json={"message": "invalid base"})

    client = _make_client(handler)
    try:
        with pytest.raises(ForgeAPIError):
            await deliver(
                setup(
                    forge=client, query=client, editor=client, monitor=client
                ).coordinator
            )
    finally:
        await client.close()
    assert [request.method for request in requests] == ["GET", "POST"]
