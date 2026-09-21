"""Native criterion, grading and branch snapshots for revision-reading audits."""

from dataclasses import dataclass

from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import (
    CriterionResolver,
    GitService,
    GitSourceReader,
    RepoCache,
)
from kodezart.domain.errors import AuditClaimReadError, AuditEvidenceReadError
from kodezart.domain.fire_spec import criterion_check
from kodezart.services.audit_failures import AUDIT_READ_FAILURES, parse_audit_evidence
from kodezart.services.git_observations import read_remote_head
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.repo_observations import ensure_repository
from kodezart.types.domain.audit import AuditClaimRequest
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.operation import LifecycleStage, OperationConfig
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.tracker import (
    TrackerComment,
    TrackerIssue,
    WorkflowStateKind,
)


@dataclass(frozen=True)
class AuditSourceSnapshot:
    """Exact native inputs, without treating any recorded conclusion as true."""

    request: AuditClaimRequest
    criterion: TrackerIssue
    check: str
    evidence: CriterionEvidence
    comment: TrackerComment
    record: LaneRunState
    repository: str
    head_sha: str


class AuditSourceReader:
    """Read immutable Git revisions from a current criterion's own Evidence."""

    def __init__(
        self,
        *,
        resolver: CriterionResolver,
        records: LaneRecordReader,
        git: GitService,
        source: GitSourceReader,
        cache: RepoCache,
        operation: OperationConfig,
        remote: str,
    ) -> None:
        self._resolver = resolver
        self._records = records
        self._git = git
        self._source = source
        self._cache = cache
        self._review_state = operation.workflow_states.get(LifecycleStage.IN_REVIEW)
        self._remote = remote

    async def _criterion(self, request: AuditClaimRequest) -> TrackerIssue:
        criterion = await self._resolver.resolve_criterion(
            issue_key=request.lane_issue_key,
            criterion_key=request.criterion_key,
        )
        if criterion.state_kind is not WorkflowStateKind.COMPLETED and (
            criterion.state_kind is not WorkflowStateKind.STARTED
            or criterion.state_name != self._review_state
        ):
            raise AuditClaimReadError(
                "a completed or configured review claim is required"
            )
        return criterion

    async def read(self, request: AuditClaimRequest) -> AuditSourceSnapshot:
        """Resolve and retain the actual source pair, then check its coherence."""
        try:
            criterion = await self._criterion(request)
            evidence = parse_audit_evidence(criterion.body)
            check = criterion_check(
                criterion=criterion, issue_key=request.lane_issue_key
            )
            comment, record = await self._records.read(
                issue_key=request.lane_issue_key,
                lane_key=request.lane_key,
                record_ref=request.record_ref,
            )
            repository = await ensure_repository(
                cache=self._cache,
                repo_url=request.repo_url,
                cache_key=request.cache_key,
            )

            async def resolve() -> str:
                await self._git.fetch(repository)
                head = await self._git.remote_branch_sha(
                    repository, self._remote, record.branch
                )
                if not head:
                    raise AuditClaimReadError(
                        "the recorded branch has no live remote head"
                    )
                for sha in (evidence.graded_sha, head):
                    if (
                        await self._source.resolve_commit(cwd=repository, ref=sha)
                        != sha
                    ):
                        raise AuditClaimReadError(
                            "a revision did not resolve to its exact commit"
                        )
                if not await self._git.is_ancestor(
                    repository, evidence.graded_sha, head
                ):
                    raise AuditClaimReadError(
                        "the graded commit is not on the recorded branch"
                    )
                return head

            head = await settle(resolve())
            snapshot = AuditSourceSnapshot(
                request=request,
                criterion=criterion,
                check=check,
                evidence=evidence,
                comment=comment,
                record=record,
                repository=repository,
                head_sha=head,
            )
            await self.require_unchanged(snapshot)
            return snapshot
        except AuditEvidenceReadError:
            raise
        except AUDIT_READ_FAILURES as exc:
            raise AuditEvidenceReadError(
                criterion_key=request.criterion_key, reason=str(exc)
            ) from exc

    async def require_unchanged(self, snapshot: AuditSourceSnapshot) -> None:
        """Refuse changed native evidence before a consumer returns its observation."""
        request = snapshot.request
        try:
            if await self._criterion(request) != snapshot.criterion:
                raise AuditClaimReadError("the criterion changed during the audit")
            latest = await self._records.read(
                issue_key=request.lane_issue_key,
                lane_key=request.lane_key,
                record_ref=snapshot.comment.comment_key,
            )
            if latest != (snapshot.comment, snapshot.record):
                raise AuditClaimReadError("the lane record changed during the audit")
            if (
                await read_remote_head(
                    git=self._git,
                    repository=snapshot.repository,
                    remote=self._remote,
                    branch=snapshot.record.branch,
                )
                != snapshot.head_sha
            ):
                raise AuditClaimReadError("the remote head changed during the audit")
        except AuditEvidenceReadError:
            raise
        except AUDIT_READ_FAILURES as exc:
            raise AuditEvidenceReadError(
                criterion_key=request.criterion_key, reason=str(exc)
            ) from exc
