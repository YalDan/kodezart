"""Tests for GitHubAPIClient using httpx.MockTransport."""

import asyncio

import httpx
import pytest
import structlog

from kodezart.adapters.github_api import GitHubAPIClient
from kodezart.domain.errors import RateLimitError, TransientAPIError
from kodezart.types.domain.gating import RepoVisibility

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
    ci_no_workflows_grace_polls: int = 3,
    ci_grace_poll_interval_seconds: float = 10.0,
    ci_ref_not_found_grace_polls: int = 3,
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
        ci_no_workflows_grace_polls=ci_no_workflows_grace_polls,
        ci_grace_poll_interval_seconds=ci_grace_poll_interval_seconds,
        ci_ref_not_found_grace_polls=ci_ref_not_found_grace_polls,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_factor=retry_backoff_factor,
        client=mock_http,
    )


def _workflows(*, active: bool) -> httpx.Response:
    """Actions-workflows listing with (or without) one active workflow."""
    if active:
        return httpx.Response(
            200,
            json={"total_count": 1, "workflows": [{"state": "active"}]},
        )
    return httpx.Response(200, json={"total_count": 0, "workflows": []})


def _empty_runs() -> httpx.Response:
    """Check-runs page carrying no runs at all."""
    return httpx.Response(200, json={"total_count": 0, "check_runs": []})


def _completed_run(conclusion: str = "success") -> httpx.Response:
    """Check-runs page with a single completed run."""
    return httpx.Response(
        200,
        json={
            "total_count": 1,
            "check_runs": [
                {
                    "id": 1,
                    "name": "ci/test",
                    "status": "completed",
                    "conclusion": conclusion,
                },
            ],
        },
    )


def _in_progress_run() -> httpx.Response:
    """Check-runs page with a single still-running run."""
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


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every ``asyncio.sleep`` delay awaited during the test."""
    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def _record(delay, result=None):
        recorded.append(delay)
        return await real_sleep(0, result)

    monkeypatch.setattr(asyncio, "sleep", _record)
    return recorded


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


# -- CIMonitor tests ---------------------------------------------------------


async def test_wait_for_checks_workflows_active_runs_delayed() -> None:
    """wait_for_checks waits for runs when the repository has workflows."""
    runs_call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_call_count
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        runs_call_count += 1
        if runs_call_count == 1:
            return _empty_runs()
        return _completed_run()

    client = _make_client(handler)
    passed, _summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is True
    assert runs_call_count == 2
    await client.close()


async def test_wait_for_checks_all_success() -> None:
    """wait_for_checks returns (True, ...) when all checks pass."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
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
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
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
    """Runs present but not completed are awaited, then resolve to pass."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        call_count += 1
        if call_count == 1:
            return _in_progress_run()
        return _completed_run()

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
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        return _in_progress_run()

    client = _make_client(
        handler,
        ci_poll_max_attempts=2,
    )
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is False
    assert summary == "CI checks still running after 2 polls."
    await client.close()


async def test_wait_for_checks_no_checks_configured() -> None:
    """Zero runs throughout with no workflows terminates as no-CI.

    Exactly one workflows call: the lazy probe on the first empty poll,
    memoised call-local for the rest of the call.
    """
    runs_calls = 0
    workflows_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls, workflows_calls
        if "actions/workflows" in str(request.url):
            workflows_calls += 1
            return _workflows(active=False)
        runs_calls += 1
        return _empty_runs()

    client = _make_client(handler, ci_no_workflows_grace_polls=2)
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is None
    assert "no ci checks" in summary.lower()
    assert summary == "No CI checks configured: repository has no active workflows."
    assert workflows_calls == 1
    assert runs_calls == 2
    await client.close()


async def test_wait_for_checks_neutral_and_skipped() -> None:
    """wait_for_checks treats neutral and skipped conclusions as passing."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
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


# ---------------------------------------------------------------------------
# KOD-47/AC-1, AC-8 — repository visibility resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("private", "expected"),
    [(True, RepoVisibility.PRIVATE), (False, RepoVisibility.PUBLIC)],
)
async def test_resolve_visibility_maps_the_repos_endpoint(
    private: bool,
    expected: RepoVisibility,
) -> None:
    """GET /repos/{owner}/{repo} decides PRIVATE vs PUBLIC."""
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"private": private})

    client = _make_client(handler)
    try:
        visibility = await client.resolve_visibility(
            repo_url="https://github.com/owner/repo",
        )
    finally:
        await client.close()

    assert visibility is expected
    assert seen == ["/repos/owner/repo"]


async def test_resolve_visibility_fails_closed_to_unknown() -> None:
    """AC-8: a resolution failure is UNKNOWN — never a raise, never a skip."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = _make_client(handler)
    try:
        visibility = await client.resolve_visibility(
            repo_url="https://github.com/owner/repo",
        )
    finally:
        await client.close()

    assert visibility is RepoVisibility.UNKNOWN


# -- Workflows probe / grace selection ---------------------------------------


async def test_no_active_workflows_concludes_on_first_empty_poll() -> None:
    """With ci_no_workflows_grace_polls=1 the very first empty poll is terminal."""
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return _workflows(active=False)
        runs_calls += 1
        return _empty_runs()

    client = _make_client(handler, ci_no_workflows_grace_polls=1)
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is None
    assert summary == "No CI checks configured: repository has no active workflows."
    assert runs_calls == 1
    await client.close()


async def test_active_workflows_use_the_standard_grace_window() -> None:
    """ACTIVE probe measures the empty streak against ci_no_checks_grace_polls."""
    runs_calls = 0
    workflows_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls, workflows_calls
        if "actions/workflows" in str(request.url):
            workflows_calls += 1
            return _workflows(active=True)
        runs_calls += 1
        return _empty_runs()

    client = _make_client(
        handler,
        ci_no_checks_grace_polls=4,
        ci_no_workflows_grace_polls=1,
    )
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is None
    assert summary == "No CI checks appeared for this ref after 4 polls."
    assert runs_calls == 4
    assert workflows_calls == 1
    await client.close()


async def test_grace_sleeps_strictly_between_polls(sleeps: list[float]) -> None:
    """N grace polls cost N-1 sleeps — the terminal poll takes no trailing sleep."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        return _empty_runs()

    client = _make_client(
        handler,
        ci_poll_interval_seconds=30.0,
        ci_grace_poll_interval_seconds=10.0,
        ci_no_checks_grace_polls=4,
    )
    passed, _summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is None
    assert sleeps == [10.0, 10.0, 10.0]
    await client.close()


async def test_grace_cadence_is_clamped_to_the_poll_interval(
    sleeps: list[float],
) -> None:
    """The grace cadence is min(poll, grace) — never slower than the poll interval.

    The defaults (poll 30, grace 10) make ``min`` a no-op, so this case
    inverts them: only a grace interval *longer* than the poll interval
    can tell the pinned ``min`` apart from using the configured value
    verbatim.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        return _empty_runs()

    client = _make_client(
        handler,
        ci_poll_interval_seconds=15.0,
        ci_grace_poll_interval_seconds=30.0,
        ci_no_checks_grace_polls=3,
    )
    passed, _summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is None
    assert sleeps == [15.0, 15.0]
    await client.close()


async def test_probe_failure_403_falls_back_to_standard_grace() -> None:
    """A 403 on the workflows probe degrades to the longer grace, never raises."""
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return httpx.Response(403, json={"message": "Forbidden"})
        runs_calls += 1
        return _empty_runs()

    client = _make_client(
        handler,
        ci_no_checks_grace_polls=3,
        ci_no_workflows_grace_polls=1,
    )
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is None
    assert summary == "No CI checks appeared for this ref after 3 polls."
    assert runs_calls == 3
    await client.close()


async def test_probe_rate_limit_falls_back_to_standard_grace() -> None:
    """A retry-exhausted 429 probe degrades to the longer grace, never raises.

    ``RateLimitError`` is a ``TransientAPIError``, and the acceptance
    criterion names rate-limit alongside 403 and 5xx: the probe only
    selects a grace window, so no probe failure may end the call.
    """
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return httpx.Response(429, json={"message": "rate limited"})
        runs_calls += 1
        return _empty_runs()

    client = _make_client(
        handler,
        ci_no_checks_grace_polls=3,
        ci_no_workflows_grace_polls=1,
        max_retries=1,
        retry_backoff_factor=0.01,
    )
    with structlog.testing.capture_logs() as logs:
        result = await client.wait_for_checks(
            repo_url="https://github.com/owner/repo", ref="abc123"
        )

    assert result == (None, "No CI checks appeared for this ref after 3 polls.")
    assert runs_calls == 3
    probe_failed = [e for e in logs if e["event"] == "ci_workflows_probe_failed"]
    assert len(probe_failed) == 1
    assert probe_failed[0]["log_level"] == "warning"
    assert probe_failed[0]["grace_polls"] == 3
    await client.close()


async def test_probe_failure_5xx_falls_back_to_standard_grace() -> None:
    """A retry-exhausted 5xx probe degrades to the longer grace, never raises."""
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return httpx.Response(502, json={"message": "Bad Gateway"})
        runs_calls += 1
        return _empty_runs()

    client = _make_client(
        handler,
        ci_no_checks_grace_polls=3,
        ci_no_workflows_grace_polls=1,
    )
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is None
    assert summary == "No CI checks appeared for this ref after 3 polls."
    assert runs_calls == 3
    await client.close()


async def test_runs_appearing_during_grace_are_evaluated_normally() -> None:
    """A NONE_ACTIVE probe never suppresses runs that show up during the grace."""
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return _workflows(active=False)
        runs_calls += 1
        if runs_calls == 1:
            return _empty_runs()
        return _completed_run()

    client = _make_client(handler, ci_no_workflows_grace_polls=3)
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is True
    assert summary == "All CI checks passed."
    assert runs_calls == 2
    await client.close()


async def test_probe_never_fires_when_first_poll_observes_a_run() -> None:
    """The probe is lazy: a first poll carrying runs issues zero workflows calls."""
    workflows_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal workflows_calls
        if "actions/workflows" in str(request.url):
            workflows_calls += 1
            return _workflows(active=True)
        return _completed_run()

    client = _make_client(handler)
    passed, _summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is True
    assert workflows_calls == 0
    await client.close()


async def test_probe_total_count_beyond_page_classifies_as_active() -> None:
    """total_count > len(workflows) errs toward the longer grace window."""
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "total_count": 101,
                    "workflows": [{"state": "disabled_manually"}],
                },
            )
        runs_calls += 1
        return _empty_runs()

    client = _make_client(
        handler,
        ci_no_checks_grace_polls=2,
        ci_no_workflows_grace_polls=1,
    )
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is None
    assert summary == "No CI checks appeared for this ref after 2 polls."
    assert runs_calls == 2
    await client.close()


async def test_empty_page_after_runs_observed_is_pending_not_no_ci() -> None:
    """Once a run is observed an empty page is pending and never yields no-CI."""
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        runs_calls += 1
        if runs_calls == 1:
            return _in_progress_run()
        if runs_calls == 2:
            return _empty_runs()
        return _completed_run()

    client = _make_client(handler, ci_no_checks_grace_polls=1)
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is True
    assert summary == "All CI checks passed."
    assert runs_calls == 3
    await client.close()


async def test_grace_polls_do_not_consume_the_poll_budget() -> None:
    """Empty grace polls are free; the budget starts at the first observation."""

    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        runs_calls += 1
        if runs_calls <= 2:
            return _empty_runs()
        return _in_progress_run()

    client = _make_client(
        handler,
        ci_no_checks_grace_polls=3,
        ci_poll_max_attempts=1,
    )
    result = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert result == (False, "CI checks still running after 1 polls.")
    assert runs_calls == 3
    await client.close()


async def test_one_poll_issues_exactly_one_check_runs_request() -> None:
    """A poll is a single GET, so ci_poll_max_attempts bounds requests too."""
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        runs_calls += 1
        return _completed_run()

    client = _make_client(handler)
    passed, _summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is True
    assert runs_calls == 1
    await client.close()


# -- Ref-not-found (404) tolerance -------------------------------------------


async def test_single_404_is_tolerated_and_polling_continues() -> None:
    """A first-poll 404 does not raise; the call reaches a terminal outcome."""
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        runs_calls += 1
        if runs_calls == 1:
            return httpx.Response(404, json={"message": "Not Found"})
        if runs_calls == 2:
            return _in_progress_run()
        return _completed_run()

    client = _make_client(handler)
    with structlog.testing.capture_logs() as logs:
        passed, summary = await client.wait_for_checks(
            repo_url="https://github.com/owner/repo", ref="abc123"
        )
    assert passed is True
    assert summary == "All CI checks passed."
    assert runs_calls == 3
    tolerated = [e for e in logs if e["event"] == "ci_ref_not_found_tolerated"]
    assert len(tolerated) == 1
    assert tolerated[0]["ref"] == "abc123"
    await client.close()


async def test_404s_beyond_grace_raise_transient_api_error() -> None:
    """Consecutive 404s past the grace raise rather than returning a tuple."""
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        runs_calls += 1
        return httpx.Response(404, json={"message": "Not Found"})

    client = _make_client(handler, ci_ref_not_found_grace_polls=3)
    with pytest.raises(TransientAPIError):
        await client.wait_for_checks(
            repo_url="https://github.com/owner/repo", ref="abc123"
        )
    assert runs_calls == 4
    await client.close()


async def test_404s_never_surface_as_false_or_none() -> None:
    """The exhausted-404 condition is an exception, not a CI verdict."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        return httpx.Response(404, json={"message": "Not Found"})

    client = _make_client(handler, ci_ref_not_found_grace_polls=1)
    result: object = None
    try:
        result = await client.wait_for_checks(
            repo_url="https://github.com/owner/repo", ref="abc123"
        )
    except TransientAPIError:
        result = "raised"
    assert result == "raised"
    assert result != (False, "CI checks still running after 10 polls.")
    assert result != (None, "No CI checks appeared for this ref after 3 polls.")
    await client.close()


async def test_404s_interleaved_with_empty_pages_still_terminate() -> None:
    """404s neither advance nor reset the empty-poll streak — the loop ends."""
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        runs_calls += 1
        if runs_calls % 2 == 1:
            return httpx.Response(404, json={"message": "Not Found"})
        return _empty_runs()

    client = _make_client(
        handler,
        ci_ref_not_found_grace_polls=3,
        ci_no_checks_grace_polls=3,
    )
    result = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert result == (None, "No CI checks appeared for this ref after 3 polls.")
    assert runs_calls == 6
    await client.close()


async def test_404_consumes_no_poll_budget() -> None:
    """A tolerated 404 does not spend ci_poll_max_attempts."""
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        runs_calls += 1
        if runs_calls == 1:
            return httpx.Response(404, json={"message": "Not Found"})
        return _completed_run()

    client = _make_client(
        handler,
        ci_ref_not_found_grace_polls=3,
        ci_poll_max_attempts=1,
    )
    passed, summary = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert passed is True
    assert summary == "All CI checks passed."
    assert runs_calls == 2
    await client.close()


async def test_404_sleeps_at_the_grace_cadence_and_raise_takes_none(
    sleeps: list[float],
) -> None:
    """404s are paced like empty pages; the terminal raise takes no sleep."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        return httpx.Response(404, json={"message": "Not Found"})

    client = _make_client(
        handler,
        ci_poll_interval_seconds=30.0,
        ci_grace_poll_interval_seconds=10.0,
        ci_ref_not_found_grace_polls=3,
    )
    with pytest.raises(TransientAPIError):
        await client.wait_for_checks(
            repo_url="https://github.com/owner/repo", ref="abc123"
        )
    assert sleeps == [10.0, 10.0, 10.0]
    await client.close()


async def test_non_404_status_error_propagates_from_wait_for_checks() -> None:
    """A 403 on check-runs propagates exactly as before — no 404 tolerance."""
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        runs_calls += 1
        return httpx.Response(403, json={"message": "Forbidden"})

    client = _make_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await client.wait_for_checks(
            repo_url="https://github.com/owner/repo", ref="abc123"
        )
    assert runs_calls == 1
    await client.close()


class TestOpenDeliveryProbe:
    """The forge side of the delivered-in-review / crashed discrimination."""

    async def _client(self, entries: list[dict[str, object]]) -> GitHubAPIClient:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["state"] == "open"
            return httpx.Response(200, json=entries)

        return _make_client(handler)

    async def test_a_referencing_open_pr_is_a_delivery(self) -> None:
        client = await self._client(
            [
                {
                    "number": 1,
                    "title": "feat: something",
                    "body": "Closes KOD-58.",
                    "html_url": "https://example.invalid/pull/1",
                },
            ],
        )
        assert await client.open_delivery_exists(
            repo_url="https://github.com/owner/repo",
            issue_key="KOD-58",
        )

    async def test_an_unrelated_open_pr_is_not_a_delivery(self) -> None:
        client = await self._client(
            [
                {
                    "number": 1,
                    "title": "chore: unrelated",
                    "body": None,
                    "html_url": "https://example.invalid/pull/1",
                },
            ],
        )
        assert not await client.open_delivery_exists(
            repo_url="https://github.com/owner/repo",
            issue_key="KOD-58",
        )

    async def test_a_key_that_is_a_prefix_of_another_does_not_match(self) -> None:
        """KOD-5 must not be delivered by a pull request about KOD-58."""
        client = await self._client(
            [
                {
                    "number": 1,
                    "title": "feat: KOD-58",
                    "body": "",
                    "html_url": "https://example.invalid/pull/1",
                },
            ],
        )
        assert not await client.open_delivery_exists(
            repo_url="https://github.com/owner/repo",
            issue_key="KOD-5",
        )

    async def test_no_open_pull_requests_is_not_a_delivery(self) -> None:
        client = await self._client([])
        assert not await client.open_delivery_exists(
            repo_url="https://github.com/owner/repo",
            issue_key="KOD-58",
        )

    async def test_the_title_alone_is_enough(self) -> None:
        client = await self._client(
            [
                {
                    "number": 2,
                    "title": "KOD-58: the tracker port",
                    "body": None,
                    "html_url": "https://example.invalid/pull/2",
                },
            ],
        )
        assert await client.open_delivery_exists(
            repo_url="https://github.com/owner/repo",
            issue_key="KOD-58",
        )

    async def test_an_origin_this_forge_does_not_own_raises_rather_than_answers(
        self,
    ) -> None:
        """UNCHANGED by KOD-145, and pinned so it stays that way.

        This adapter answers for the forge it is a client of, and it cannot
        see a local bare origin at all.  The remedy for the crash that
        caused is probe SELECTION at the composition root — a forge-less
        origin is wired to the probe that can answer for it — never a
        fallback here.  An adapter that quietly returned False for an
        origin it cannot read would be guessing, and the guess would be
        indistinguishable from a real answer.
        """
        client = await self._client([])

        with pytest.raises(ValueError, match="file://"):
            await client.open_delivery_exists(
                repo_url="file:///tmp/local-origin.git",
                issue_key="KOD-58",
            )
