"""GitHub REST API adapter — implements forge write, query and CI protocols.

``httpx`` and this forge's wire shapes are the module's private business.
No NON-DOMAIN exception leaves a port method: every request goes through
``_request_with_retry``, whose arms are total over the exception types
httpx publishes, and every body read goes through ``_parsed_with_retry``,
whose arm is total over the ways a payload can fail to become a wire
model.  What comes out is ``RateLimitError`` / ``TransientAPIError`` for
the retry-eligible failures and ``ForgeAPIError`` for the rest.

The one deliberate exception is ``extract_owner_repo``'s ``ValueError``
on an origin this forge does not own.  That is a domain refusal rather
than a vendor leak, it is raised before any request, and the composition
root routes such origins to another adapter rather than here.
"""

import asyncio
import re
import secrets
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final, TypeVar
from urllib.parse import quote, urlsplit

import httpx

from kodezart.adapters.github_types import (
    CheckRun,
    CheckRunsResponse,
    CommitIdentity,
    DeclaredWorkflowsResponse,
    PullRequestResponse,
    PullRequestStateResponse,
    PullRequestSummary,
    RepositoryResponse,
    WorkflowJob,
    WorkflowJobsResponse,
    WorkflowRun,
    WorkflowRunsResponse,
    WorkflowsResponse,
)
from kodezart.core.backoff import RetryPolicy
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.domain.errors import (
    CheckObservationError,
    ForgeAPIError,
    PRStateReadError,
    RateLimitError,
    TransientAPIError,
)
from kodezart.domain.git_url import extract_owner_repo
from kodezart.types.domain.check_observation import ObservedChecks
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.pr_state import PRLifecycle, PRState
from kodezart.utils.http import parse_ratelimit_reset, parse_retry_after

#: Every root httpx derives an exception from.  ``HTTPError`` covers the
#: request and status families; the other three are its siblings, not its
#: subclasses, so naming the union is what makes the translation total.
#: A bare ``Exception`` here would swallow this adapter's own defects.
_VENDOR_FAILURE: Final[tuple[type[Exception], ...]] = (
    httpx.HTTPError,
    httpx.InvalidURL,
    httpx.CookieConflict,
    httpx.StreamError,
)

_WireT = TypeVar("_WireT")


@dataclass(frozen=True)
class _RerunTarget:
    run: WorkflowRun
    previous_job_ids: frozenset[int]


@dataclass(frozen=True)
class _RerunBatch:
    targets: tuple[_RerunTarget, ...]
    dispatched: bool = False


@dataclass(frozen=True)
class _RerunContext:
    task: object
    batches: Mapping[tuple[str, str, str], _RerunBatch]


@dataclass(frozen=True)
class _CompletedWatch:
    checks: tuple[CheckRun, ...]
    total_count: int
    passed: bool


@dataclass(frozen=True)
class _WatchContext:
    task: object
    observations: Mapping[tuple[str, str, str], _CompletedWatch]


def _pull_request_listing(payload: object) -> tuple[PullRequestSummary, ...]:
    """The open pull requests, which arrive as a BARE JSON array.

    No envelope to unwrap, so there is no wrapper model to validate and
    the array shape is checked here.  A payload that is not an array is
    refused as a ``ValueError``, the same class the wire model raises on
    an entry it cannot accept, so both reach one translation.
    """
    if not isinstance(payload, list):
        msg = f"expected a pull request array, got {type(payload).__name__}"
        raise ValueError(msg)
    return tuple(PullRequestSummary.model_validate(entry) for entry in payload)


class WorkflowsProbeResult(StrEnum):
    """Classification of a repository's GitHub Actions workflow listing.

    Selects which grace window an empty check-runs streak is measured
    against.  Internal to the adapter — never crosses the CIMonitor port.
    """

    ACTIVE = "active"
    NONE_ACTIVE = "none_active"
    INDETERMINATE = "indeterminate"


class GitHubAPIClient:
    """Single adapter serving active PR writes, check watches and audit reads.

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
    _PENDING_STATUSES = frozenset(
        {"queued", "in_progress", "waiting", "pending", "requested"}
    )
    _ACTIVE_WORKFLOW_STATE = "active"
    _NOT_FOUND_STATUS = 404
    _PAGE_SIZE = 100
    _OPEN_STATE = "open"
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
        ci_check_runs_max_pages: int,
        timeout_seconds: float,
        retry: RetryPolicy,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._ci_poll_interval: float = ci_poll_interval_seconds
        self._ci_poll_max_attempts: int = ci_poll_max_attempts
        self._ci_no_checks_grace_polls: int = ci_no_checks_grace_polls
        self._ci_no_workflows_grace_polls: int = ci_no_workflows_grace_polls
        self._ci_grace_poll_interval: float = ci_grace_poll_interval_seconds
        self._ci_ref_not_found_grace_polls: int = ci_ref_not_found_grace_polls
        self._ci_check_runs_max_pages: int = ci_check_runs_max_pages
        self._retry = retry
        self._reruns: ContextVar[_RerunContext | None] = ContextVar(
            "github_ci_rerun_context", default=None
        )
        self._watched_checks: ContextVar[_WatchContext | None] = ContextVar(
            "github_completed_check_watches", default=None
        )
        self._rerun_dispatch_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self._rerun_attempt_floors: dict[tuple[str, str, int], int] = {}
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
        retryable: bool = True,
    ) -> httpx.Response:
        """HTTP request with the configured bounded backoff and jitter.

        Raises ``RateLimitError`` / ``TransientAPIError`` once the retry
        budget is spent and ``ForgeAPIError`` on a failure no retry would
        change.  No ``httpx`` exception leaves this method, because the
        ports above it speak the domain taxonomy.

        Three arms, total over ``_VENDOR_FAILURE``: a status the server
        answered with, a transport failure worth another attempt, and
        everything else httpx can raise — a body that would not decode, a
        redirect loop, a URL the client would not build.  The third arm
        is NOT retried, because none of those is a condition a second
        identical request finds changed, and it carries no status
        because none was ever received.
        """
        attempts = self._retry.attempts if retryable else 1
        for attempt in range(attempts):
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
                is_last = attempt + 1 == attempts

                if status == 429 or status >= 500:
                    wait = self._retry.delay(
                        attempt,
                        retry_after=parse_retry_after(exc.response)
                        if status == 429
                        else None,
                        rng=self._rng,
                    )

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

                raise ForgeAPIError(
                    "Forge refused the request",
                    status_code=status,
                    detail=f"{method} {url}",
                ) from exc

            except httpx.TransportError as exc:
                is_last = attempt + 1 == attempts
                wait = self._retry.delay(attempt, rng=self._rng)

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

            except _VENDOR_FAILURE as exc:
                raise ForgeAPIError(
                    f"Forge request failed: {type(exc).__name__}",
                    status_code=None,
                    detail=f"{method} {url}",
                ) from exc

        raise TransientAPIError(
            f"Request failed after retries: {url}",
        )

    async def _parsed_with_retry(
        self,
        method: str,
        url: str,
        parse: Callable[[object], _WireT],
        *,
        json: dict[str, object] | None = None,
        params: dict[str, str | int] | None = None,
        retryable: bool = True,
    ) -> _WireT:
        """One request, with its body decoded and validated.

        The single seam every body read goes through, so the translation
        below is stated ONCE rather than at each reader.  Both ways a
        payload fails to become a wire model are ``ValueError``: the JSON
        decoder raises one on bytes that are not JSON, and pydantic's
        ``ValidationError`` IS one.  Catching the superset is exact here
        and keeps the two from needing separate arms that could drift.

        A body this adapter cannot read is a forge failure like any
        other, and it carries the status the response really had — the
        request was answered, and what came back was unusable.
        """
        response = await self._request_with_retry(
            method,
            url,
            json=json,
            params=params,
            retryable=retryable,
        )
        try:
            return parse(response.json())
        except ValueError as exc:
            raise ForgeAPIError(
                f"Forge answered with a body this adapter cannot read: "
                f"{type(exc).__name__}",
                status_code=response.status_code,
                detail=f"{method} {url}",
            ) from exc

    # -- RepoVisibilityResolver ---------------------------------------------

    async def resolve_visibility(self, *, repo_url: str) -> RepoVisibility:
        """Resolve visibility via ``GET /repos/{owner}/{repo}``.

        Fail-closed: any failure yields ``UNKNOWN``, which takes the public
        path with the gate engaged.  Never raises, never skips.
        """
        try:
            owner, repo = extract_owner_repo(repo_url)
            result = await self._parsed_with_retry(
                "GET",
                f"/repos/{owner}/{repo}",
                RepositoryResponse.model_validate,
            )
        except Exception as exc:
            await self._log.awarning(
                "repo_visibility_resolution_failed",
                error=str(exc),
                error_kind=type(exc).__name__,
            )
            return RepoVisibility.UNKNOWN
        return RepoVisibility.PRIVATE if result.private else RepoVisibility.PUBLIC

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
        result = await self._parsed_with_retry(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            PullRequestResponse.model_validate,
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
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

    # -- ForgeQuery ----------------------------------------------------------

    async def open_pr_for_head(
        self, *, repo_url: str, head: str
    ) -> tuple[str, int] | None:
        """Ask this forge's own head filter, and refuse a contradictory answer.

        The filter is the forge's: its listing takes ``owner:branch`` and
        answers with the open pull requests on that head, so the question
        is asked once, of the party that knows, instead of being
        reconstructed by paging every open pull request and matching here.
        """
        owner, repo = extract_owner_repo(repo_url)
        listing = await self._parsed_with_retry(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            _pull_request_listing,
            params={
                "state": self._OPEN_STATE,
                "head": f"{owner}:{head}",
                "per_page": self._PAGE_SIZE,
            },
        )
        if not listing:
            return None
        if len(listing) > 1:
            raise ForgeAPIError(
                f"the forge reports {len(listing)} open pull requests on one head",
                status_code=None,
                detail=f"GET /repos/{owner}/{repo}/pulls?head={owner}:{head}",
            )
        return (listing[0].html_url, listing[0].number)

    def branch_web_url(self, *, repo_url: str, branch: str) -> str:
        """Compose the branch page from this repository's own host and path.

        The host comes from the origin rather than from the configured API
        base: this forge serves its API and its pages from two different
        hosts, and a deployment against an enterprise instance has both of
        them different again.
        """
        owner, repo = extract_owner_repo(repo_url)
        origin = urlsplit(repo_url)
        if origin.scheme != "https" or not origin.netloc:
            msg = f"Cannot compose a branch page for origin: {repo_url}"
            raise ValueError(msg)
        return f"https://{origin.netloc}/{owner}/{repo}/tree/{quote(branch)}"

    # -- PRStateReader -------------------------------------------------------

    async def read_pr_state(self, *, repo_url: str, pr_number: int) -> PRState:
        """Read one native PR, without relying on an open-only listing."""
        if (
            not isinstance(pr_number, int)
            or isinstance(pr_number, bool)
            or pr_number <= 0
        ):
            raise PRStateReadError("a PR number must be a positive integer")
        owner, repo = extract_owner_repo(repo_url)
        native = await self._parsed_with_retry(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pr_number}",
            PullRequestStateResponse.model_validate,
        )
        expected = urlsplit(repo_url)
        try:
            observed = urlsplit(native.html_url)
        except ValueError as exc:
            raise PRStateReadError("native PR URL is malformed") from exc
        expected_path = f"/{owner}/{repo}/pull/{pr_number}"
        if (
            native.number != pr_number
            or observed.scheme != "https"
            or observed.netloc.casefold() != expected.netloc.casefold()
            or observed.path.casefold() != expected_path.casefold()
            or observed.query
            or observed.fragment
            or observed.username is not None
            or observed.password is not None
        ):
            raise PRStateReadError("native PR identity differs from its address")
        head_repo = native.head.repo
        if head_repo is None:
            raise PRStateReadError("native PR head repository is unavailable")
        try:
            head_origin = urlsplit(head_repo.html_url)
        except ValueError as exc:
            raise PRStateReadError(
                "native PR head repository URL is malformed"
            ) from exc
        if (
            head_origin.scheme != "https"
            or head_origin.netloc.casefold() != expected.netloc.casefold()
            or head_origin.path.casefold() != f"/{owner}/{repo}".casefold()
            or head_origin.query
            or head_origin.fragment
            or head_origin.username is not None
            or head_origin.password is not None
            or head_repo.full_name.casefold() != f"{owner}/{repo}".casefold()
        ):
            raise PRStateReadError("native PR head belongs to another repository")
        lifecycle = (
            PRLifecycle.MERGED
            if native.merged
            else PRLifecycle.OPEN
            if native.state == self._OPEN_STATE
            else PRLifecycle.CLOSED
        )
        return PRState(
            url=native.html_url,
            number=native.number,
            head_repo_url=head_repo.html_url,
            head_branch=native.head.ref,
            head_sha=native.head.sha,
            lifecycle=lifecycle,
        )

    # -- DeliveryProbe -------------------------------------------------------

    async def open_delivery_exists(
        self,
        *,
        repo_url: str,
        issue_key: str,
    ) -> bool:
        """True iff an OPEN pull request references *issue_key*.

        Matching lives here, not in the caller: the reference convention is
        a property of this forge's pull requests.  The key is matched as a
        whole token in the title or body, never as the prefix of a longer
        key.  A branch name is never parsed — an issue identity is
        not derivable from one.
        """
        owner, repo = extract_owner_repo(repo_url)
        listing = await self._parsed_with_retry(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            _pull_request_listing,
            params={"state": self._OPEN_STATE, "per_page": self._PAGE_SIZE},
        )
        pattern = re.compile(rf"(?<![\w-]){re.escape(issue_key)}(?![\w-])")
        for summary in listing:
            if pattern.search(summary.title) or pattern.search(summary.body or ""):
                return True
        return False

    # -- CIMonitor -----------------------------------------------------------

    def _rerun_context(self) -> Mapping[tuple[str, str, str], _RerunBatch]:
        context = self._reruns.get()
        if context is None or context.task is not asyncio.current_task():
            return {}
        return context.batches

    def _remember_rerun(
        self, owner: str, repo: str, ref: str, batch: _RerunBatch
    ) -> None:
        batches = dict(self._rerun_context())
        batches[(owner, repo, ref)] = batch
        self._reruns.set(_RerunContext(task=asyncio.current_task(), batches=batches))

    @staticmethod
    def _rerun_error(message: str) -> ForgeAPIError:
        return ForgeAPIError(message, status_code=None, detail="CI rerun observation")

    def _terminal_checks(self, page: CheckRunsResponse) -> None:
        if (
            len(page.check_runs) != page.total_count
            or len({run.id for run in page.check_runs}) != page.total_count
        ):
            raise self._rerun_error(
                "Check observation was incomplete or repeated identities"
            )
        if any(
            run.status != "completed"
            or run.conclusion not in self._FAILURE_CONCLUSIONS | self._OK_CONCLUSIONS
            for run in page.check_runs
        ):
            raise self._rerun_error("Check observation was not terminal and understood")

    async def _attempt_jobs(
        self, owner: str, repo: str, run: WorkflowRun, attempt: int
    ) -> tuple[WorkflowJob, ...]:
        jobs: dict[int, WorkflowJob] = {}
        expected_total: int | None = None
        for page_number in range(1, self._ci_check_runs_max_pages + 1):
            page = await self._parsed_with_retry(
                "GET",
                f"/repos/{owner}/{repo}/actions/runs/{run.id}/attempts/{attempt}/jobs",
                WorkflowJobsResponse.model_validate,
                params={"per_page": self._PAGE_SIZE, "page": page_number},
            )
            if expected_total is not None and page.total_count != expected_total:
                raise self._rerun_error("Attempt jobs changed during pagination")
            expected_total = page.total_count
            for job in page.jobs:
                if job.id in jobs:
                    raise self._rerun_error("Attempt jobs repeated an identity")
                if job.run_id != run.id or job.head_sha != run.head_sha:
                    raise self._rerun_error(
                        "Attempt job belonged to another run or SHA"
                    )
                jobs[job.id] = job
            if len(jobs) == expected_total:
                return tuple(jobs.values())
            if not page.jobs or len(jobs) > expected_total:
                raise self._rerun_error("Attempt jobs were incompletely enumerated")
        raise self._rerun_error("Attempt jobs exceeded the pagination bound")

    async def rerun_checks(self, *, repo_url: str, ref: str) -> None:
        """Re-run the Actions runs backing every current check, at one SHA.

        All mappings are validated before the first write. POSTs are not
        automatically retried: a lost response cannot safely be turned into
        an additional attempt. A failed batch remains unreadable, including
        when some of its requests succeeded. Its caller receives the error.
        """
        owner, repo = extract_owner_repo(repo_url)
        previous = self._rerun_context().get((owner, repo, ref))
        if previous is not None:
            observed = await self._rerun_observation(owner, repo, previous)
            if observed is None:
                raise self._rerun_error("The preceding rerun has not completed")
        encoded_ref = quote(ref, safe="")
        commit = await self._parsed_with_retry(
            "GET",
            f"/repos/{owner}/{repo}/commits/{encoded_ref}",
            CommitIdentity.model_validate,
        )
        if re.fullmatch(r"[0-9a-fA-F]{40}", ref) and commit.sha.lower() != ref.lower():
            raise self._rerun_error("Resolved commit did not match the requested SHA")
        key = (owner.casefold(), repo.casefold(), commit.sha.lower())
        lock = self._rerun_dispatch_locks.setdefault(key, asyncio.Lock())
        async with lock:
            await self._dispatch_rerun(owner, repo, ref, commit)

    async def _dispatch_rerun(
        self, owner: str, repo: str, ref: str, commit: CommitIdentity
    ) -> None:
        """Serialize baseline selection and writes across aliases of one SHA."""
        page = await self._fetch_check_runs(
            owner, repo, commit.sha, require_stable_total=True
        )
        if page is None or not page.check_runs:
            raise self._rerun_error("No complete check set exists to rerun")
        self._terminal_checks(page)
        suites: dict[int, set[int]] = {}
        for check in page.check_runs:
            if check.check_suite is None or check.head_sha != commit.sha:
                raise self._rerun_error(
                    "Check suite or matching commit identity was absent"
                )
            suites.setdefault(check.check_suite.id, set()).add(check.id)

        targets: list[_RerunTarget] = []
        for suite_id, check_ids in sorted(suites.items()):
            listing = await self._parsed_with_retry(
                "GET",
                f"/repos/{owner}/{repo}/actions/runs",
                WorkflowRunsResponse.model_validate,
                params={
                    "head_sha": commit.sha,
                    "check_suite_id": suite_id,
                    "per_page": self._PAGE_SIZE,
                },
            )
            if listing.total_count != 1 or len(listing.workflow_runs) != 1:
                raise self._rerun_error(
                    "Check suite did not map to exactly one Actions run"
                )
            run = listing.workflow_runs[0]
            observed_attempt = self._rerun_attempt_floors.get(
                (owner.casefold(), repo.casefold(), run.id)
            )
            if observed_attempt is not None and run.run_attempt < observed_attempt:
                raise self._rerun_error(
                    "Actions listing preceded an already observed attempt"
                )
            if run.check_suite_id != suite_id or run.head_sha != commit.sha:
                raise self._rerun_error(
                    "Actions run did not match its check suite and SHA"
                )
            if (
                run.status != "completed"
                or run.conclusion
                not in self._FAILURE_CONCLUSIONS | self._OK_CONCLUSIONS
            ):
                raise self._rerun_error("Actions run was not terminal and understood")
            jobs = await self._attempt_jobs(owner, repo, run, run.run_attempt)
            self._terminal_checks(
                CheckRunsResponse(total_count=len(jobs), check_runs=list(jobs))
            )
            expected_urls = {
                str(
                    self._client.build_request(
                        "GET", f"/repos/{owner}/{repo}/check-runs/{check_id}"
                    ).url
                )
                for check_id in check_ids
            }
            if {job.check_run_url for job in jobs} != expected_urls:
                raise self._rerun_error(
                    "Actions attempt did not cover the observed check set"
                )
            targets.append(
                _RerunTarget(
                    run=run, previous_job_ids=frozenset(job.id for job in jobs)
                )
            )

        if len({target.run.id for target in targets}) != len(targets):
            raise self._rerun_error("Check suites repeated a workflow run identity")
        batch = _RerunBatch(targets=tuple(targets))
        self._remember_rerun(owner, repo, ref, batch)
        for target in batch.targets:
            self._rerun_attempt_floors[
                (owner.casefold(), repo.casefold(), target.run.id)
            ] = target.run.run_attempt + 1
        for target in batch.targets:
            response = await self._request_with_retry(
                "POST",
                f"/repos/{owner}/{repo}/actions/runs/{target.run.id}/rerun",
                retryable=False,
            )
            if response.status_code != 201:
                raise self._rerun_error("Forge did not confirm creation of the rerun")
        self._remember_rerun(owner, repo, ref, replace(batch, dispatched=True))

    async def _rerun_observation(
        self, owner: str, repo: str, batch: _RerunBatch
    ) -> CheckRunsResponse | None:
        if not batch.dispatched:
            raise self._rerun_error(
                "Rerun dispatch was incomplete or its response was lost"
            )
        checks: list[CheckRun] = []
        for target in batch.targets:
            expected = target.run.run_attempt + 1
            try:
                run = await self._parsed_with_retry(
                    "GET",
                    f"/repos/{owner}/{repo}/actions/runs/{target.run.id}/attempts/{expected}",
                    WorkflowRun.model_validate,
                )
            except ForgeAPIError as exc:
                if exc.status_code == self._NOT_FOUND_STATUS:
                    return None
                raise
            if (
                run.id != target.run.id
                or run.head_sha != target.run.head_sha
                or run.check_suite_id != target.run.check_suite_id
            ):
                raise self._rerun_error(
                    "Rerun identity changed from the requested run and SHA"
                )
            if run.run_attempt < expected:
                return None
            if run.run_attempt != expected:
                raise self._rerun_error("Forge returned another rerun attempt")
            if run.status in self._PENDING_STATUSES:
                return None
            if run.status != "completed":
                raise self._rerun_error("Rerun carried an unknown status")
            if run.conclusion not in self._FAILURE_CONCLUSIONS | self._OK_CONCLUSIONS:
                raise self._rerun_error("Rerun carried an unknown conclusion")
            jobs = await self._attempt_jobs(owner, repo, run, expected)
            if not jobs:
                raise self._rerun_error("Completed rerun contained no observable jobs")
            if any(job.id in target.previous_job_ids for job in jobs):
                return None
            if any(job.status in self._PENDING_STATUSES for job in jobs):
                return None
            page = CheckRunsResponse(total_count=len(jobs), check_runs=list(jobs))
            self._terminal_checks(page)
            verdict = self._verdict(page)
            if verdict is None or verdict[0] != (
                run.conclusion in self._OK_CONCLUSIONS
            ):
                raise self._rerun_error("Rerun conclusion disagreed with its jobs")
            checks.extend(jobs)
        page = CheckRunsResponse(total_count=len(checks), check_runs=checks)
        self._terminal_checks(page)
        return page

    async def _wait_for_rerun(
        self, owner: str, repo: str, ref: str, batch: _RerunBatch
    ) -> tuple[bool | None, str]:
        for poll in range(self._ci_poll_max_attempts):
            page = await self._rerun_observation(owner, repo, batch)
            if page is not None:
                verdict = self._verdict(page)
                if verdict is not None:
                    self._remember_watch(owner, repo, ref, page, verdict[0])
                    return verdict
            if poll + 1 < self._ci_poll_max_attempts:
                await asyncio.sleep(self._ci_poll_interval)
        raise TransientAPIError(
            "Requested CI rerun was not observable within the poll bound"
        )

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
        """Terminal pass/fail verdict for the run set, or None while pending.

        A run set shorter than its own ``total_count`` is never terminal,
        and that single rule covers both ways the listing comes up short: a
        page the walk could not reach, and a ``total_count`` the API
        reported but did not enumerate.  Either way the adapter is holding
        less evidence than the ref has, and a verdict drawn from it would
        report a pass nobody verified.
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
        *,
        require_stable_total: bool = False,
    ) -> CheckRunsResponse | None:
        """Fetch every check-runs page for *ref*, or ``None`` when it 404s.

        Walks pages until the collected runs reach the reported
        ``total_count``, so the verdict is drawn from the whole run set
        rather than from the first hundred runs.

        One logical poll, however many pages it takes, costs exactly ONE
        ``ci_poll_max_attempts`` unit: the walk answers a single question —
        what are this ref's check runs right now — and the caller counts it
        once.

        The walk is bounded by ``ci_check_runs_max_pages``.  Hitting the cap
        returns what was collected, which is necessarily shorter than
        ``total_count`` and therefore PENDING by ``_verdict``'s rule — never
        a timeout, and never a ``TransientAPIError``.  A bounded cap is an
        incomplete observation, not an error: the next poll re-reads the ref
        from page one, and either the run set fits within the bound or the
        poll budget expires with the ref honestly never verified.

        A page carrying no runs ends the walk under the same rule: the
        reported count is then larger than what the API enumerated, which is
        short, which is pending.

        A 404 means the ref is not yet visible to the checks API — a
        transient condition on a freshly pushed commit.  Every other forge
        failure propagates, an unreadable page included: a walk that
        skipped one would draw a verdict from a run set it knows is
        incomplete.
        """
        collected: list[CheckRun] = []
        reported_total = 0
        for page_number in range(1, self._ci_check_runs_max_pages + 1):
            try:
                page = await self._parsed_with_retry(
                    "GET",
                    f"/repos/{owner}/{repo}/commits/{ref}/check-runs",
                    CheckRunsResponse.model_validate,
                    params={"per_page": self._PAGE_SIZE, "page": page_number},
                )
            except ForgeAPIError as exc:
                if exc.status_code == self._NOT_FOUND_STATUS:
                    return None
                raise
            if (
                require_stable_total
                and page_number > 1
                and page.total_count != reported_total
            ):
                raise ForgeAPIError(
                    "Check listing changed during pagination",
                    status_code=None,
                    detail="CI observation",
                )
            reported_total = page.total_count
            collected.extend(page.check_runs)
            if not page.check_runs or len(collected) >= reported_total:
                break
        return CheckRunsResponse(total_count=reported_total, check_runs=collected)

    async def _probe_workflows(
        self,
        owner: str,
        repo: str,
    ) -> WorkflowsProbeResult:
        """Classify the repository's Actions workflows.

        A listing carrying more workflows than the page returned errs
        toward ``ACTIVE`` — the longer grace window.

        Every failure degrades to ``INDETERMINATE``, including a
        retry-exhausted rate limit (``RateLimitError`` is a
        ``TransientAPIError``) and a listing that will not parse: the
        probe only ever selects a grace window, and no probe failure may
        end the call.  The read is INSIDE the guard for that reason —
        an unreadable listing is a listing this probe could not classify,
        which is what ``INDETERMINATE`` means.
        """
        try:
            result = await self._parsed_with_retry(
                "GET",
                f"/repos/{owner}/{repo}/actions/workflows",
                WorkflowsResponse.model_validate,
                params={"per_page": self._PAGE_SIZE},
            )
        except (ForgeAPIError, TransientAPIError) as exc:
            await self._log.awarning(
                "ci_workflows_probe_failed",
                error=str(exc),
                grace_polls=self._ci_no_checks_grace_polls,
            )
            return WorkflowsProbeResult.INDETERMINATE

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

    async def checks_declared(self, *, repo_url: str) -> bool:
        """Whether an active Actions workflow is actually declared.

        The advisory grace-window probe cannot answer this contract: an
        unreadable or incomplete declaration is an error, never absence.
        """
        owner, repo = extract_owner_repo(repo_url)
        seen: set[int] = set()
        expected_total: int | None = None
        for page_number in range(1, self._ci_check_runs_max_pages + 1):
            page = await self._parsed_with_retry(
                "GET",
                f"/repos/{owner}/{repo}/actions/workflows",
                DeclaredWorkflowsResponse.model_validate,
                params={"per_page": self._PAGE_SIZE, "page": page_number},
            )
            if any(
                item.state == self._ACTIVE_WORKFLOW_STATE for item in page.workflows
            ):
                return True
            if expected_total is not None and page.total_count != expected_total:
                raise ForgeAPIError(
                    "Workflow declaration changed during pagination",
                    status_code=None,
                    detail="CI observation",
                )
            expected_total = page.total_count
            if any(
                item.state
                not in {
                    "deleted",
                    "disabled_fork",
                    "disabled_inactivity",
                    "disabled_manually",
                }
                for item in page.workflows
            ):
                raise ForgeAPIError(
                    "Workflow declaration contained an unknown state",
                    status_code=None,
                    detail="CI observation",
                )
            identities = {item.id for item in page.workflows}
            if identities & seen or len(identities) != len(page.workflows):
                raise ForgeAPIError(
                    "Workflow declaration pagination repeated an identity",
                    status_code=None,
                    detail="CI observation",
                )
            seen.update(identities)
            if len(seen) == page.total_count:
                return False
            if not identities or len(seen) > page.total_count:
                raise ForgeAPIError(
                    "Workflow declaration listing was incomplete",
                    status_code=None,
                    detail="CI observation",
                )
        raise ForgeAPIError(
            "Workflow declaration exceeded pagination bound",
            status_code=None,
            detail="CI observation",
        )

    async def failed_check_names(self, *, repo_url: str, ref: str) -> frozenset[str]:
        """Read one failing-set interpretation from the pinned watch or API.

        A rerun selects its own attempt first. Otherwise a completed watch
        in this task owns the original ref's bytes, so a branch move cannot
        replace the original failing set between watching and classifying.
        """
        owner, repo = extract_owner_repo(repo_url)
        batch = self._rerun_context().get((owner, repo, ref))
        watched = self._watch_context().get((owner.casefold(), repo.casefold(), ref))
        if batch is not None:
            page = await self._rerun_observation(owner, repo, batch)
        elif watched is not None:
            page = CheckRunsResponse(
                total_count=watched.total_count, check_runs=list(watched.checks)
            )
        else:
            page = await self._fetch_check_runs(
                owner, repo, ref, require_stable_total=True
            )
        if page is None or len(page.check_runs) != page.total_count:
            raise ForgeAPIError(
                "Failed check names were not completely observable",
                status_code=None,
                detail="CI observation",
            )
        if len({run.id for run in page.check_runs}) != page.total_count:
            raise ForgeAPIError(
                "Failed check listing repeated a check identity",
                status_code=None,
                detail="CI observation",
            )
        if any(
            run.status != "completed"
            or run.conclusion not in self._FAILURE_CONCLUSIONS | self._OK_CONCLUSIONS
            for run in page.check_runs
        ):
            raise ForgeAPIError(
                "Failed check names require a terminal check observation",
                status_code=None,
                detail="CI observation",
            )
        return frozenset(
            run.name
            for run in page.check_runs
            if run.conclusion in self._FAILURE_CONCLUSIONS
        )

    def _watch_context(self) -> Mapping[tuple[str, str, str], _CompletedWatch]:
        context = self._watched_checks.get()
        if context is None or context.task is not asyncio.current_task():
            return {}
        return context.observations

    def _forget_watch(self, owner: str, repo: str, ref: str) -> None:
        observations = dict(self._watch_context())
        observations.pop((owner.casefold(), repo.casefold(), ref), None)
        self._watched_checks.set(
            _WatchContext(task=asyncio.current_task(), observations=observations)
        )

    def _remember_watch(
        self, owner: str, repo: str, ref: str, page: CheckRunsResponse, passed: bool
    ) -> None:
        observations = dict(self._watch_context())
        observations[(owner.casefold(), repo.casefold(), ref)] = _CompletedWatch(
            checks=tuple(page.check_runs), total_count=page.total_count, passed=passed
        )
        self._watched_checks.set(
            _WatchContext(task=asyncio.current_task(), observations=observations)
        )

    async def observed_checks(self, *, repo_url: str, ref: str) -> ObservedChecks:
        """Read this task's exact completed watch, with no new remote query."""
        owner, repo = extract_owner_repo(repo_url)
        watched = self._watch_context().get((owner.casefold(), repo.casefold(), ref))
        reason: str | None = None
        if watched is None or not watched.checks:
            reason = "this task has no completed nonempty check watch"
        elif (
            len(watched.checks) != watched.total_count
            or len({check.id for check in watched.checks}) != watched.total_count
        ):
            reason = "the watched check set was incomplete or repeated identities"
        elif any(
            check.status != "completed"
            or check.conclusion not in self._FAILURE_CONCLUSIONS | self._OK_CONCLUSIONS
            or not check.name
            for check in watched.checks
        ):
            reason = "the watched check set was not terminal and understood"
        if reason is not None:
            raise CheckObservationError(repo_url=repo_url, ref=ref, reason=reason)
        assert watched is not None
        sha = watched.checks[0].head_sha
        if not sha or any(check.head_sha != sha for check in watched.checks):
            raise CheckObservationError(
                repo_url=repo_url,
                ref=ref,
                reason="the watched checks did not identify one immutable commit",
            )
        if re.fullmatch(r"[0-9a-fA-F]{40}", ref) and sha.lower() != ref.lower():
            raise CheckObservationError(
                repo_url=repo_url,
                ref=ref,
                reason="the watched commit differs from the requested SHA",
            )
        return ObservedChecks(
            commit_sha=sha,
            checks_passed=watched.passed,
            check_names=frozenset(check.name for check in watched.checks),
        )

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
        self._forget_watch(owner, repo, ref)
        batch = self._rerun_context().get((owner, repo, ref))
        if batch is not None:
            return await self._wait_for_rerun(owner, repo, ref, batch)
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
                self._remember_watch(owner, repo, ref, page, verdict[0])
                return verdict

            if polls_used >= self._ci_poll_max_attempts:
                attempts = self._ci_poll_max_attempts
                return (False, f"CI checks still running after {attempts} polls.")
            await asyncio.sleep(self._ci_poll_interval)

    # -- Lifecycle -----------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying httpx connection pool."""
        await self._client.aclose()
