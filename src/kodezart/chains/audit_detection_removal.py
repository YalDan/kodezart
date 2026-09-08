"""Read actual grading and branch sources before judging deleted detection."""

from kodezart.core.protocols import GitSourceReader, PromptSetProvider
from kodezart.domain.errors import AuditEvidenceReadError
from kodezart.services.audit_sessions import FreshAuditSession
from kodezart.services.audit_sources import AuditSourceReader, AuditSourceSnapshot
from kodezart.types.domain.agent import DETECTOR_REMOVAL_SCHEMA
from kodezart.types.domain.audit import AuditClaimRequest
from kodezart.types.domain.audit_detection_removal import (
    DetectorRemovalJudgment,
    DetectorRemovalObservation,
    RemovedSourceQuote,
)
from kodezart.types.domain.prompts import PromptKey


class DetectorRemovalVerifier:
    """Return one native-source observation without applying a refutation."""

    def __init__(
        self,
        *,
        sources: AuditSourceReader,
        sessions: FreshAuditSession,
        git: GitSourceReader,
        prompts: PromptSetProvider,
    ) -> None:
        self._sources = sources
        self._sessions = sessions
        self._git = git
        self._prompts = prompts

    async def _require_removed_quote(
        self, snapshot: AuditSourceSnapshot, quote: RemovedSourceQuote
    ) -> None:
        baseline = await self._git.read_source(
            cwd=snapshot.repository,
            commit_sha=snapshot.evidence.graded_sha,
            path=quote.path,
        )
        if (
            baseline.commit_sha != snapshot.evidence.graded_sha
            or baseline.path != quote.path
        ):
            raise ValueError("the baseline excerpt belongs to another source")
        quoted = quote.text.encode("utf-8")
        lines = baseline.content.splitlines(keepends=True)
        if not b"".join(lines[quote.line - 1 :]).startswith(quoted):
            raise ValueError("the excerpt is not exact source at its stated line")
        current = await self._git.find_source(
            cwd=snapshot.repository, commit_sha=snapshot.head_sha, path=quote.path
        )
        if current is not None:
            if current.commit_sha != snapshot.head_sha or current.path != quote.path:
                raise ValueError("the current excerpt belongs to another source")
            if quoted in current.content:
                raise ValueError("a reported removal is still present at the head")

    async def observe(self, request: AuditClaimRequest) -> DetectorRemovalObservation:
        """Inspect real revisions, then validate source quotations and coherence."""
        try:
            snapshot = await self._sources.read(request)
            key = PromptKey.AUDIT_DETECTION_REMOVAL
            prompt = self._prompts.template_for(key).render(
                {
                    "criterion_key": snapshot.criterion.issue_key,
                    "check": snapshot.check,
                    "graded_sha": snapshot.evidence.graded_sha,
                    "head_sha": snapshot.head_sha,
                }
            )
            payload = await self._sessions.judge(
                repository=snapshot.repository,
                head_sha=snapshot.head_sha,
                key=key,
                prompt=prompt,
                output_schema=DETECTOR_REMOVAL_SCHEMA,
                site="audit_detection_removal",
            )
            judgment = DetectorRemovalJudgment.model_validate(payload)
            if judgment.criterion_key != snapshot.criterion.issue_key:
                raise ValueError("the detector judgment names another criterion")
            for finding in judgment.findings:
                await self._require_removed_quote(snapshot, finding.mechanism)
                await self._require_removed_quote(snapshot, finding.detector)
            await self._sources.require_unchanged(snapshot)
            return DetectorRemovalObservation(
                judgment=judgment,
                graded_sha=snapshot.evidence.graded_sha,
                head_sha=snapshot.head_sha,
                record_ref=snapshot.comment.comment_key,
                check=snapshot.check,
            )
        except AuditEvidenceReadError:
            raise
        except Exception as exc:
            raise AuditEvidenceReadError(
                criterion_key=request.criterion_key, reason=str(exc)
            ) from exc
