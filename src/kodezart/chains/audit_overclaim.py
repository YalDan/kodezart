"""Fresh over-claim judgments, with native byte checks for adopted artifacts."""

from kodezart.core.protocols import GitSourceReader, PromptSetProvider
from kodezart.domain.errors import AuditClaimReadError, AssertionComparisonError
from kodezart.services.audit_sessions import FreshAuditSession
from kodezart.services.audit_sources import AuditSourceReader, AuditSourceSnapshot
from kodezart.types.domain.agent import AUDIT_OVERCLAIM_SCHEMA
from kodezart.types.domain.audit import AuditClaimRequest, AuditVerdict
from kodezart.types.domain.audit_overclaim import (
    AuditOverclaimJudgment,
    AuditOverclaimObservation,
    OverclaimKind,
    OverclaimReading,
)
from kodezart.types.domain.prompts import PromptKey


class AuditOverclaimVerifier:
    """Observe four standing checks without publishing an incomplete refutation."""

    def __init__(
        self,
        *,
        sources: AuditSourceReader,
        sessions: FreshAuditSession,
        prompts: PromptSetProvider,
        git: GitSourceReader,
    ) -> None:
        self._sources = sources
        self._sessions = sessions
        self._prompts = prompts
        self._git = git

    async def _adoption(
        self, snapshot: AuditSourceSnapshot, judgment: AuditOverclaimJudgment
    ) -> AuditOverclaimJudgment:
        """Verify addressed repository bytes independently of model coverage."""
        if not judgment.byte_pairs:
            return judgment
        differences: list[str] = []
        missing: list[str] = []
        for pair in judgment.byte_pairs:
            if pair.source_sha not in {snapshot.evidence.graded_sha, snapshot.head_sha}:
                raise AuditClaimReadError(
                    "adoption source is outside the audited revisions"
                )
            if (
                pair.source_sha == snapshot.head_sha
                and pair.source_path == pair.artifact_path
            ):
                raise AuditClaimReadError("an adopted artifact cannot witness itself")
            blobs = []
            for sha, path in (
                (pair.source_sha, pair.source_path),
                (snapshot.head_sha, pair.artifact_path),
            ):
                try:
                    blob = await self._git.read_source(
                        cwd=snapshot.repository, commit_sha=sha, path=path
                    )
                except AssertionComparisonError:
                    missing.append(f"{sha}:{path}")
                    continue
                if blob.commit_sha != sha or blob.path != path:
                    raise AuditClaimReadError("native adoption source changed identity")
                blobs.append(blob)
            if len(blobs) == 2 and blobs[0].content != blobs[1].content:
                differences.append(
                    f"{pair.source_sha}:{pair.source_path} differs byte-wise from "
                    f"{snapshot.head_sha}:{pair.artifact_path}"
                )
        old = next(
            item for item in judgment.checks if item.kind is OverclaimKind.ADOPTION
        )
        if differences:
            replacement = OverclaimReading(
                kind=OverclaimKind.ADOPTION,
                verdict=AuditVerdict.REFUTED,
                evidence="Native Git byte comparison: " + "; ".join(differences),
                recomputed_value=None,
                missing_artifact=None,
            )
        elif missing:
            replacement = OverclaimReading(
                kind=OverclaimKind.ADOPTION,
                verdict=AuditVerdict.UNVERIFIABLE,
                evidence="Native Git could not read every named adoption witness.",
                recomputed_value=None,
                missing_artifact="; ".join(missing),
            )
        elif old.verdict is AuditVerdict.REFUTED:
            raise AuditClaimReadError(
                "adoption refutation contradicts the named native bytes"
            )
        else:
            # A partial list cannot settle a model's separately named missing
            # external witness; native equality never manufactures coverage.
            replacement = old
        return judgment.model_copy(
            update={
                "checks": tuple(
                    replacement if item.kind is OverclaimKind.ADOPTION else item
                    for item in judgment.checks
                )
            }
        )

    async def observe(self, request: AuditClaimRequest) -> AuditOverclaimObservation:
        snapshot = await self._sources.read(request)
        key = PromptKey.AUDIT_OVERCLAIM
        prompt = self._prompts.template_for(key).render(
            {
                "criterion_key": snapshot.criterion.issue_key,
                "graded_sha": snapshot.evidence.graded_sha,
                "head_sha": snapshot.head_sha,
                "check": snapshot.check,
            }
        )
        output = await self._sessions.judge(
            repository=snapshot.repository,
            head_sha=snapshot.head_sha,
            key=key,
            prompt=prompt,
            output_schema=AUDIT_OVERCLAIM_SCHEMA,
            site="audit_overclaim",
        )
        judgment = AuditOverclaimJudgment.model_validate(output)
        if judgment.criterion_key != snapshot.criterion.issue_key:
            raise AuditClaimReadError("over-claim judgment names another criterion")
        judgment = await self._adoption(snapshot, judgment)
        await self._sources.require_unchanged(snapshot)
        return AuditOverclaimObservation(
            judgment=judgment,
            graded_sha=snapshot.evidence.graded_sha,
            head_sha=snapshot.head_sha,
            record_ref=snapshot.comment.comment_key,
            check=snapshot.check,
        )
