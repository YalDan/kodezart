"""Delivery reads the existing PR identity before any possible duplicate write."""

import httpx
import pytest

from kodezart.domain.errors import (
    DeliveryContextError,
    DeliveryRouteUnavailableError,
    ForgeAPIError,
)
from kodezart.types.domain.operation import RunKind
from tests.adapters.test_github_api import _completed_run, _make_client
from tests.chains.test_delivery_runtime import (
    HEAD,
    REPOSITORY,
    context,
    deliver,
    setup,
)
from tests.fakes import FakeArtifactPersister, FakeForgeQuery, FakeGitService

EXISTING_URL = "https://github.com/example/project/pull/72"


async def test_existing_pr_refuses_before_cleaner_session_git_or_write():
    query = FakeForgeQuery(open_prs={(REPOSITORY, HEAD): (EXISTING_URL, 72)})
    cleaner = FakeArtifactPersister()
    git = FakeGitService()
    fixture = setup(query=query, cleaner=cleaner, git=git)
    with pytest.raises(DeliveryRouteUnavailableError) as error:
        await deliver(fixture.coordinator)
    assert error.value.pr_url == EXISTING_URL
    assert error.value.pr_number == 72
    assert error.value.checks_passed is None
    assert error.value.checks_summary is None
    assert "content-edit" in error.value.reason
    assert query.calls == [
        {"method": "open_pr_for_head", "repo_url": REPOSITORY, "head": HEAD}
    ]
    assert git.calls == cleaner.clean_calls == fixture.runner.calls == []
    assert fixture.forge.calls == fixture.monitor.calls == fixture.gate.calls == []


async def test_successful_empty_lookup_permits_one_creation():
    fixture = setup()
    result = await deliver(fixture.coordinator)
    assert fixture.query.calls == [
        {"method": "open_pr_for_head", "repo_url": REPOSITORY, "head": HEAD}
    ]
    assert [call["method"] for call in fixture.forge.calls] == ["create_pr"]
    assert result.pr.number == 1


@pytest.mark.parametrize("mismatch", ["missing", "kind", "issue"])
async def test_invalid_fire_identity_refuses_even_the_forge_read(mismatch):
    original = context()
    identity = original.execution.run_identity
    if mismatch == "missing":
        identity = None
    elif mismatch == "kind":
        identity = identity.model_copy(update={"kind": RunKind.FIRE_PREP})
    else:
        identity = identity.model_copy(update={"name": "other/99"})
    execution = original.execution.model_copy(update={"run_identity": identity})
    fixture = setup()
    with pytest.raises(DeliveryContextError):
        await deliver(
            fixture.coordinator,
            facts=original.model_copy(update={"execution": execution}),
        )
    assert fixture.query.calls == []
    assert fixture.runner.calls == fixture.forge.calls == []


@pytest.mark.parametrize("lookup", ["existing", "refusal", "malformed"])
async def test_native_lookup_never_turns_existing_or_unreadable_into_creation(lookup):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/repos/example/project/pulls"
        assert request.url.params["state"] == "open"
        assert request.url.params["head"] == f"example:{HEAD}"
        if lookup == "refusal":
            return httpx.Response(403, json={"message": "read refused"})
        if lookup == "malformed":
            return httpx.Response(200, json=[{"number": 72}])
        return httpx.Response(200, json=[{"html_url": EXISTING_URL, "number": 72}])

    client = _make_client(handler)
    cleaner = FakeArtifactPersister()
    fixture = setup(forge=client, query=client, monitor=client, cleaner=cleaner)
    expected = DeliveryRouteUnavailableError if lookup == "existing" else ForgeAPIError
    try:
        with pytest.raises(expected) as error:
            await deliver(fixture.coordinator)
    finally:
        await client.close()
    if lookup == "existing":
        assert error.value.pr_url == EXISTING_URL
        assert error.value.pr_number == 72
    assert len(requests) == 1
    assert cleaner.clean_calls == fixture.runner.calls == fixture.gate.calls == []


async def test_native_second_delivery_keeps_one_pr_and_runs_no_second_session():
    requests = []
    created = []

    def handler(request):
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=created)
        if request.method == "POST" and request.url.path.endswith("/pulls"):
            assert created == []
            created.append({"html_url": EXISTING_URL, "number": 72})
            return httpx.Response(201, json=created[0])
        if request.method == "GET" and request.url.path.endswith("/check-runs"):
            return _completed_run()
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")

    client = _make_client(handler)
    fixture = setup(forge=client, query=client, monitor=client)
    try:
        first = await deliver(fixture.coordinator)
        before = len(requests)
        with pytest.raises(DeliveryRouteUnavailableError) as error:
            await deliver(fixture.coordinator)
    finally:
        await client.close()
    assert first.pr.number == error.value.pr_number == 72
    assert first.pr.url == error.value.pr_url == EXISTING_URL
    assert len(created) == len(fixture.runner.calls) == 1
    assert [request.method for request in requests] == ["GET", "POST", "GET", "GET"]
    assert len(requests) == before + 1
