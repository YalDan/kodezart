"""Tests for GitHubAPIClient using httpx.MockTransport."""

import httpx
import pytest

from kodezart.adapters.github_api import GitHubAPIClient
from kodezart.domain.errors import RateLimitError

_FAKE_PAT = "test-token"


def _mock_transport(handler):
    """Build an httpx.MockTransport from a request handler function."""
    return httpx.MockTransport(handler)


def _make_client(
    handler,
    *,
    ci_poll_interval_seconds: float = 0.0,
    ci_poll_max_attempts: int = 10,
    ci_no_checks_grace_polls: int = 3,
    timeout_seconds: float = 5.0,
    max_retries: int = 1,
    retry_backoff_factor: float = 0.01,
) -> GitHubAPIClient:
    """Create a GitHubAPIClient with a mock transport for testing."""
    mock_http = httpx.AsyncClient(
        transport=_mock_transport(handler),
        base_url="https://api.github.com",
    )
    return GitHubAPIClient(
        token=_FAKE_PAT,
        base_url="https://api.github.com",
        ci_poll_interval_seconds=ci_poll_interval_seconds,
        ci_poll_max_attempts=ci_poll_max_attempts,
        ci_no_checks_grace_polls=ci_no_checks_grace_polls,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_factor=retry_backoff_factor,
        client=mock_http,
    )


# -- PRCreator tests ---------------------------------------------------------


async def test_create_pr_success() -> None:
    """create_pr returns (url, number) from 201 response."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/repos/owner/repo/pulls" in str(request.url)
        return httpx.Response(
            201,
            json={"html_url": "https://github.com/owner/repo/pull/42", "number": 42},
        )

    client = _make_client(handler)
    url, number = await client.create_pr(
        repo_url="https://github.com/owner/repo",
        title="feat: test",
        body="Test body",
        head="feature-branch",
        base="main",
    )
    assert url == "https://github.com/owner/repo/pull/42"
    assert number == 42
    await client.close()


async def test_create_pr_http_error() -> None:
    """create_pr raises httpx.HTTPStatusError on 422."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "Validation Failed"})

    client = _make_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await client.create_pr(
            repo_url="https://github.com/owner/repo",
            title="feat: test",
            body="body",
            head="branch",
            base="main",
        )
    await client.close()


async def test_comment_on_pr_success() -> None:
    """comment_on_pr succeeds on 201."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/repos/owner/repo/issues/1/comments" in str(request.url)
        return httpx.Response(201, json={"id": 1})

    client = _make_client(handler)
    await client.comment_on_pr(
        repo_url="https://github.com/owner/repo",
        pr_number=1,
        body="Test comment",
    )
    await client.close()


# -- Retry tests -------------------------------------------------------------


async def test_retries_on_429_then_succeeds() -> None:
    """_request_with_retry retries 429 and succeeds on retry."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(
            201,
            json={"html_url": "https://github.com/owner/repo/pull/1", "number": 1},
        )

    client = _make_client(handler, max_retries=1, retry_backoff_factor=0.01)
    url, number = await client.create_pr(
        repo_url="https://github.com/owner/repo",
        title="feat: retry",
        body="body",
        head="branch",
        base="main",
    )
    assert url == "https://github.com/owner/repo/pull/1"
    assert number == 1
    assert call_count == 2
    await client.close()


async def test_exhausts_retries_on_429_raises_rate_limit_error() -> None:
    """_request_with_retry raises RateLimitError after exhausting retries."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "rate limited"})

    client = _make_client(handler, max_retries=1, retry_backoff_factor=0.01)
    with pytest.raises(RateLimitError):
        await client.create_pr(
            repo_url="https://github.com/owner/repo",
            title="feat: test",
            body="body",
            head="branch",
            base="main",
        )
    await client.close()


async def test_retry_after_header_respected() -> None:
    """_request_with_retry uses Retry-After header for wait time."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                429,
                headers={"retry-after": "0.01"},
                json={"message": "rate limited"},
            )
        return httpx.Response(
            201,
            json={"html_url": "https://github.com/owner/repo/pull/1", "number": 1},
        )

    client = _make_client(handler, max_retries=1, retry_backoff_factor=0.01)
    url, _number = await client.create_pr(
        repo_url="https://github.com/owner/repo",
        title="feat: retry-after",
        body="body",
        head="branch",
        base="main",
    )
    assert url == "https://github.com/owner/repo/pull/1"
    assert call_count == 2
    await client.close()


async def test_retries_on_5xx_then_succeeds() -> None:
    """_request_with_retry retries 5xx and succeeds on retry."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(502, json={"message": "Bad Gateway"})
        return httpx.Response(
            201,
            json={"html_url": "https://github.com/owner/repo/pull/1", "number": 1},
        )

    client = _make_client(handler, max_retries=1, retry_backoff_factor=0.01)
    url, number = await client.create_pr(
        repo_url="https://github.com/owner/repo",
        title="feat: 5xx retry",
        body="body",
        head="branch",
        base="main",
    )
    assert url == "https://github.com/owner/repo/pull/1"
    assert number == 1
    assert call_count == 2
    await client.close()


async def test_non_retryable_422_propagates_immediately() -> None:
    """_request_with_retry does not retry 4xx (non-429)."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(422, json={"message": "Validation Failed"})

    client = _make_client(handler, max_retries=3, retry_backoff_factor=0.01)
    with pytest.raises(httpx.HTTPStatusError):
        await client.create_pr(
            repo_url="https://github.com/owner/repo",
            title="feat: test",
            body="body",
            head="branch",
            base="main",
        )
    assert call_count == 1
    await client.close()


# -- CI detection tests ------------------------------------------------------


async def test_detect_ci_returns_true_when_suites_present() -> None:
    """_detect_ci finds check suites on first poll."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "check-suites" in str(request.url):
            return httpx.Response(200, json={"total_count": 1})
        # check-runs endpoint
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "check_runs": [
                    {
                        "id": 1,
                        "name": "ci/test",
                        "status": "completed",
                        "conclusion": "success",
                    },
                ],
            },
        )

    client = _make_client(handler)
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is True
    assert "passed" in summary.lower()
    await client.close()


async def test_detect_ci_returns_false_after_grace_polls() -> None:
    """_detect_ci returns False when suites never appear."""
    suites_call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal suites_call_count
        if "check-suites" in str(request.url):
            suites_call_count += 1
            return httpx.Response(200, json={"total_count": 0})
        return httpx.Response(200, json={"total_count": 0, "check_runs": []})

    client = _make_client(handler, ci_no_checks_grace_polls=2)
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is None
    assert "no ci checks" in summary.lower()
    assert suites_call_count == 2
    await client.close()


async def test_wait_for_checks_suites_exist_runs_delayed() -> None:
    """wait_for_checks waits for runs when suites confirm CI exists."""
    runs_call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_call_count
        if "check-suites" in str(request.url):
            return httpx.Response(200, json={"total_count": 1})
        # check-runs endpoint
        runs_call_count += 1
        if runs_call_count == 1:
            return httpx.Response(200, json={"total_count": 0, "check_runs": []})
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "check_runs": [
                    {
                        "id": 1,
                        "name": "ci/test",
                        "status": "completed",
                        "conclusion": "success",
                    },
                ],
            },
        )

    client = _make_client(handler)
    passed, _summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is True
    assert runs_call_count == 2
    await client.close()


# -- CIMonitor tests (existing) ---------------------------------------------


async def test_wait_for_checks_all_success() -> None:
    """wait_for_checks returns (True, ...) when all checks pass."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "check-suites" in str(request.url):
            return httpx.Response(200, json={"total_count": 1})
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "check_runs": [
                    {
                        "id": 1,
                        "name": "ci/test",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "id": 2,
                        "name": "ci/lint",
                        "status": "completed",
                        "conclusion": "success",
                    },
                ],
            },
        )

    client = _make_client(handler)
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is True
    assert "passed" in summary.lower()
    await client.close()


async def test_wait_for_checks_failure() -> None:
    """wait_for_checks returns (False, ...) with failed check names."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "check-suites" in str(request.url):
            return httpx.Response(200, json={"total_count": 1})
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "check_runs": [
                    {
                        "id": 1,
                        "name": "ci/test",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                    {
                        "id": 2,
                        "name": "ci/lint",
                        "status": "completed",
                        "conclusion": "success",
                    },
                ],
            },
        )

    client = _make_client(handler)
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is False
    assert "ci/test" in summary
    await client.close()


async def test_wait_for_checks_in_progress_then_success() -> None:
    """wait_for_checks polls in-progress then returns success."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        if "check-suites" in str(request.url):
            return httpx.Response(200, json={"total_count": 1})
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "check_runs": [
                        {
                            "id": 1,
                            "name": "ci/test",
                            "status": "in_progress",
                            "conclusion": None,
                        },
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "check_runs": [
                    {
                        "id": 1,
                        "name": "ci/test",
                        "status": "completed",
                        "conclusion": "success",
                    },
                ],
            },
        )

    client = _make_client(handler)
    passed, _summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is True
    assert call_count == 2
    await client.close()


async def test_wait_for_checks_timeout() -> None:
    """wait_for_checks returns (False, ...) when max attempts exhausted."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "check-suites" in str(request.url):
            return httpx.Response(200, json={"total_count": 1})
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "check_runs": [
                    {
                        "id": 1,
                        "name": "ci/test",
                        "status": "in_progress",
                        "conclusion": None,
                    },
                ],
            },
        )

    client = _make_client(
        handler,
        ci_poll_max_attempts=2,
    )
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is False
    assert "still running" in summary.lower()
    await client.close()


async def test_wait_for_checks_no_checks_configured() -> None:
    """wait_for_checks returns (None, ...) when no checks exist after grace polls."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "check-suites" in str(request.url):
            return httpx.Response(200, json={"total_count": 0})
        return httpx.Response(
            200,
            json={"total_count": 0, "check_runs": []},
        )

    client = _make_client(handler, ci_no_checks_grace_polls=2)
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is None
    assert "no ci checks" in summary.lower()
    await client.close()


async def test_wait_for_checks_neutral_and_skipped() -> None:
    """wait_for_checks treats neutral and skipped conclusions as passing."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "check-suites" in str(request.url):
            return httpx.Response(200, json={"total_count": 1})
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "check_runs": [
                    {
                        "id": 1,
                        "name": "ci/optional",
                        "status": "completed",
                        "conclusion": "neutral",
                    },
                    {
                        "id": 2,
                        "name": "ci/skippable",
                        "status": "completed",
                        "conclusion": "skipped",
                    },
                ],
            },
        )

    client = _make_client(handler)
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is True
    assert "passed" in summary.lower()
    await client.close()
