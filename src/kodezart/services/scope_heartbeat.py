"""The cron: each tick starts a run of every approved scope that is not finished."""

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime

from kodezart.core.constants import DEFAULT_LANE
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import JobQueue, JobRegistry
from kodezart.types.domain.agent import ScopeScanNode, ScopeScanOutput
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.workflow import WorkflowSubmission


class ScopeHeartbeat:
    """Ask the scope scan, then submit a run of each node it lists.

    A node is skipped when the registry holds a live job for its scope, and
    when the repository the scan names is not one the operation declares;
    every other node is submitted onto the queue exactly as ``POST /fire``
    submits a scope. Nothing is remembered between ticks: the scan lists only
    approved nodes that are not finished, and the run's entry refuses an
    unapproved one anyway.
    """

    def __init__(
        self,
        *,
        ask: Callable[[], Awaitable[ScopeScanOutput | None]],
        registry: JobRegistry,
        queue: JobQueue,
        trunks: Mapping[str, str],
    ) -> None:
        self._ask = ask
        self._registry = registry
        self._queue = queue
        self._trunks = dict(trunks)
        self._log: BoundLogger = get_logger(__name__)

    async def run(self, started_at: datetime) -> PassRun:
        """The scheduled tick: RAN when it submitted anything, else SKIPPED.

        Every listed node is reached, each in its own try/except, so one node
        that fails never starves the others; the first failure is re-raised
        after the last node, because a pass that swallowed it would look like
        a quiet board. *started_at* titles nothing: this pass keeps no record.
        """
        del started_at
        answer = await self._ask()
        if answer is None:
            return PassRun.SKIPPED
        await self._log.ainfo(
            "scope_heartbeat_scanned", listed=len(answer.scopes), reason=answer.reason
        )
        submitted = False
        first_failure: Exception | None = None
        for node in answer.scopes:
            try:
                submitted = await self._submit(node) or submitted
            except Exception as exc:
                await self._log.aerror(
                    "scope_heartbeat_scope_failed",
                    scope_kind=node.kind.value,
                    scope_key=node.key,
                    repo_url=node.repository,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                if first_failure is None:
                    first_failure = exc
        if first_failure is not None:
            raise first_failure
        return PassRun.RAN if submitted else PassRun.SKIPPED

    async def _submit(self, node: ScopeScanNode) -> bool:
        """Submit *node*'s run unless its scope is live or its repository undeclared."""
        scope = ScopeRef(kind=node.kind, key=node.key)
        live = await self._registry.live_for_scope(scope=scope)
        if live:
            await self._log.ainfo(
                "scope_heartbeat_scope_live",
                scope_kind=scope.kind.value,
                scope_key=scope.key,
                job_id=live[0].job_id,
            )
            return False
        repo_url = node.repository
        if repo_url is None or repo_url not in self._trunks:
            await self._log.ainfo(
                "scope_heartbeat_repository_undeclared",
                scope_kind=scope.kind.value,
                scope_key=scope.key,
                repo_url=repo_url,
            )
            return False
        record = await self._queue.submit(
            lane=DEFAULT_LANE,
            request=WorkflowSubmission(
                prompt=f"scope {scope.kind.value} {scope.key}",
                issue_key=None,
                repo_path=None,
                repo_url=repo_url,
                base_spec=trunk_base(self._trunks[repo_url]),
                implied_base=None,
                scope=scope,
                permission_mode=PermissionMode.UNATTENDED,
                allowed_tools=ToolPreset.IMPLEMENTATION,
            ),
        )
        await self._log.ainfo(
            "scope_heartbeat_run_submitted",
            lane=DEFAULT_LANE,
            scope_kind=scope.kind.value,
            scope_key=scope.key,
            repo_url=repo_url,
            job_id=record.job_id,
        )
        return True
