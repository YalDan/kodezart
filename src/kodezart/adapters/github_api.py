"""GitHub REST API adapter — implements PRCreator and CIMonitor protocols."""

import asyncio
import secrets
from enum import StrEnum

import httpx

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.domain.errors import RateLimitError, TransientAPIError
from kodezart.domain.git_url import extract_owner_repo
from kodezart.types.domain.github import (
    CheckRun,
    CheckRunsResponse,
    PullRequestResponse,
    WorkflowsResponse,
)
from kodezart.utils.http import parse_ratelimit_reset, parse_retry_after


class WorkflowsProbeResult(StrEnum):
    """Classification of a repository's GitHub Actions workflow listing.

    Selects which grace window an empty check-runs streak is measured
    against.  Internal to the adapter — never crosses the CIMonitor port.
    """

    ACTIVE = "active"
    NONE_ACTIVE = "none_active"
    INDETERMINATE = "indeterminate"


class GitHubAPIClient:
    """Single adapter satisfying both PRCreator and CIMonitor protocols.

    Uses httpx.AsyncClient for async HTTP. API responses are validated
    via frozen Pydantic models (``CheckRunsResponse``, ``PullRequestResponse``,
    ``WorkflowsResponse``).
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
    _ACTIVE_WORKFLOW_STATE = "active"
    _NOT_FOUND_STATUS = 404
    _PAGE_SIZE = 100
    _NO_WORKFLOWS_SUMMARY = (
        "No CI checks configured: repository has no active workflows."
    )

    def __init__(
        self,
        *,
        token: str,
        base_url: str,
        ci_poll_interval_seconds: float,
        ci_poll_max_attempts: int,
        ci_no_checks_grace_polls: int,
        ci_no_workflows_grace_polls: int,
        ci_grace_poll_interval_seconds: float,
        ci_ref_not_found_grace_polls: int,
        timeout_seconds: float,
        max_retries: int,
        retry_backoff_factor: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._ci_poll_interval: float = ci_poll_interval_seconds
        self._ci_poll_max_attempts: int = ci_poll_max_attempts
        self._ci_no_checks_grace_polls: int = ci_no_checks_grace_polls
        self._ci_no_workflows_grace_polls: int = ci_no_workflows_grace_polls
        self._ci_grace_poll_interval: float = ci_grace_poll_interval_seconds
        self._ci_ref_not_found_grace_polls: int = ci_ref_not_found_grace_polls
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

    def _grace_polls_for(self, probe: WorkflowsProbeResult) -> int:
        """Grace window an empty check-runs streak is measured against."""
        if probe is WorkflowsProbeResult.NONE_ACTIVE:
            return self._ci_no_workflows_grace_polls
        return self._ci_no_checks_grace_polls

    def _no_checks_summary(
        self,
        probe: WorkflowsProbeResult,
        grace_polls: int,
    ) -> str:
        """Terminal no-CI summary for the selected grace window."""
        if probe is WorkflowsProbeResult.NONE_ACTIVE:
            return self._NO_WORKFLOWS_SUMMARY
        return f"No CI checks appeared for this ref after {grace_polls} polls."

    def _verdict(self, page: CheckRunsResponse) -> tuple[bool, str] | None:
        """Terminal pass/fail verdict for a page, or None while pending.

        A run set shorter than ``total_count`` is never terminal: the
        listing is incomplete (runs were added while paginating, or the
        API reported a count it did not enumerate), and a verdict drawn
        from it would report a pass this adapter did not verify.
        """
        if page.total_count == 0:
            return None
        if len(page.check_runs) != page.total_count:
            return None
        if any(run.status != "completed" for run in page.check_runs):
            return None
        failed_names = [
            run.name
            for run in page.check_runs
            if run.conclusion in self._FAILURE_CONCLUSIONS
        ]
        if failed_names:
            return (False, f"CI failed: {', '.join(failed_names)}")
        if all(run.conclusion in self._OK_CONCLUSIONS for run in page.check_runs):
            return (True, "All CI checks passed.")
        return None

    async def _fetch_check_runs(
        self,
        owner: str,
        repo: str,
        ref: str,
    ) -> CheckRunsResponse | None:
        """Fetch every check-runs page for *ref*, or ``None`` when it 404s.

        Pages until the collected runs reach the reported ``total_count``
        so the verdict is drawn from the whole run set, not the first
        page.  A page carrying no runs ends the walk — the count is then
        larger than what the API enumerated and ``_verdict`` treats the
        result as pending.

        A 404 means the ref is not yet visible to the checks API — a
        transient condition on a freshly pushed commit.  Every other
        status error propagates.
        """
        collected: list[CheckRun] = []
        page_number = 1
        while True:
            try:
                response = await self._request_with_retry(
                    "GET",
                    f"/repos/{owner}/{repo}/commits/{ref}/check-runs",
                    params={"per_page": self._PAGE_SIZE, "page": page_number},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == self._NOT_FOUND_STATUS:
                    return None
                raise
            page = CheckRunsResponse.model_validate(
                response.json(),
            )
            collected.extend(page.check_runs)
            if not page.check_runs or len(collected) >= page.total_count:
                return CheckRunsResponse(
                    total_count=page.total_count,
                    check_runs=collected,
                )
            page_number += 1

    async def _probe_workflows(
        self,
        owner: str,
        repo: str,
    ) -> WorkflowsProbeResult:
        """Classify the repository's Actions workflows.

        A listing carrying more workflows than the page returned errs
        toward ``ACTIVE`` — the longer grace window.

        Every request failure degrades to ``INDETERMINATE``, including a
        retry-exhausted rate limit (``RateLimitError`` is a
        ``TransientAPIError``): the probe only ever selects a grace
        window, and no probe failure may end the call.
        """
        try:
            response = await self._request_with_retry(
                "GET",
                f"/repos/{owner}/{repo}/actions/workflows",
                params={"per_page": self._PAGE_SIZE},
            )
        except (httpx.HTTPStatusError, TransientAPIError) as exc:
            await self._log.awarning(
                "ci_workflows_probe_failed",
                error=str(exc),
                grace_polls=self._ci_no_checks_grace_polls,
            )
            return WorkflowsProbeResult.INDETERMINATE

        result = WorkflowsResponse.model_validate(
            response.json(),
        )
        has_active = any(
            workflow.state == self._ACTIVE_WORKFLOW_STATE
            for workflow in result.workflows
        )
        probe = (
            WorkflowsProbeResult.ACTIVE
            if has_active or result.total_count > len(result.workflows)
            else WorkflowsProbeResult.NONE_ACTIVE
        )
        await self._log.ainfo(
            "ci_workflows_probed",
            result=probe,
            total_count=result.total_count,
            grace_polls=self._grace_polls_for(probe),
        )
        return probe

    async def wait_for_checks(
        self,
        *,
        repo_url: str,
        ref: str,
    ) -> tuple[bool | None, str]:
        """Poll Check Runs API until all checks complete or timeout.

        Single loop.  While no check run has ever been observed, poll at
        the grace cadence and count consecutive empty pages; the streak
        reaching the grace window selected by a lazy, call-local
        workflows probe concludes no CI.  Once any run is observed, poll
        at the standard cadence and evaluate until the poll budget is
        exhausted — an empty page after observation is pending, never
        no-CI.  Sleeps occur strictly between polls.

        A 404 on the check-runs page advances neither counter and is
        tolerated up to ``ci_ref_not_found_grace_polls`` consecutive
        occurrences; beyond that the call raises ``TransientAPIError``.

        Returns ``(True, ...)`` when all checks pass, ``(False, ...)``
        on failure or timeout, ``(None, ...)`` when no CI ran.
        """
        owner, repo = extract_owner_repo(repo_url)
        grace_interval = min(self._ci_poll_interval, self._ci_grace_poll_interval)

        probe: WorkflowsProbeResult | None = None
        grace_polls: int = self._ci_no_checks_grace_polls
        runs_observed = False
        empty_polls = 0
        not_found_polls = 0
        polls_used = 0

        while True:
            page = await self._fetch_check_runs(owner, repo, ref)

            if page is None:
                not_found_polls += 1
                if not_found_polls > self._ci_ref_not_found_grace_polls:
                    msg = (
                        f"Check runs for {ref} not found after "
                        f"{not_found_polls} consecutive polls."
                    )
                    raise TransientAPIError(msg)
                await self._log.awarning(
                    "ci_ref_not_found_tolerated",
                    ref=ref,
                    consecutive=not_found_polls,
                )
                await asyncio.sleep(
                    self._ci_poll_interval if runs_observed else grace_interval
                )
                continue

            not_found_polls = 0

            if not runs_observed and page.total_count == 0:
                empty_polls += 1
                if probe is None:
                    probe = await self._probe_workflows(owner, repo)
                    grace_polls = self._grace_polls_for(probe)
                if empty_polls >= grace_polls:
                    await self._log.ainfo(
                        "ci_no_checks_concluded",
                        ref=ref,
                        result=probe,
                        grace_polls=grace_polls,
                    )
                    return (None, self._no_checks_summary(probe, grace_polls))
                await asyncio.sleep(grace_interval)
                continue

            if not runs_observed:
                runs_observed = True
                await self._log.ainfo(
                    "ci_runs_observed",
                    ref=ref,
                    count=page.total_count,
                )

            polls_used += 1
            verdict = self._verdict(page)
            if verdict is not None:
                return verdict

            if polls_used >= self._ci_poll_max_attempts:
                attempts = self._ci_poll_max_attempts
                return (False, f"CI checks still running after {attempts} polls.")
            await asyncio.sleep(self._ci_poll_interval)

    # -- Lifecycle -----------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying httpx connection pool."""
        await self._client.aclose()
