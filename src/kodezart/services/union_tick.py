"""Observe current lane heads around the existing pinned union composition."""

import asyncio
from collections.abc import Sequence

from kodezart.core.config import AppConfig
from kodezart.core.owned_tasks import finish_owned
from kodezart.core.protocols import GitService
from kodezart.domain.errors import UnionHeadReadError, UnionUnstableError
from kodezart.services.git_observations import read_remote_head
from kodezart.services.union_composition import UnionComposition
from kodezart.services.union_identity import require_union_object_identity
from kodezart.types.domain.union import UnionCompositionResult, UnionLaneHead
from kodezart.types.domain.union_tick import (
    UnionLaneBranch,
    UnionTickContext,
    UnionTickPlan,
)


class UnionTick:
    """Reuse only an observation made under this instance's fixed configuration.

    The caller supplies its complete planner-order branch roster on each
    tick. Native reads determine the commits; a moved head invalidates the
    entire measured union, including a red result or a merge conflict.
    """

    def __init__(
        self,
        *,
        composition: UnionComposition,
        git: GitService,
        context: UnionTickContext,
        config: AppConfig,
    ) -> None:
        self._composition = composition
        self._git = git
        # The caller's mutable repository configuration cannot change the
        # meaning of a result already cached by this consumer.
        self._context = UnionTickContext.model_validate(context.model_dump())
        self._max_attempts = config.union_stale_max_attempts
        self._last_result: UnionCompositionResult | None = None
        self._tick = asyncio.Lock()

    async def verify(
        self, *, lane_branches: Sequence[UnionLaneBranch]
    ) -> UnionCompositionResult:
        """Return a current observation or refuse continuously moving heads."""
        plan = UnionTickPlan.model_validate(
            {"lanes": [lane.model_dump() for lane in lane_branches]}
        )
        async with self._tick:
            heads = await self._read_heads(plan)
            previous = self._last_result
            if previous is not None and previous.lane_heads == heads:
                return previous
            for _ in range(self._max_attempts):
                measured = heads
                await self._fetch()
                # A push during fetch may leave its objects ahead of our local
                # copy. Require matching reads around fetch before composing.
                heads = await self._read_heads(plan)
                if heads != measured:
                    continue
                result = await self._composition.verify(
                    scope_key=self._context.scope_key,
                    repo_path=self._context.repo_path,
                    repo=self._context.repo,
                    base_sha=self._context.base_sha,
                    lane_heads=heads,
                )
                heads = await self._read_heads(plan)
                if heads == result.lane_heads:
                    self._last_result = result
                    return result
            raise UnionUnstableError(
                scope_key=self._context.scope_key,
                attempts=self._max_attempts,
                lane_keys=tuple(head.lane_key for head in measured),
                measured_shas=tuple(head.head_sha for head in measured),
                current_shas=tuple(head.head_sha for head in heads),
            )

    async def _fetch(self) -> None:
        try:
            _, cancelled = await finish_owned(
                asyncio.create_task(self._git.fetch(self._context.repo_path))
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise UnionHeadReadError(
                scope_key=self._context.scope_key,
                branch=None,
                reason="the configured remote could not be fetched",
            ) from exc
        if cancelled:
            raise asyncio.CancelledError

    async def _read_heads(self, plan: UnionTickPlan) -> tuple[UnionLaneHead, ...]:
        observed: list[UnionLaneHead] = []
        for lane in plan.lanes:
            try:
                sha = await read_remote_head(
                    git=self._git,
                    repository=self._context.repo_path,
                    remote=self._context.git_remote,
                    branch=lane.branch,
                )
                if sha is None:
                    raise UnionHeadReadError(
                        scope_key=self._context.scope_key,
                        branch=lane.branch,
                        reason="the planned branch is absent on the remote",
                    )
                observed.append(
                    UnionLaneHead(
                        lane_key=lane.lane_key, branch=lane.branch, head_sha=sha
                    )
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise UnionHeadReadError(
                    scope_key=self._context.scope_key,
                    branch=lane.branch,
                    reason="the planned branch has no readable commit identity",
                ) from exc
        await require_union_object_identity(
            git=self._git,
            repository=self._context.repo_path,
            scope_key=self._context.scope_key,
        )
        return tuple(observed)
