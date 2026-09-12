"""Observe completed CI and classify bounded same-SHA recovery."""

import asyncio
from collections.abc import Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from kodezart.core.protocols import CIMonitor
from kodezart.domain.ci import ci_status_of
from kodezart.domain.errors import (
    CheckObservationError,
)
from kodezart.domain.git_url import resolve_repo_url
from kodezart.services.check_classification import classify_red_checks
from kodezart.types.domain.agent import (
    WorkflowCIEvent,
)
from kodezart.types.domain.check_observation import IncompleteChecks, ObservedChecks
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.workflow import (
    AuthoredWorkflowState,
    ExecutionContext,
)


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
    ) -> None:
        self._ci_monitor = ci_monitor
        self._git_base_url = git_base_url
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
        canonical = resolve_repo_url(repo_url, self._git_base_url)
        matches = tuple(
            repo
            for repo in self._repositories
            if resolve_repo_url(repo.url, self._git_base_url) == canonical
        )
        if len(matches) > 1:
            raise ValueError("delivery repository declarations are ambiguous")
        repository = matches[0] if matches else None
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
            summary = observed.summary
            run_absent = (
                passed is None
                and await ci_monitor.checks_declared(repo_url=repo_url)
                and not (repository is not None and repository.forge_exempt)
            )
        ci_status = ci_status_of(passed)
        writer(WorkflowCIEvent(ci_status=ci_status, summary=summary, ref=ref))
        return {
            "ci_status": ci_status,
            "ci_summary": summary,
            "ci_red_class": red_class,
            "ci_run_absent": run_absent,
        }
