"""Read recorded grading and distinguish lapse from fresh current-head judgment."""

import asyncio

from kodezart.chains.audit_pass import AuditClaimVerifier
from kodezart.core.config import AppConfig
from kodezart.core.owned_tasks import finish_owned
from kodezart.core.protocols import GitService, GitSourceReader, RepoCache, TrackerPort
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import AuditEvidenceReadError
from kodezart.domain.fire_spec import criterion_check
from kodezart.services.criterion_sources import resolve_criterion
from kodezart.services.git_observations import read_replace_refs
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.repo_observations import ensure_repository
from kodezart.types.domain.audit import AuditClaimRequest, AuditVerdict
from kodezart.types.domain.audit_evidence import AuditEvidenceObservation
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.operation import LifecycleStage, OperationConfig
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind


class AuditEvidenceVerifier:
    """Observe the two grading arms without applying or publishing a correction.

    A stale completed claim lapses without a session. A current claim or a
    configured review-state claim goes through the existing fresh verifier.
    No recorded test/verdict text becomes that session's judgment input.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        records: LaneRecordReader,
        git: GitService,
        source: GitSourceReader,
        cache: RepoCache,
        claims: AuditClaimVerifier,
        operation: OperationConfig,
        config: AppConfig,
    ) -> None:
        self._tracker = tracker
        self._records = records
        self._git = git
        self._source = source
        self._cache = cache
        self._claims = claims
        self._operation = operation
        self._remote = config.git_remote

    async def _criterion(self, request: AuditClaimRequest) -> TrackerIssue:
        return await resolve_criterion(
            tracker=self._tracker,
            issue_key=request.lane_issue_key,
            criterion_key=request.criterion_key,
        )

    async def _head(
        self,
        *,
        repository: str,
        branch: str,
        evidence: CriterionEvidence,
        completed: bool,
    ) -> str:
        async def observe() -> str:
            await self._git.fetch(repository)
            if await read_replace_refs(git=self._git, workspace=repository):
                raise ValueError("the Evidence repository substitutes Git objects")
            head = await self._git.remote_branch_sha(repository, self._remote, branch)
            if not head:
                raise ValueError("the recorded branch has no live remote head")
            if await self._source.resolve_commit(cwd=repository, ref=head) != head:
                raise ValueError("the remote head did not resolve to the same commit")
            if completed:
                if (
                    await self._source.resolve_commit(
                        cwd=repository, ref=evidence.graded_sha
                    )
                    != evidence.graded_sha
                ):
                    raise ValueError("the graded reference is not its recorded commit")
                if not await self._git.is_ancestor(
                    repository, evidence.graded_sha, head
                ):
                    raise ValueError("the graded commit is not on the recorded branch")
            return head

        head, cancelled = await finish_owned(asyncio.create_task(observe()))
        if cancelled:
            raise asyncio.CancelledError
        return head

    async def observe(self, request: AuditClaimRequest) -> AuditEvidenceObservation:
        """Read Evidence from its owner and revalidate source coherence at return."""
        try:
            return await self._observe(request)
        except AuditEvidenceReadError:
            raise
        except Exception as exc:
            raise AuditEvidenceReadError(
                criterion_key=request.criterion_key, reason=str(exc)
            ) from exc

    async def _observe(self, request: AuditClaimRequest) -> AuditEvidenceObservation:
        criterion = await self._criterion(request)
        completed = criterion.state_kind is WorkflowStateKind.COMPLETED
        if not completed and (
            criterion.state_kind is not WorkflowStateKind.STARTED
            or criterion.state_name
            != self._operation.workflow_states.get(LifecycleStage.IN_REVIEW)
        ):
            raise ValueError("a completed or configured review claim is required")
        evidence = parse_criterion_evidence(criterion.body)
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
        head = await self._head(
            repository=repository,
            branch=record.branch,
            evidence=evidence,
            completed=completed,
        )
        claim = None
        verdict = AuditVerdict.UNVERIFIABLE
        if not completed or evidence.graded_sha == head:
            claim = await self._claims.verify(
                request.model_copy(update={"record_ref": comment.comment_key})
            )
            if claim.check != criterion_check(
                criterion=criterion, issue_key=request.lane_issue_key
            ):
                raise ValueError("the fresh claim examined a different Check")
            verdict = claim.judgment.verdict
        if await self._criterion(request) != criterion:
            raise ValueError("the criterion changed during Evidence verification")
        latest_record = await self._records.read(
            issue_key=request.lane_issue_key,
            lane_key=request.lane_key,
            record_ref=comment.comment_key,
        )
        if latest_record != (comment, record):
            raise ValueError("the lane record changed during Evidence verification")
        latest_head, cancelled = await finish_owned(
            asyncio.create_task(
                self._git.remote_branch_sha(repository, self._remote, record.branch)
            )
        )
        if cancelled:
            raise asyncio.CancelledError
        if latest_head != head:
            raise ValueError("the remote head changed during Evidence verification")
        if await read_replace_refs(git=self._git, workspace=repository):
            raise ValueError("the Evidence repository substitutes Git objects")
        return AuditEvidenceObservation(
            criterion=criterion,
            recorded_evidence=evidence,
            head_sha=head,
            record_ref=comment.comment_key,
            verdict=verdict,
            current_claim=claim,
        )
