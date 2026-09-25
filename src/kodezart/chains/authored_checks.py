"""Observe completed CI and classify bounded same-SHA recovery."""

import asyncio
from collections.abc import Sequence
from typing import NamedTuple

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from kodezart.core.prompt_namespaces import repo_display
from kodezart.core.protocols import CIMonitor, GitService, RepoCache
from kodezart.domain.ci import ci_status_of
from kodezart.domain.errors import (
    CheckObservationError,
)
from kodezart.domain.git_url import resolve_repo_url
from kodezart.services.check_classification import classify_red_checks
from kodezart.services.gained_commits import gained_commits, scope_repositories
from kodezart.types.domain.agent import (
    WorkflowCIEvent,
)
from kodezart.types.domain.check_observation import IncompleteChecks, ObservedChecks
from kodezart.types.domain.delivery import CheckRedClass
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.workflow import (
    AuthoredWorkflowState,
    ExecutionContext,
)


class _Watched(NamedTuple):
    """What one repository's checks came to at the watched ref."""

    passed: bool | None
    summary: str
    red_class: CheckRedClass | None
    run_absent: bool


class AuthoredChecks:
    """Observe completed CI and classify bounded same-SHA recovery."""

    def __init__(
        self,
        *,
        ci_monitor: CIMonitor | None,
        git_base_url: str,
        repositories: Sequence[RepoEntry],
        max_concurrent_watches: int,
        red_rerun_max_attempts: int,
        git: GitService,
        cache: RepoCache,
    ) -> None:
        self._ci_monitor = ci_monitor
        self._git_base_url = git_base_url
        self._git = git
        self._cache = cache
        self._red_rerun_max_attempts = red_rerun_max_attempts
        self._repositories = tuple(repo.model_copy(deep=True) for repo in repositories)
        if max_concurrent_watches < 1 or red_rerun_max_attempts < 0:
            raise ValueError(
                "delivery needs a positive watch bound and nonnegative rerun bound"
            )
        self._watch_slots = asyncio.Semaphore(max_concurrent_watches)

    @property
    def available(self) -> bool:
        """Whether this origin has the native checks surface."""
        return self._ci_monitor is not None

    async def monitor_ci(
        self,
        state: AuthoredWorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Poll CI status for the latest commit on the feature branch."""
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        ci_monitor = self._ci_monitor
        if ci_monitor is None:
            msg = "monitor_ci requires ci_monitor but self._ci_monitor is None"
            raise RuntimeError(msg)

        repo_url = ctx.repo_url
        if repo_url is None:
            msg = "monitor_ci requires repo_url but ctx.repo_url is None"
            raise RuntimeError(msg)

        ref = state["feature_branch"]
        repositories = scope_repositories(ctx.scope, self._repositories)
        if repositories:
            passed, summary, red_class, run_absent = await self._watch_each(
                ci_monitor, ctx, repositories=repositories, ref=ref
            )
        else:
            canonical = resolve_repo_url(repo_url, self._git_base_url)
            matches = tuple(
                repo
                for repo in self._repositories
                if resolve_repo_url(repo.url, self._git_base_url) == canonical
            )
            if len(matches) > 1:
                raise ValueError("delivery repository declarations are ambiguous")
            repository = matches[0] if matches else None
            passed, summary, red_class, run_absent = await self._watch(
                ci_monitor, repo_url=repo_url, repository=repository, ref=ref
            )
        ci_status = ci_status_of(passed)
        writer(WorkflowCIEvent(ci_status=ci_status, summary=summary, ref=ref))
        return {
            "ci_status": ci_status,
            "ci_summary": summary,
            "ci_red_class": red_class,
            "ci_run_absent": run_absent,
        }

    async def _watch_each(
        self,
        ci_monitor: CIMonitor,
        ctx: ExecutionContext,
        *,
        repositories: Sequence[RepoEntry],
        ref: str,
    ) -> _Watched:
        """The checks of every repository the branch gained commits in, as one.

        Failed when any repository failed, and a work defect in any of them
        is the run's to fix; each summary is prefixed with its repository.
        """
        watched = [
            (
                each.repository,
                await self._watch(
                    ci_monitor,
                    repo_url=each.repository.url,
                    repository=each.repository,
                    ref=ref,
                ),
            )
            for each in await gained_commits(
                git=self._git,
                cache=self._cache,
                repositories=repositories,
                branch=ref,
                cache_key=ctx.cache_key,
            )
        ]
        passes = [checks.passed for _, checks in watched]
        reds = [
            checks.red_class for _, checks in watched if checks.red_class is not None
        ]
        return _Watched(
            passed=False if False in passes else (True if True in passes else None),
            summary="\n".join(
                f"{repo_display(repository.url)[0]}: {checks.summary}"
                for repository, checks in watched
            ),
            red_class=(
                CheckRedClass.WORK_DEFECT
                if CheckRedClass.WORK_DEFECT in reds
                else next(iter(reds), None)
            ),
            run_absent=any(checks.run_absent for _, checks in watched),
        )

    async def _watch(
        self,
        ci_monitor: CIMonitor,
        *,
        repo_url: str,
        repository: RepoEntry | None,
        ref: str,
    ) -> _Watched:
        """One repository's checks at *ref*, reruns and classification included."""
        red_class = None
        async with self._watch_slots:
            observed = await ci_monitor.wait_for_checks(repo_url=repo_url, ref=ref)
            if isinstance(observed, IncompleteChecks):
                raise CheckObservationError(
                    repo_url=repo_url, ref=ref, reason=observed.summary
                )
            if isinstance(observed, ObservedChecks) and not observed.checks_passed:
                red = await classify_red_checks(
                    ci=ci_monitor,
                    repo_url=repo_url,
                    repository=repository,
                    initial=observed,
                    max_attempts=self._red_rerun_max_attempts,
                )
                red_class = red.red_class
                observed = red.observation
            passed = (
                observed.checks_passed if isinstance(observed, ObservedChecks) else None
            )
            run_absent = (
                passed is None
                and await ci_monitor.checks_declared(repo_url=repo_url)
                and not (repository is not None and repository.forge_exempt)
            )
        return _Watched(
            passed=passed,
            summary=observed.summary,
            red_class=red_class,
            run_absent=run_absent,
        )
