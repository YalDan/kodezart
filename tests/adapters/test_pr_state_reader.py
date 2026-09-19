"""Native PR lifecycle reads require actual facts and exact identities."""

import httpx
import pytest

from kodezart.core.protocols import PRStateReader
from kodezart.domain.errors import ForgeAPIError, PRStateReadError, TransientAPIError
from kodezart.types.domain.pr_state import PRLifecycle
from tests.adapters.test_github_api import _make_client
from tests.chains.test_audit_pass import pr_state
from tests.fakes import FakePRStateReader

REPO = "https://github.com/example/project"


def payload(**changes):
    return {
        "number": 7,
        "html_url": f"{REPO}/pull/7",
        "state": "open",
        "merged": False,
        "head": {
            "ref": "topic",
            "sha": "a" * 40,
            "repo": {"html_url": REPO, "full_name": "example/project"},
        },
        "base": {
            "ref": "main",
            "sha": "b" * 40,
            "repo": {"html_url": REPO, "full_name": "example/project"},
        },
        **changes,
    }


@pytest.mark.parametrize(
    "state,merged,expected",
    [
        ("open", False, PRLifecycle.OPEN),
        ("closed", False, PRLifecycle.CLOSED),
        ("closed", True, PRLifecycle.MERGED),
    ],
)
async def test_native_lifecycle_uses_one_read_only_endpoint(state, merged, expected):
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path))
        return httpx.Response(200, json=payload(state=state, merged=merged))

    client = _make_client(handler)
    assert isinstance(client, PRStateReader)
    try:
        observed = await client.read_pr_state(repo_url=REPO, pr_number=7)
        assert observed.lifecycle is expected
        assert observed.number == 7 and observed.head_sha == "a" * 40
        assert observed.head_repo_url == observed.base_repo_url == REPO
        assert observed.base_branch == "main"
    finally:
        await client.close()
    assert calls == [("GET", "/repos/example/project/pulls/7")]


@pytest.mark.parametrize(
    "field", ["number", "html_url", "state", "merged", "head", "base"]
)
async def test_missing_native_fact_never_defaults(field):
    data = payload()
    del data[field]
    client = _make_client(lambda _: httpx.Response(200, json=data))
    try:
        with pytest.raises(ForgeAPIError):
            await client.read_pr_state(repo_url=REPO, pr_number=7)
    finally:
        await client.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "unknown"},
        {"merged": "false"},
        {"merged": True},
        {"number": True},
        {"head": {"ref": "topic"}},
        {"head": {"sha": "a" * 40}},
        {"head": {"ref": "topic", "sha": ""}},
    ],
)
async def test_malformed_native_lifecycle_is_not_a_healthy_state(changes):
    client = _make_client(lambda _: httpx.Response(200, json=payload(**changes)))
    try:
        with pytest.raises(ForgeAPIError):
            await client.read_pr_state(repo_url=REPO, pr_number=7)
    finally:
        await client.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"number": 8},
        {"html_url": "https://github.com/example/other/pull/7"},
        {"html_url": "https://other.invalid/example/project/pull/7"},
        {"html_url": f"{REPO}/pull/7#fabricated"},
        {"html_url": f"{REPO}/pull/7?target=other"},
    ],
)
async def test_foreign_native_identity_refuses(changes):
    client = _make_client(lambda _: httpx.Response(200, json=payload(**changes)))
    try:
        with pytest.raises(PRStateReadError):
            await client.read_pr_state(repo_url=REPO, pr_number=7)
    finally:
        await client.close()


@pytest.mark.parametrize("status", [403, 404, 500])
async def test_failed_read_never_means_absent_or_open(status):
    client = _make_client(
        lambda _: httpx.Response(status, json={"message": "unavailable"})
    )
    try:
        with pytest.raises((ForgeAPIError, TransientAPIError)):
            await client.read_pr_state(repo_url=REPO, pr_number=7)
    finally:
        await client.close()


@pytest.mark.parametrize("role", ["head", "base"])
@pytest.mark.parametrize("damage", ["foreign", "deleted", "missing", "missing-name"])
async def test_native_branch_repository_must_resolve_to_the_addressed_lane(
    damage, role
):
    data = payload()
    head = data[role]
    head["repo"] = {"html_url": REPO, "full_name": "example/project"}
    if damage == "foreign":
        head["repo"] = {
            "html_url": "https://github.com/foreign/fork",
            "full_name": "foreign/fork",
        }
    elif damage == "deleted":
        head["repo"] = None
    elif damage == "missing":
        del head["repo"]
    else:
        del head["repo"]["full_name"]
    client = _make_client(lambda _: httpx.Response(200, json=data))
    try:
        with pytest.raises((PRStateReadError, ForgeAPIError)):
            await client.read_pr_state(repo_url=REPO, pr_number=7)
    finally:
        await client.close()


async def test_fake_cannot_treat_same_name_and_sha_in_fork_as_lane_repository():
    state = pr_state().model_copy(
        update={"head_repo_url": "https://github.com/foreign/fork"}
    )
    reader = FakePRStateReader(records={(REPO, 7): state})
    with pytest.raises(PRStateReadError):
        await reader.read_pr_state(repo_url=REPO, pr_number=7)


@pytest.mark.parametrize(
    "repository",
    [
        {"html_url": REPO, "full_name": "foreign/fork"},
        {"html_url": "https://github.com/foreign/fork", "full_name": "example/project"},
        {"html_url": f"{REPO}?other", "full_name": "example/project"},
        {"html_url": f"{REPO}#other", "full_name": "example/project"},
        {
            "html_url": "http://github.com/example/project",
            "full_name": "example/project",
        },
        {
            "html_url": "https://user@github.com/example/project",
            "full_name": "example/project",
        },
        {
            "html_url": "https://elsewhere.invalid/example/project",
            "full_name": "example/project",
        },
    ],
)
@pytest.mark.parametrize("role", ["head", "base"])
async def test_branch_repository_url_and_native_full_name_must_both_match(
    repository, role
):
    data = payload()
    data[role]["repo"] = repository
    client = _make_client(lambda _: httpx.Response(200, json=data))
    try:
        with pytest.raises(PRStateReadError):
            await client.read_pr_state(repo_url=REPO, pr_number=7)
    finally:
        await client.close()


@pytest.mark.parametrize("surface", ["pr", "head_repository", "base_repository"])
async def test_malformed_native_url_is_a_typed_read_refusal(surface):
    data = payload()
    if surface == "pr":
        data["html_url"] = "https://[invalid/pull/7"
    else:
        role = "head" if surface == "head_repository" else "base"
        data[role]["repo"]["html_url"] = "https://[invalid/example/project"
    client = _make_client(lambda _: httpx.Response(200, json=data))
    try:
        with pytest.raises(PRStateReadError):
            await client.read_pr_state(repo_url=REPO, pr_number=7)
    finally:
        await client.close()


@pytest.mark.parametrize(
    "repo_url", [f"{REPO}.git", f"{REPO}/", REPO.upper().replace("HTTPS", "https")]
)
async def test_native_repository_alias_keeps_authoritative_head_url(repo_url):
    client = _make_client(lambda _: httpx.Response(200, json=payload()))
    try:
        state = await client.read_pr_state(repo_url=repo_url, pr_number=7)
    finally:
        await client.close()
    assert state.head_repo_url == REPO


async def test_enterprise_repository_authority_includes_port():
    repo_url = "https://forge.example:8443/example/project"
    data = payload(html_url=f"{repo_url}/pull/7")
    data["head"]["repo"]["html_url"] = repo_url
    data["base"]["repo"]["html_url"] = repo_url
    client = _make_client(lambda _: httpx.Response(200, json=data))
    try:
        state = await client.read_pr_state(repo_url=repo_url, pr_number=7)
        assert state.head_repo_url == repo_url
        data["head"]["repo"]["html_url"] = "https://forge.example/example/project"
        with pytest.raises(PRStateReadError):
            await client.read_pr_state(repo_url=repo_url, pr_number=7)
    finally:
        await client.close()


@pytest.mark.parametrize("number", [True, "7", 7.0, 0, -1])
@pytest.mark.parametrize("adapter", ["fake", "github"])
async def test_invalid_requested_number_refuses_before_native_read(number, adapter):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=payload())

    client = _make_client(handler)
    fake = FakePRStateReader(records={(REPO, 7): pr_state()})
    reader = client if adapter == "github" else fake
    try:
        with pytest.raises(PRStateReadError):
            await reader.read_pr_state(repo_url=REPO, pr_number=number)
    finally:
        await client.close()
    assert calls == [] and fake.calls == []


@pytest.mark.parametrize("number", [True, "7", 7.0])
def test_domain_pr_number_is_a_strict_positive_integer(number):
    from pydantic import ValidationError

    from kodezart.types.domain.pr_state import PRState

    with pytest.raises(ValidationError):
        PRState.model_validate({**pr_state().model_dump(), "number": number})
