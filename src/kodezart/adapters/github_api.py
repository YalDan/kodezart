"""GitHub REST API adapter — implements PRCreator and CIMonitor protocols."""

import asyncio
import secrets

import httpx

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.domain.errors import RateLimitError, TransientAPIError
from kodezart.domain.git_url import extract_owner_repo
from kodezart.types.domain.github import (
    CheckRunsResponse,
    CheckSuitesResponse,
    PullRequestResponse,
)
from kodezart.utils.http import parse_ratelimit_reset, parse_retry_after


class GitHubAPIClient:
    """Single adapter satisfying both PRCreator and CIMonitor protocols.

    Uses httpx.AsyncClient for async HTTP. API responses are validated
    via frozen Pydantic models (``CheckRunsResponse``, ``PullRequestResponse``).
    """

    _FAILURE_CONCLUSIONS = frozenset(
        {
            "failure",
            "timed_out",
            "cancelled",
            "action_required",
        }
    )
    _OK_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})

    def __init__(
        self,
        *,
        token: str,
        base_url: str,
        ci_poll_interval_seconds: float,
        ci_poll_max_attempts: int,
        ci_no_checks_grace_polls: int,
        timeout_seconds: float,
        max_retries: int,
        retry_backoff_factor: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._ci_poll_interval: float = ci_poll_interval_seconds
        self._ci_poll_max_attempts: int = ci_poll_max_attempts
        self._ci_no_checks_grace_polls: int = ci_no_checks_grace_polls
        self._max_retries: int = max_retries
        self._retry_backoff_factor: float = retry_backoff_factor
        self._rng: secrets.SystemRandom = secrets.SystemRandom()
        self._log: BoundLogger = get_logger(__name__)
        self._client: httpx.AsyncClient = client or httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout_seconds,
        )

    # -- Retry logic --------------------------------------------------------

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, object] | None = None,
        params: dict[str, str | int] | None = None,
    ) -> httpx.Response:
        """HTTP request with exponential backoff + 10% jitter."""
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    json=json,
                    params=params,
                )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                is_last = attempt == self._max_retries

                if status == 429 or status >= 500:
                    if status == 429:
                        header_wait = parse_retry_after(
                            exc.response,
                        )
                        base_wait: float = (
                            header_wait
                            if header_wait is not None
                            else self._retry_backoff_factor * (2**attempt)
                        )
                    else:
                        base_wait = self._retry_backoff_factor * (2**attempt)

                    jitter = self._rng.uniform(
                        0.0,
                        base_wait * 0.1,
                    )
                    wait = base_wait + jitter

                    await self._log.awarning(
                        "github_api_retry",
                        status=status,
                        attempt=attempt + 1,
                        wait_seconds=wait,
                        url=url,
                    )

                    if is_last:
                        if status == 429:
                            raise RateLimitError(
                                f"Rate limit on {url}",
                                retry_after=(
                                    parse_retry_after(
                                        exc.response,
                                    )
                                ),
                                resets_at=(
                                    parse_ratelimit_reset(
                                        exc.response,
                                    )
                                ),
                            ) from exc
                        raise TransientAPIError(
                            f"Server error {status} on {url}",
                        ) from exc

                    await asyncio.sleep(wait)
                    continue

                raise

            except httpx.TransportError as exc:
                is_last = attempt == self._max_retries
                base_wait = self._retry_backoff_factor * (2**attempt)
                jitter = self._rng.uniform(
                    0.0,
                    base_wait * 0.1,
                )
                wait = base_wait + jitter

                await self._log.awarning(
                    "github_api_transport_error",
                    error=str(exc),
                    attempt=attempt + 1,
                    wait_seconds=wait,
                    url=url,
                )

                if is_last:
                    raise TransientAPIError(
                        f"Transport error on {url}: {exc}",
                    ) from exc

                await asyncio.sleep(wait)

        raise TransientAPIError(
            f"Request failed after retries: {url}",
        )

    # -- PRCreator -----------------------------------------------------------

    async def create_pr(
        self,
        *,
        repo_url: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> tuple[str, int]:
        """Open a pull request. Returns (html_url, number)."""
        owner, repo = extract_owner_repo(repo_url)
        response = await self._request_with_retry(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
        )
        result = PullRequestResponse.model_validate(
            response.json(),
        )
        return (result.html_url, result.number)

    async def comment_on_pr(
        self,
        *,
        repo_url: str,
        pr_number: int,
        body: str,
    ) -> None:
        """Post a comment on a pull request (via issues API)."""
        owner, repo = extract_owner_repo(repo_url)
        await self._request_with_retry(
            "POST",
            f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
            json={"body": body},
        )

    # -- CIMonitor -----------------------------------------------------------

    async def _detect_ci(
        self,
        owner: str,
        repo: str,
        ref: str,
    ) -> bool:
        """Probe Check Suites API to see if CI is configured."""
        probe_interval = min(self._ci_poll_interval, 10.0)
        for attempt in range(self._ci_no_checks_grace_polls):
            response = await self._request_with_retry(
                "GET",
                f"/repos/{owner}/{repo}/commits/{ref}/check-suites",
            )
            result = CheckSuitesResponse.model_validate(
                response.json(),
            )
            if result.total_count > 0:
                await self._log.ainfo(
                    "ci_suites_detected",
                    count=result.total_count,
                    attempt=attempt + 1,
                )
                return True
            await asyncio.sleep(probe_interval)

        await self._log.ainfo(
            "ci_no_suites_found",
            grace_polls=self._ci_no_checks_grace_polls,
        )
        return False

    async def wait_for_checks(
        self,
        *,
        repo_url: str,
        ref: str,
    ) -> tuple[bool | None, str]:
        """Poll Check Runs API until all checks complete or timeout.

        Phase 1: detect CI via Check Suites.
        Phase 2: poll Check Runs until completion.

        Returns ``(True, ...)`` when all checks pass, ``(False, ...)``
        on failure or timeout, ``(None, ...)`` when no CI configured.
        """
        owner, repo = extract_owner_repo(repo_url)

        # Phase 1 — CI detection
        if not await self._detect_ci(owner, repo, ref):
            return (
                None,
                "No CI checks configured for this ref.",
            )

        # Phase 2 — poll Check Runs
        for _ in range(self._ci_poll_max_attempts):
            response = await self._request_with_retry(
                "GET",
                f"/repos/{owner}/{repo}/commits/{ref}/check-runs",
                params={"per_page": 100},
            )
            result = CheckRunsResponse.model_validate(
                response.json(),
            )

            if result.total_count == 0:
                await asyncio.sleep(self._ci_poll_interval)
                continue

            if any(run.status != "completed" for run in result.check_runs):
                await asyncio.sleep(self._ci_poll_interval)
                continue

            failed_names = [
                run.name
                for run in result.check_runs
                if run.conclusion in self._FAILURE_CONCLUSIONS
            ]
            if failed_names:
                msg = f"CI failed: {', '.join(failed_names)}"
                return (False, msg)

            if all(run.conclusion in self._OK_CONCLUSIONS for run in result.check_runs):
                return (True, "All CI checks passed.")

            await asyncio.sleep(self._ci_poll_interval)

        return (
            False,
            f"CI checks still running after {self._ci_poll_max_attempts} polls.",
        )

    # -- Lifecycle -----------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying httpx connection pool."""
        await self._client.aclose()
