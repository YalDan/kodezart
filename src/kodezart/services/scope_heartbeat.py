"""The cron: each tick starts a run of every approved scope that is not finished."""

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime

from kodezart.core.constants import DEFAULT_LANE
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import JobQueue, JobRegistry, ScopeHeartbeatReader
from kodezart.domain.scope_submission import family_root, open_work_count
from kodezart.services.scope_approval import scope_approved
from kodezart.types.domain.agent import ScopeScanNode, ScopeScanOutput
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.dispatch import PassRun
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.workflow import WorkflowSubmission


class ScopeHeartbeat:
    """Ask the scope scan, then submit a run of each listed node the board admits.

    A node is skipped when the registry holds a live job for its scope, and
    when the repository the scan names is not one the operation declares. It
    is rejected, logged as ``scope_heartbeat_scan_rejected`` with the reason,
    when the tracker says its scope is not approved, or that the scope holds
    no open member other than tracker records. Every other node is submitted
    onto the queue exactly as ``POST /fire`` submits a scope.

    The scan is a cheap question and its answer on one board is not stable:
    on 2026-09-29 it left a finished project out and listed it five minutes
    later, and this submitted it (KOD-1302). So the scan only nominates; the
    two facts that decide a submission are read from the board here, so a
    node the board refuses is refused on every tick that lists it. A node the
    scan leaves out is not asked about on that tick. Nothing is remembered
    between ticks.
    """

    def __init__(
        self,
        *,
        ask: Callable[[], Awaitable[ScopeScanOutput | None]],
        tracker: ScopeHeartbeatReader,
        registry: JobRegistry,
        queue: JobQueue,
        trunks: Mapping[str, str],
    ) -> None:
        self._ask = ask
        self._tracker = tracker
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
        """Submit *node*'s run unless live, undeclared, or refused by the board."""
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
        rejected = await self._rejection(scope)
        if rejected is not None:
            await self._log.ainfo(
                "scope_heartbeat_scan_rejected",
                scope_kind=scope.kind.value,
                scope_key=scope.key,
                repo_url=repo_url,
                reason=rejected,
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

    async def _rejection(self, scope: ScopeRef) -> str | None:
        """Why the board refuses a run of *scope*, or ``None`` when it admits one.

        Approval first, through the one resolver a run's entry reads it with,
        so a label a person removed stops the next tick. Then the scope's
        family, counted: a scope whose every member is closed, or that holds
        none but tracker records, has nothing for a run to do.
        """
        if not await scope_approved(ref=scope, tracker=self._tracker):
            return "not_approved"
        family = await self._tracker.scope_issues(ref=scope)
        root = family_root(family) if scope.kind is ScopeKind.ISSUE else None
        if open_work_count(family, root=root) == 0:
            return "no_open_member"
        return None
