"""Read recorded grading and distinguish lapse from fresh current-head judgment."""

from kodezart.chains.audit_pass import AuditClaimVerifier
from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import (
    CriterionResolver,
    GitService,
    GitSourceReader,
    LaneEventHistory,
    RepoCache,
)
from kodezart.domain.audit_claims import evidence_row_history, restamp_verdict
from kodezart.domain.errors import AuditClaimReadError, AuditEvidenceReadError
from kodezart.domain.fire_spec import criterion_check
from kodezart.domain.lapse import GradedState, graded_state
from kodezart.services.audit_failures import AUDIT_READ_FAILURES, parse_audit_evidence
from kodezart.services.git_observations import read_replace_refs
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.repo_observations import ensure_repository
from kodezart.types.domain.audit import AuditClaimRequest, AuditVerdict
from kodezart.types.domain.audit_evidence import (
    AuditEvidenceObservation,
    AuditRestampTrace,
)
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
        resolver: CriterionResolver,
        records: LaneRecordReader,
        git: GitService,
        source: GitSourceReader,
        cache: RepoCache,
        claims: AuditClaimVerifier,
        operation: OperationConfig,
        remote: str,
    ) -> None:
        self._resolver = resolver
        self._records = records
        self._git = git
        self._source = source
        self._cache = cache
        self._claims = claims
        self._operation = operation
        self._remote = remote

    async def _criterion(self, request: AuditClaimRequest) -> TrackerIssue:
        return await self._resolver.resolve_criterion(
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
                raise AuditClaimReadError(
                    "the Evidence repository substitutes Git objects"
                )
            head = await self._git.remote_branch_sha(repository, self._remote, branch)
            if not head:
                raise AuditClaimReadError("the recorded branch has no live remote head")
            if await self._source.resolve_commit(cwd=repository, ref=head) != head:
                raise AuditClaimReadError(
                    "the remote head did not resolve to the same commit"
                )
            if completed:
                if (
                    await self._source.resolve_commit(
                        cwd=repository, ref=evidence.graded_sha
                    )
                    != evidence.graded_sha
                ):
                    raise AuditClaimReadError(
                        "the graded reference is not its recorded commit"
                    )
                if not await self._git.is_ancestor(
                    repository, evidence.graded_sha, head
                ):
                    raise AuditClaimReadError(
                        "the graded commit is not on the recorded branch"
                    )
            return head

        head = await settle(observe())
        return head

    async def observe(self, request: AuditClaimRequest) -> AuditEvidenceObservation:
        """Read Evidence from its owner and revalidate source coherence at return."""
        try:
            return await self._observe(request)
        except AuditEvidenceReadError:
            raise
        except AUDIT_READ_FAILURES as exc:
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
            raise AuditClaimReadError(
                "a completed or configured review claim is required"
            )
        evidence = parse_audit_evidence(criterion.body)
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
        # A finished claim is verified afresh only while its recorded
        # grading still stands; whether it does is the one rule's answer,
        # never this reader's own comparison of the two shas.
        if not completed or (
            graded_state(graded_sha=evidence.graded_sha, head_sha=head)
            is GradedState.counted
        ):
            claim = await self._claims.verify(
                AuditClaimRequest.model_validate(
                    {**request.model_dump(), "record_ref": comment.comment_key}
                )
            )
            if claim.check != criterion_check(
                criterion=criterion, issue_key=request.lane_issue_key
            ):
                raise AuditClaimReadError("the fresh claim examined a different Check")
            verdict = claim.judgment.verdict
        if await self._criterion(request) != criterion:
            raise AuditClaimReadError(
                "the criterion changed during Evidence verification"
            )
        latest_record = await self._records.read(
            issue_key=request.lane_issue_key,
            lane_key=request.lane_key,
            record_ref=comment.comment_key,
        )
        if latest_record != (comment, record):
            raise AuditClaimReadError(
                "the lane record changed during Evidence verification"
            )
        latest_head = await settle(
            self._git.remote_branch_sha(repository, self._remote, record.branch)
        )
        if latest_head != head:
            raise AuditClaimReadError(
                "the remote head changed during Evidence verification"
            )
        if await read_replace_refs(git=self._git, workspace=repository):
            raise AuditClaimReadError("the Evidence repository substitutes Git objects")
        return AuditEvidenceObservation(
            criterion=criterion,
            recorded_evidence=evidence,
            head_sha=head,
            record_ref=comment.comment_key,
            verdict=verdict,
            current_claim=claim,
        )


class AuditRestampVerifier:
    """Trace one Evidence row's commit to the gradings the lane recorded.

    Restamping an Evidence row is a write; the gradings a lane actually ran
    are its own append-only stream. Nothing joined the two, so a row could
    name any commit and read as graded there. This reads the stream and
    says whether the row's commit is the one the LAST recorded grading
    names (KOD-506).

    ``observe`` takes no claim, judgment or verdict. An implementation that
    graded whether the restamped verdicts happen to be true is not merely
    unwired here, it has no parameter to arrive through.
    """

    def __init__(self, *, events: LaneEventHistory) -> None:
        self._events = events

    async def observe(
        self, *, request: AuditClaimRequest, evidence: CriterionEvidence
    ) -> AuditRestampTrace | None:
        """The row's trace, or ``None`` when no grading was ever recorded.

        A criterion whose history holds no recorded grading was never
        restamped by this lane and is not traced. Every cross-off the lane
        writes records its grading on the stream, so an empty history is a
        row no lane write accounts for at all — one a person moved into the
        finished state, or one whose announcement never landed — and reading
        it as "no entry at this commit" would answer for a write this stream
        never saw.

        The read is the lane issue's own stream, keyed to this criterion by
        ``subject_key`` — that is where a grading is posted. A failed or
        damaged read raises, so the sweep's one translation point turns it
        into an unavailable reason rather than a silent absence.
        """
        try:
            events = await self._events.lane_run_events(
                issue_key=request.lane_issue_key, lane_key=request.lane_key
            )
        except AUDIT_READ_FAILURES as exc:
            raise AuditEvidenceReadError(
                criterion_key=request.criterion_key,
                reason=f"the lane's recorded gradings could not be read: {exc}",
            ) from exc
        history = evidence_row_history(
            events=events, criterion_key=request.criterion_key
        )
        if not history:
            return None
        verdict = restamp_verdict(history=history, graded_sha=evidence.graded_sha)
        return AuditRestampTrace(
            criterion_key=request.criterion_key,
            recorded_evidence=evidence,
            history=history,
            verdict=verdict,
            reason=(
                f"{request.criterion_key}: the Evidence row names "
                f"{evidence.graded_sha}, and the last recorded grading is at "
                f"{history[-1]}"
            ),
        )
