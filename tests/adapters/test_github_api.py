"""Tests for GitHubAPIClient using httpx.MockTransport."""

import asyncio

import httpx
import pytest
import structlog

from kodezart.adapters.github_api import GitHubAPIClient
from kodezart.composition.forge import build_forge_client
from kodezart.core.config import AppConfig
from kodezart.domain.errors import ForgeAPIError, RateLimitError, TransientAPIError
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
    ci_check_runs_max_pages: int = 10,
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
        ci_check_runs_max_pages=ci_check_runs_max_pages,
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
    """create_pr raises the domain ForgeAPIError on 422, never a vendor type.

    The port is ``PRCreator`` and it speaks the domain taxonomy: a caller
    that had to name ``httpx`` to catch this would be importing the
    adapter's transport in order to use the port.  The status travels as
    a field so a consumer can route on it, and the detail names the
    refused request rather than the response body.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "Validation Failed"})

    client = _make_client(handler)
    with pytest.raises(ForgeAPIError) as caught:
        await client.create_pr(
            repo_url="https://github.com/owner/repo",
            title="feat: test",
            body="body",
            head="branch",
            base="main",
        )
    assert caught.value.status_code == 422
    assert caught.value.detail == "POST /repos/owner/repo/pulls"
    assert "Validation Failed" not in str(caught.value)
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
    with pytest.raises(ForgeAPIError):
        await client.create_pr(
            repo_url="https://github.com/owner/repo",
            title="feat: test",
            body="body",
            head="branch",
            base="main",
        )
    assert call_count == 1
    await client.close()


#: One instance per family under every root in the adapter's
#: ``_VENDOR_FAILURE``, so the arms are exercised as a partition rather
#: than as a list of the two the residual happened to name.
_STATUSLESS_FAILURES = [
    httpx.DecodingError("body would not decode"),
    httpx.TooManyRedirects("redirect loop"),
    httpx.InvalidURL("not a url this client will build"),
    httpx.CookieConflict("two cookies of one name"),
    httpx.StreamConsumed(),
]
#: The other arm, which keeps its retry-then-``TransientAPIError`` path.
_TRANSPORT_FAILURES = [
    httpx.ConnectError("connection refused"),
    httpx.ReadTimeout("read timed out"),
    httpx.RemoteProtocolError("bad framing"),
]
#: The two ways a body that ARRIVED still fails to become a wire model.
#: Both are ``ValueError`` — the decoder's and pydantic's — which is why
#: one arm covers them, and both are answered with a real 200.
_UNREADABLE_BODIES = [
    httpx.Response(200, content=b"<html>upstream error page</html>"),
    httpx.Response(200, json={"unexpected": True}),
]
#: The port methods that read a body. ``comment_on_pr`` reads none and is
#: the control below.
_BODY_READING_METHODS = ["create_pr", "open_delivery_exists", "wait_for_checks"]


class TestVendorFailureTranslation:
    """No NON-DOMAIN type crosses a public method — plumbing and bodies alike.

    A status the server answered with is the common failure and was
    always translated.  The rest of what httpx can raise — a body that
    would not decode, a redirect loop, a URL the client would not build,
    a stream already consumed — used to travel out of the adapter as the
    transport's own class, which put the vendor on the port for exactly
    the failures nobody writes a test for.

    A body that ARRIVED and is unusable is the same defect one layer in:
    the JSON decoder and the wire model both refuse with a ``ValueError``,
    and both used to cross the port as one.  ``ValueError`` is not the
    ports' vocabulary — a consumer catching it would be catching pydantic
    through the seam that exists to hide pydantic.

    The plumbing failures carry no status, because no request completed.
    The unreadable bodies carry the real one, because the request was
    answered and what came back was unusable.
    """

    REPO_URL = "https://github.com/owner/repo"

    def _raising(self, failure: Exception):
        """A transport that fails *failure*'s way on every request."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise failure

        return handler

    def _answering(self, body: httpx.Response):
        """A transport that answers *body* to every request."""

        def handler(request: httpx.Request) -> httpx.Response:
            return body

        return handler

    async def _call(self, client: GitHubAPIClient, port_method: str) -> object:
        calls = {
            "create_pr": lambda: client.create_pr(
                repo_url=self.REPO_URL,
                title="feat: test",
                body="body",
                head="branch",
                base="main",
            ),
            "comment_on_pr": lambda: client.comment_on_pr(
                repo_url=self.REPO_URL,
                pr_number=1,
                body="body",
            ),
            "open_delivery_exists": lambda: client.open_delivery_exists(
                repo_url=self.REPO_URL,
                issue_key="KOD-1",
            ),
            "wait_for_checks": lambda: client.wait_for_checks(
                repo_url=self.REPO_URL,
                ref="abc123",
            ),
        }
        return await calls[port_method]()

    @pytest.mark.parametrize("failure", _STATUSLESS_FAILURES)
    async def test_a_statusless_vendor_failure_is_not_retried(
        self,
        failure: Exception,
    ) -> None:
        """One attempt, then the domain error — the retry budget goes unspent.

        Nothing about a body that would not decode or a URL that would
        not build is changed by sending the identical request again, so
        these keep the 4xx path's cost rather than the transport path's.
        """
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise failure

        client = _make_client(handler, max_retries=3, retry_backoff_factor=0.01)
        with pytest.raises(ForgeAPIError) as caught:
            await self._call(client, "create_pr")

        assert attempts == 1
        assert caught.value.status_code is None
        assert caught.value.detail == "POST /repos/owner/repo/pulls"
        assert type(failure).__name__ in str(caught.value)
        await client.close()

    @pytest.mark.parametrize(
        "port_method",
        ["create_pr", "comment_on_pr", "open_delivery_exists", "wait_for_checks"],
    )
    @pytest.mark.parametrize("failure", [*_STATUSLESS_FAILURES, *_TRANSPORT_FAILURES])
    async def test_only_domain_errors_leave_a_port_method(
        self,
        port_method: str,
        failure: Exception,
    ) -> None:
        """The boundary asserted as TOTAL: every family, every port method."""
        client = _make_client(
            self._raising(failure),
            max_retries=0,
            retry_backoff_factor=0.01,
        )
        with pytest.raises((ForgeAPIError, TransientAPIError)) as caught:
            await self._call(client, port_method)

        assert not isinstance(caught.value, httpx.HTTPError)
        await client.close()

    @pytest.mark.parametrize("failure", [*_STATUSLESS_FAILURES, *_TRANSPORT_FAILURES])
    async def test_the_visibility_resolver_still_fails_closed(
        self,
        failure: Exception,
    ) -> None:
        """The one method that answers rather than raises answers UNKNOWN."""
        client = _make_client(
            self._raising(failure),
            max_retries=0,
            retry_backoff_factor=0.01,
        )

        assert (
            await client.resolve_visibility(repo_url=self.REPO_URL)
            is RepoVisibility.UNKNOWN
        )
        await client.close()

    @pytest.mark.parametrize("port_method", _BODY_READING_METHODS)
    @pytest.mark.parametrize("body", _UNREADABLE_BODIES)
    async def test_an_unreadable_body_leaves_as_a_domain_error(
        self,
        port_method: str,
        body: httpx.Response,
    ) -> None:
        """Bytes that will not decode and a shape the model refuses, both.

        ``ValidationError`` is a ``ValueError``, so a consumer that
        wanted to contain this had to catch ``ValueError`` — which is to
        say catch pydantic, through the very seam that exists so no
        consumer knows this adapter parses anything.
        """
        client = _make_client(self._answering(body), max_retries=0)
        with pytest.raises(ForgeAPIError) as caught:
            await self._call(client, port_method)

        assert not isinstance(caught.value, ValueError)
        assert caught.value.status_code == 200
        assert caught.value.detail.startswith(("GET /repos/", "POST /repos/"))
        await client.close()

    @pytest.mark.parametrize("body", _UNREADABLE_BODIES)
    async def test_a_method_that_reads_no_body_is_untouched_by_one(
        self,
        body: httpx.Response,
    ) -> None:
        """The control: the seam is where a body is READ, not on every call.

        ``comment_on_pr`` wants the status and nothing else, so a
        response it never parses is not a failure it should invent.
        """
        client = _make_client(self._answering(body), max_retries=0)

        await client.comment_on_pr(repo_url=self.REPO_URL, pr_number=1, body="body")
        await client.close()

    async def test_an_unreadable_workflows_listing_never_ends_the_call(self) -> None:
        """The probe's own invariant, held through the new translation.

        A probe only ever selects a grace window, so a listing it cannot
        classify is ``INDETERMINATE`` — never a raise out of
        ``wait_for_checks``, which would end a run over a body nothing
        was waiting on.
        """
        runs_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal runs_calls
            if "actions/workflows" in str(request.url):
                return httpx.Response(200, content=b"<html>not json</html>")
            runs_calls += 1
            return _empty_runs()

        client = _make_client(
            handler,
            ci_no_checks_grace_polls=2,
            ci_no_workflows_grace_polls=1,
            ci_grace_poll_interval_seconds=0.0,
        )
        with structlog.testing.capture_logs() as logs:
            passed, summary = await client.wait_for_checks(
                repo_url=self.REPO_URL, ref="abc123"
            )

        assert passed is None
        assert summary == "No CI checks appeared for this ref after 2 polls."
        assert runs_calls == 2
        assert [e["event"] for e in logs].count("ci_workflows_probe_failed") == 1
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
    with pytest.raises(ForgeAPIError):
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


# -- Paginated check runs (KOD-99) -------------------------------------------


def _run(index: int, *, conclusion: str = "success") -> dict[str, object]:
    """One completed check run, named so a failure is identifiable."""
    return {
        "id": index,
        "name": f"ci/test-{index}",
        "status": "completed",
        "conclusion": conclusion,
    }


def _page_of(runs: list[dict[str, object]], *, total: int) -> httpx.Response:
    """A check-runs page reporting *total* while carrying *runs*."""
    return httpx.Response(200, json={"total_count": total, "check_runs": runs})


def _page_number(request: httpx.Request) -> int:
    """The ``page`` query parameter the walk asked for."""
    return int(request.url.params.get("page", "1"))


async def test_a_failure_on_the_second_page_still_fails_the_ref() -> None:
    """The verdict is drawn from the whole run set, not the first page.

    One hundred passing runs fill page one and the only failing run is on
    page two.  Reading a single page reports a pass the ref does not have —
    which is the defect: a green verdict over evidence never collected.
    """
    passing = [_run(index) for index in range(1, 101)]
    failing = [_run(101, conclusion="failure")]

    def handler(request: httpx.Request) -> httpx.Response:
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        if _page_number(request) == 1:
            return _page_of(passing, total=101)
        return _page_of(failing, total=101)

    client = _make_client(handler)
    result = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert result == (False, "CI failed: ci/test-101")
    await client.close()


async def test_a_count_the_api_never_enumerates_is_pending_not_a_pass() -> None:
    """``total_count`` over an empty list is the degenerate short listing.

    Nothing is enumerated, so nothing completed and nothing passed.  The
    old rule ("no run has a failing conclusion") called that a pass; the
    short-listing rule calls it pending, which is what it is.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        return _page_of([], total=3)

    client = _make_client(handler, ci_poll_max_attempts=2)
    result = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert result == (False, "CI checks still running after 2 polls.")
    await client.close()


async def test_one_complete_page_costs_exactly_one_request() -> None:
    """A run set that fits on one page must not ask for a second."""
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        runs_calls += 1
        return _page_of([_run(1)], total=1)

    client = _make_client(handler)
    result = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert result == (True, "All CI checks passed.")
    assert runs_calls == 1
    await client.close()


async def test_the_page_walk_stops_at_the_configured_cap() -> None:
    """A listing that never ends is bounded by configuration, not by luck.

    Every page is full and ``total_count`` always exceeds what has been
    collected, so the walk would run forever unbounded.  It stops at the
    cap, and what it collected is short — therefore pending.
    """
    runs_calls = 0
    cap = 4

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        runs_calls += 1
        return _page_of([_run(index) for index in range(100)], total=100_000)

    client = _make_client(
        handler,
        ci_check_runs_max_pages=cap,
        ci_poll_max_attempts=1,
    )
    result = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    # Pending, so the poll budget runs out — never a TransientAPIError and
    # never a verdict drawn from the pages that did arrive.
    assert result == (False, "CI checks still running after 1 polls.")
    assert runs_calls == cap
    await client.close()


async def test_one_poll_spends_one_attempt_however_many_pages_it_reads() -> None:
    """The ruled accounting: pages are requests, but a poll is a poll.

    Two polls over a four-page cap make eight requests and spend two
    attempts — not eight.  A page is a unit of transport; an attempt is a
    unit of waiting for CI, and conflating them would shrink the poll
    budget by whatever the run set happens to be sized at.
    """
    runs_calls = 0
    cap = 4
    attempts = 2

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        runs_calls += 1
        return _page_of([_run(index) for index in range(100)], total=100_000)

    client = _make_client(
        handler,
        ci_check_runs_max_pages=cap,
        ci_poll_max_attempts=attempts,
    )
    result = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert result == (False, f"CI checks still running after {attempts} polls.")
    assert runs_calls == cap * attempts
    await client.close()


async def test_the_walk_stops_early_on_a_page_that_carries_no_runs() -> None:
    """An empty page ends the walk rather than spending the whole cap."""
    runs_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal runs_calls
        if "actions/workflows" in str(request.url):
            return _workflows(active=True)
        runs_calls += 1
        if _page_number(request) == 1:
            return _page_of([_run(index) for index in range(100)], total=250)
        return _page_of([], total=250)

    client = _make_client(handler, ci_check_runs_max_pages=10, ci_poll_max_attempts=1)
    result = await client.wait_for_checks(
        repo_url="https://github.com/owner/repo", ref="abc123"
    )
    assert result == (False, "CI checks still running after 1 polls.")
    assert runs_calls == 2
    await client.close()


def test_the_page_bound_is_threaded_from_config_to_the_forge_client() -> None:
    """The knob is configuration end to end, not a constant in the adapter."""
    config = AppConfig(github_token=_FAKE_PAT, ci_check_runs_max_pages=7)
    client = build_forge_client(config=config)

    assert client is not None
    assert client._ci_check_runs_max_pages == 7

    # And the shipped default is the field's own, not a literal in the adapter.
    default_client = build_forge_client(config=AppConfig(github_token=_FAKE_PAT))
    assert default_client is not None
    assert (
        default_client._ci_check_runs_max_pages == AppConfig().ci_check_runs_max_pages
    )
