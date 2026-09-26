"""The scoped arm: a scope run's refusals, then its delivery."""

from collections.abc import AsyncIterator, Callable

from kodezart.core.protocols import (
    JobRegistry,
    TrackerScopeApprovalReader,
    WorkflowEngine,
)
from kodezart.domain.errors import ScopeNotApprovedError, ScopeRunLiveError
from kodezart.domain.scope_submission import prior_live_job
from kodezart.services.scope_approval import scope_approved
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.session import AllowedTools, PermissionMode


class ScopeEntry:
    """The scoped arm: refuse, or run the scope composition in its delivery.

    Refuses a scope an older live job is already running, and a scope that
    is not approved, both before anything else is read. An admitted run goes
    to the delivery arm the repository's origin selects, whose fire graph is
    the scope composition. Writes nothing itself, and no lease, claim or
    in-progress mark is taken for either refusal (KOD-788).
    """

    def __init__(
        self,
        *,
        approvals: TrackerScopeApprovalReader,
        registry: JobRegistry,
        arm_for: Callable[[str | None], WorkflowEngine],
    ) -> None:
        self._approvals = approvals
        self._registry = registry
        self._arm_for = arm_for

    async def admit(self, *, scope: ScopeRef, job_id: str) -> None:
        """Raise unless the run may begin; return on success.

        Raises ``ScopeRunLiveError`` and ``ScopeNotApprovedError``.

        Liveness is asked FIRST, and of the record store rather than of the
        tracker: a scope is a queue item and two runs of one scope contend
        over every issue below it, so the run that yields should cost the
        board nothing at all — not even the approval read.
        """
        ahead = prior_live_job(
            live=await self._registry.live_for_scope(scope=scope), job_id=job_id
        )
        if ahead is not None:
            raise ScopeRunLiveError(ref=scope, job_id=ahead.job_id, lane=ahead.lane)
        if not await scope_approved(ref=scope, tracker=self._approvals):
            raise ScopeNotApprovedError(ref=scope)

    async def run(
        self,
        *,
        prompt: str,
        issue_key: str | None = None,
        run_identity: RunIdentity | None = None,
        repo_path: str | None,
        repo_url: str | None,
        base_spec: BaseSpec,
        scope: ScopeRef | None,
        implied_base: BaseSpec | None = None,
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        cache_key: str,
    ) -> AsyncIterator[AgentEvent]:
        """Admit the addressed scope, then run it in its origin's delivery."""
        if scope is None:
            raise ValueError("The scoped arm requires an addressed scope")
        await self.admit(scope=scope, job_id=cache_key)
        async for event in self._arm_for(repo_url).run(
            prompt=prompt,
            issue_key=issue_key,
            run_identity=run_identity,
            repo_path=repo_path,
            repo_url=repo_url,
            base_spec=base_spec,
            scope=scope,
            implied_base=implied_base,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            cache_key=cache_key,
        ):
            yield event
