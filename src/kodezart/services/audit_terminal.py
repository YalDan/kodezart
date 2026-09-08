"""Read actual tracker/forge terminal facts without requiring a merge."""

import asyncio

from kodezart.core.config import AppConfig
from kodezart.core.owned_tasks import finish_owned
from kodezart.core.protocols import GitService, PRStateReader, RepoCache, TrackerPort
from kodezart.domain.errors import AuditClaimReadError
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.repo_observations import ensure_repository
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_terminal import (
    AuditTerminalObservation,
    AuditTerminalRequest,
    TerminalDiscrepancy,
)
from kodezart.types.domain.operation import LifecycleStage, OperationConfig
from kodezart.types.domain.pr_state import PRLifecycle
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind


class AuditTerminalReader:
    """Inspect the configured review terminal and its complete native family.

    The full sweep's selection, claim execution and publication remain separate.
    Read failures or changing observations raise; none is a healthy terminal.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        records: LaneRecordReader,
        forge: PRStateReader,
        git: GitService,
        cache: RepoCache,
        operation: OperationConfig,
        config: AppConfig,
    ) -> None:
        self._tracker = tracker
        self._records = records
        self._forge = forge
        self._git = git
        self._cache = cache
        self._remote = config.git_remote
        self._review_state = operation.workflow_states[LifecycleStage.IN_REVIEW]

    async def _criteria(self, issue_key: str) -> tuple[TrackerIssue, ...]:
        rows = tuple(await self._tracker.read_criteria(issue_key=issue_key))
        if not rows or len({row.issue_key for row in rows}) != len(rows):
            raise AuditClaimReadError(
                "terminal criterion family is empty or duplicated"
            )
        if any(row.parent_key != issue_key for row in rows):
            raise AuditClaimReadError("terminal criterion family has another parent")
        return tuple(sorted(rows, key=lambda row: row.issue_key))

    async def _head(self, repository: str, branch: str) -> str | None:
        observed, cancelled = await finish_owned(
            asyncio.create_task(
                self._git.remote_branch_sha(repository, self._remote, branch)
            )
        )
        if cancelled:
            raise asyncio.CancelledError
        if observed is not None and not observed.strip():
            raise AuditClaimReadError("remote branch returned an empty commit identity")
        return observed

    async def observe(self, request: AuditTerminalRequest) -> AuditTerminalObservation:
        issue = await self._tracker.read_issue(issue_key=request.issue_key)
        if issue.issue_key != request.issue_key:
            raise AuditClaimReadError("terminal read returned another issue")
        criteria = await self._criteria(request.issue_key)
        if issue.state_name != self._review_state or any(
            row.state_kind is not WorkflowStateKind.COMPLETED for row in criteria
        ):
            raise AuditClaimReadError("the expected review terminal is not established")
        comment, record = await self._records.read(
            issue_key=request.issue_key,
            lane_key=request.lane_key,
            record_ref=request.record_ref,
        )
        repository = await ensure_repository(
            cache=self._cache, repo_url=request.repo_url, cache_key=request.cache_key
        )
        branch_head = await self._head(repository, record.branch)
        discrepancies: list[TerminalDiscrepancy] = []
        if branch_head is None:
            discrepancies.append(TerminalDiscrepancy.NO_BRANCH)
        pr = None
        pr_head = None
        if record.pr is None:
            discrepancies.append(TerminalDiscrepancy.UNRESOLVED_ASSOCIATION)
        else:
            pr = await self._forge.read_pr_state(
                repo_url=request.repo_url, pr_number=record.pr.number
            )
            if pr.number != record.pr.number or pr.url != record.pr.url:
                raise AuditClaimReadError(
                    "PR reader returned another recorded identity"
                )
            associated = {item.branch for item in record.associations}
            pr_head = await self._head(repository, pr.head_branch)
            if pr.head_branch not in associated or pr_head is None:
                discrepancies.append(TerminalDiscrepancy.UNRESOLVED_ASSOCIATION)
            elif pr.head_sha != pr_head:
                raise AuditClaimReadError(
                    "PR commit and its current branch do not agree"
                )
            if pr.lifecycle is PRLifecycle.CLOSED:
                discrepancies.append(TerminalDiscrepancy.CLOSED_UNMERGED_PR)
        latest_issue = await self._tracker.read_issue(issue_key=request.issue_key)
        latest_criteria = await self._criteria(request.issue_key)
        latest_record = await self._records.read(
            issue_key=request.issue_key,
            lane_key=request.lane_key,
            record_ref=comment.comment_key,
        )
        if (latest_issue, latest_criteria, latest_record) != (
            issue,
            criteria,
            (comment, record),
        ):
            raise AuditClaimReadError("tracker terminal changed during observation")
        if await self._head(repository, record.branch) != branch_head:
            raise AuditClaimReadError("recorded branch changed during observation")
        if pr is not None:
            if (
                await self._forge.read_pr_state(
                    repo_url=request.repo_url, pr_number=pr.number
                )
                != pr
            ):
                raise AuditClaimReadError("PR changed during terminal observation")
            if await self._head(repository, pr.head_branch) != pr_head:
                raise AuditClaimReadError(
                    "PR branch changed during terminal observation"
                )
        return AuditTerminalObservation(
            issue_key=issue.issue_key,
            record_ref=comment.comment_key,
            verdict=AuditVerdict.REFUTED if discrepancies else AuditVerdict.HOLDS,
            discrepancies=tuple(discrepancies),
            branch_head=branch_head,
            pr=pr,
        )
