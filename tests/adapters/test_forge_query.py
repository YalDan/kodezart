"""Read-before-create forge contract at the HTTP and fake boundaries."""

import httpx
import pytest

from kodezart.core.protocols import ForgeQuery
from kodezart.domain.errors import ForgeAPIError, PRContentConflictError
from tests.adapters.test_github_api import _make_client
from tests.fakes import FakeForgeQuery


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [False, True])
async def test_open_head_lookup_uses_native_filter_and_domain_result(found):
    def handle(request):
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repository/pulls"
        assert dict(request.url.params) == {
            "state": "open",
            "head": "owner:feature/one",
            "per_page": "2",
            "sort": "created",
            "direction": "desc",
        }
        return httpx.Response(
            200,
            json=[
                {
                    "number": 42,
                    "title": "change",
                    "html_url": "https://github.com/owner/repository/pull/42",
                }
            ]
            if found
            else [],
        )

    adapter = _make_client(handle)
    assert isinstance(adapter, ForgeQuery)
    assert await adapter.open_pr_for_head(
        repo_url="https://github.com/owner/repository.git",
        head="feature/one",
    ) == (("https://github.com/owner/repository/pull/42", 42) if found else None)
    await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, [{"title": "missing identity"}]])
async def test_malformed_query_is_domain_error_not_no_pr(payload):
    adapter = _make_client(lambda _: httpx.Response(200, json=payload), max_retries=0)
    with pytest.raises(ForgeAPIError):
        await adapter.open_pr_for_head(repo_url="https://github.com/o/r.git", head="x")
    await adapter.close()


@pytest.mark.asyncio
async def test_refused_lookup_is_not_absence():
    adapter = _make_client(lambda _: httpx.Response(403), max_retries=0)
    with pytest.raises(ForgeAPIError):
        await adapter.open_pr_for_head(repo_url="https://github.com/o/r.git", head="x")
    await adapter.close()


@pytest.mark.parametrize("boundary", ["fake", "github"])
async def test_multiple_open_heads_refuse_without_selecting_the_newest(boundary):
    repo = "https://github.com/example/project"
    if boundary == "fake":
        adapter = FakeForgeQuery(ambiguous_heads=frozenset({(repo, "head")}))
        with pytest.raises(PRContentConflictError):
            await adapter.open_pr_for_head(repo_url=repo, head="head")
    else:
        adapter = _make_client(
            lambda _: httpx.Response(
                200,
                json=[
                    {"html_url": f"{repo}/pull/1", "number": 1},
                    {"html_url": f"{repo}/pull/2", "number": 2},
                ],
            )
        )
        try:
            with pytest.raises(PRContentConflictError):
                await adapter.open_pr_for_head(repo_url=repo, head="head")
        finally:
            await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["github.com", "github.example:8443"])
@pytest.mark.parametrize(
    "repo_url",
    [
        "https://github.com/owner/repository.git",
        "https://github.com/owner/repository/",
    ],
)
async def test_branch_url_is_composed_without_http_or_query_fragment_injection(
    repo_url, host
):
    repo_url = repo_url.replace("github.com", host)

    def unexpected(_):
        pytest.fail("branch URL composition must not call HTTP")

    adapter = _make_client(unexpected)
    assert adapter.branch_web_url(repo_url=repo_url, branch="feature/a#b%c") == (
        f"https://{host}/owner/repository/tree/feature%2Fa%23b%25c"
    )
    await adapter.close()


@pytest.mark.asyncio
async def test_fake_read_results_are_keyed_and_recorded():
    repo = "https://github.com/owner/repository.git"
    result = ("https://github.com/owner/repository/pull/42", 42)
    fake = FakeForgeQuery(
        open_prs={(repo, "branch"): result},
        branch_urls={(repo, "branch"): "https://example.test/branch"},
    )
    assert isinstance(fake, ForgeQuery)
    assert await fake.open_pr_for_head(repo_url=repo, head="branch") == result
    assert await fake.open_pr_for_head(repo_url=repo, head="other") is None
    assert (
        await fake.open_pr_for_head(
            repo_url="https://github.com/other/repo.git", head="branch"
        )
        is None
    )
    assert (
        fake.branch_web_url(repo_url=repo, branch="branch")
        == "https://example.test/branch"
    )
    assert len(fake.calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repo_url",
    ["owner/repository", "https://user:secret@github.com/o/r.git", "file:///repo"],
)
async def test_branch_url_refuses_missing_or_credential_bearing_authority(repo_url):
    adapter = _make_client(lambda _: pytest.fail("unexpected HTTP"))
    try:
        with pytest.raises(ValueError):
            adapter.branch_web_url(repo_url=repo_url, branch="main")
    finally:
        await adapter.close()
