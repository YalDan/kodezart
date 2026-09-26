"""Consolidate the implemented branch and retain or clean its backups."""

from collections.abc import Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    BranchMerger,
    GitService,
    RefPublisher,
    RepoCache,
)
from kodezart.domain.accept_gate import (
    gate_cleared,
)
from kodezart.domain.agent import best_iteration_ref
from kodezart.domain.trajectory import landable_commit
from kodezart.services.gained_commits import gained_commits, scope_repositories
from kodezart.types.domain.agent import (
    WorkflowCompleteEvent,
    WorkflowConsolidationEvent,
)
from kodezart.types.domain.consolidation import (
    ConsolidationOutcome,
    ConsolidationStatus,
)
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.workflow import (
    ExecutionContext,
    WorkflowState,
)


async def resolve_workflow_cwd(ctx: ExecutionContext, cache: RepoCache) -> str:
    """Resolve a usable cwd for GitService calls in the outer engine."""
    if ctx.repo_path is not None:
        return ctx.repo_path
    if ctx.repo_url is None:
        msg = "Neither repo_path nor repo_url set on ExecutionContext"
        raise RuntimeError(msg)
    return await cache.ensure_available(ctx.repo_url, ctx.cache_key)


class FireConsolidation:
    """Consolidate the implemented branch and retain or clean its backups."""

    def __init__(
        self,
        *,
        merger: BranchMerger,
        git: GitService,
        cache: RepoCache,
        git_remote: str,
        ref_publisher: RefPublisher | None,
        repositories: Sequence[RepoEntry] = (),
    ) -> None:
        self._merger = merger
        self._git = git
        self._cache = cache
        self._git_remote = git_remote
        self._ref_publisher = ref_publisher
        self._repositories = tuple(repositories)
        self._log: BoundLogger = get_logger("kodezart.chains.ralph_workflow")

    async def repo_dir(self, config: RunnableConfig) -> str:
        """The directory this step's own git reads of the run stand in.

        The checkout the run was dispatched with, or the clone this step
        consolidates in when it was dispatched with a url alone.  Public
        because the node that holds this step reads the run's shas from the
        same place it does, and a second resolution beside it could answer
        with another tree.
        """
        return await resolve_workflow_cwd(
            ExecutionContext.from_configurable(config), self._cache
        )

    async def merge_to_feature(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Consolidate ralph branch into feature branch.

        Single ``BranchMerger.consolidate`` call; routes on the four-status
        outcome.  Never catches exceptions around the merger — the merger
        is a total function over the four statuses.  ``SOURCE_MISSING`` is
        a programming error (the loop must have pushed) and raises.

        This is the ONLY writer of ``work_base_ref``, and it writes it on
        exactly the two statuses that put work on the feature branch.
        Every other arm omits the key, leaving the ref a later round
        stands on where the last integration left it.
        """
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        if not gate_cleared(state["accept_verdict"]):
            trajectory = state["trajectory"]
            best = None if trajectory is None else landable_commit(trajectory)
            exit_state: dict[str, object] = {
                "merged": False,
                "merge_error": None,
                "feature_tip_sha": None,
                "review_base_sha": None,
                "review_head_sha": None,
            }
            # A round that committed nothing writes nothing: omitting the
            # key leaves the previously recorded best standing, so a run
            # whose LAST round was empty is not reported as having done
            # no work at all. The branch is written with it: the best is a
            # commit of THIS round's loop branch, which its push put on the
            # remote. A round that committed nothing may push its branch at
            # its cut point, but that push is not an iteration commit and
            # never replaces an earlier best.
            if best is not None:
                exit_state["best_iteration_sha"] = best
                exit_state["best_iteration_branch"] = state["ralph_branch"]
            return exit_state

        repositories = scope_repositories(ctx.scope, self._repositories)
        if repositories:
            return await self._merge_each(state, ctx, repositories)

        outcome = await self._merger.consolidate(
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            base_branch=ctx.base_branch,
            feature_branch=state["feature_branch"],
            source_branch=state["ralph_branch"],
            cache_key=ctx.cache_key,
        )
        writer(
            WorkflowConsolidationEvent(
                status=outcome.status,
                feature_branch=state["feature_branch"],
                source_branch=state["ralph_branch"],
                feature_tip_sha=outcome.feature_tip_sha,
            )
        )

        if outcome.status is ConsolidationStatus.SOURCE_MISSING:
            msg = (
                f"consolidate returned SOURCE_MISSING for "
                f"{state['ralph_branch']!r} — the loop must have pushed"
            )
            raise RuntimeError(msg)

        if outcome.status is ConsolidationStatus.DIVERGENT:
            return {
                "merged": False,
                "merge_error": (
                    f"ralph diverged from feature: "
                    f"{state['ralph_branch']} ⇄ {state['feature_branch']}"
                ),
                "feature_tip_sha": outcome.feature_tip_sha,
                "review_base_sha": None,
                "review_head_sha": None,
            }

        # FAST_FORWARDED or ALREADY_INTEGRATED — both yield a merged
        # feature tip the reviewer can evaluate against the base branch.
        cwd = await resolve_workflow_cwd(ctx, self._cache)
        base_tip = await self._git.remote_branch_sha(
            cwd,
            self._git_remote,
            ctx.base_branch,
        )
        if base_tip is None:
            msg = (
                f"Base branch {ctx.base_branch!r} not found on {self._git_remote} "
                "after successful consolidation"
            )
            raise RuntimeError(msg)
        return {
            "merged": True,
            "merge_error": None,
            "feature_tip_sha": outcome.feature_tip_sha,
            "review_base_sha": base_tip,
            "review_head_sha": outcome.feature_tip_sha,
            "work_base_ref": state["feature_branch"],
        }

    async def land_best_iteration(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Publish and select the best available work without opening a PR."""
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        trajectory = state["trajectory"]
        best_sha = state["best_iteration_sha"]
        if trajectory is None or best_sha is None:
            await self._log.ainfo(
                "stall_exit_no_commit_to_land",
                feature_branch=state["feature_branch"],
                total_iterations=state["total_iterations"],
            )
            return {}

        if self._ref_publisher is None or ctx.repo_url is None:
            return {}

        repositories = scope_repositories(ctx.scope, self._repositories)
        if repositories:
            return await self._land_each(state, ctx, repositories)

        best_ref = best_iteration_ref(state["feature_branch"])
        await self._ref_publisher.publish(
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            commit_sha=best_sha,
            ref=best_ref,
            cache_key=ctx.cache_key,
        )

        outcome = await self._merger.consolidate(
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            base_branch=ctx.base_branch,
            feature_branch=state["feature_branch"],
            source_branch=best_ref,
            cache_key=ctx.cache_key,
        )
        writer(
            WorkflowConsolidationEvent(
                status=outcome.status,
                feature_branch=state["feature_branch"],
                source_branch=best_ref,
                feature_tip_sha=outcome.feature_tip_sha,
            )
        )
        integrated = outcome.status in (
            ConsolidationStatus.FAST_FORWARDED,
            ConsolidationStatus.ALREADY_INTEGRATED,
        )
        head = state["feature_branch"] if integrated else best_ref
        head_sha = outcome.feature_tip_sha if integrated else best_sha

        return {"feature_branch": head, "feature_tip_sha": head_sha}

    async def _merge_each(
        self,
        state: WorkflowState,
        ctx: ExecutionContext,
        repositories: Sequence[RepoEntry],
    ) -> dict[str, object]:
        """Consolidate the loop branch in every repository it gained commits in.

        Each repository merges onto its own trunk. The run merged when every
        one of them did; its tip and review endpoints are the first
        repository's, which is what the steps after this one read.
        """
        outcomes = await self._consolidate_each(state, ctx, repositories)
        if not outcomes:
            msg = (
                f"{state['ralph_branch']!r} was accepted with no commits in any "
                "declared repository"
            )
            raise RuntimeError(msg)
        first_repository, first = outcomes[0]
        diverged = [
            repository.url
            for repository, outcome in outcomes
            if outcome.status is ConsolidationStatus.DIVERGENT
        ]
        if diverged:
            return {
                "merged": False,
                "merge_error": (
                    f"ralph diverged from feature in {', '.join(diverged)}: "
                    f"{state['ralph_branch']} ⇄ {state['feature_branch']}"
                ),
                "feature_tip_sha": first.feature_tip_sha,
                "review_base_sha": None,
                "review_head_sha": None,
            }
        clone = await self._cache.ensure_available(first_repository.url, ctx.cache_key)
        base_tip = await self._git.remote_branch_sha(
            clone, self._git_remote, first_repository.trunk
        )
        if base_tip is None:
            msg = (
                f"Base branch {first_repository.trunk!r} not found on "
                f"{self._git_remote} after successful consolidation"
            )
            raise RuntimeError(msg)
        return {
            "merged": True,
            "merge_error": None,
            "feature_tip_sha": first.feature_tip_sha,
            "review_base_sha": base_tip,
            "review_head_sha": first.feature_tip_sha,
            "work_base_ref": state["feature_branch"],
        }

    async def _land_each(
        self,
        state: WorkflowState,
        ctx: ExecutionContext,
        repositories: Sequence[RepoEntry],
    ) -> dict[str, object]:
        """Publish the loop branch as the deliverable wherever it gained commits.

        No best commit is chosen across repositories: each repository's loop
        branch is consolidated onto its own trunk as it stands.
        """
        outcomes = await self._consolidate_each(state, ctx, repositories)
        landed = [
            outcome.feature_tip_sha
            for _, outcome in outcomes
            if outcome.status is not ConsolidationStatus.DIVERGENT
        ]
        return {"feature_tip_sha": landed[0]} if landed else {}

    async def _consolidate_each(
        self,
        state: WorkflowState,
        ctx: ExecutionContext,
        repositories: Sequence[RepoEntry],
    ) -> list[tuple[RepoEntry, ConsolidationOutcome]]:
        """Consolidate the loop branch onto the deliverable, per repository."""
        writer = get_stream_writer()
        outcomes: list[tuple[RepoEntry, ConsolidationOutcome]] = []
        for each in await gained_commits(
            git=self._git,
            cache=self._cache,
            repositories=repositories,
            branch=state["ralph_branch"],
            cache_key=ctx.cache_key,
        ):
            outcome = await self._merger.consolidate(
                repo_path=None,
                repo_url=each.repository.url,
                base_branch=each.repository.trunk,
                feature_branch=state["feature_branch"],
                source_branch=state["ralph_branch"],
                cache_key=ctx.cache_key,
            )
            writer(
                WorkflowConsolidationEvent(
                    status=outcome.status,
                    feature_branch=state["feature_branch"],
                    source_branch=state["ralph_branch"],
                    feature_tip_sha=outcome.feature_tip_sha,
                )
            )
            if outcome.status is ConsolidationStatus.SOURCE_MISSING:
                msg = (
                    f"consolidate returned SOURCE_MISSING for "
                    f"{state['ralph_branch']!r} in {each.repository.url}"
                )
                raise RuntimeError(msg)
            outcomes.append((each.repository, outcome))
        return outcomes

    async def cleanup_backups(
        self,
        terminal: WorkflowCompleteEvent,
        config: RunnableConfig,
    ) -> None:
        ctx = ExecutionContext.from_configurable(config)
        if terminal.accepted and terminal.merged:
            await self._log.ainfo(
                "backup_cleanup_starting",
                prefix=terminal.feature_branch,
            )
            origins: list[tuple[str | None, str | None]] = [
                (None, repository.url)
                for repository in scope_repositories(ctx.scope, self._repositories)
            ] or [(ctx.repo_path, ctx.repo_url)]
            for repo_path, repo_url in origins:
                await self._merger.cleanup_backup_branches(
                    repo_path=repo_path,
                    repo_url=repo_url,
                    prefix=terminal.feature_branch,
                    cache_key=ctx.cache_key,
                )
        else:
            await self._log.adebug(
                "backup_cleanup_skipped",
                accept_verdict=terminal.accepted,
                merged=terminal.merged,
            )
