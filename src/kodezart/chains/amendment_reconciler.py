"""Rule on a deviating writer's claim from the repository, not from the claim.

The reconciler answers one question: does the repository at the resolved
base itself bear out the evidence this claim offers?  It holds a criteria
READ and an immutable source read and nothing else — no write reaches the
tracker from here, so a criterion's text is what it was however the claim
is ruled.
"""

from collections.abc import Sequence

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import GitSourceReader, TrackerCriteriaReader
from kodezart.types.domain.amendment import (
    AmendmentClaim,
    AmendmentDecision,
    AmendmentVerdict,
    GroundEvidence,
)
from kodezart.types.domain.tracker import TrackerIssue


class AmendmentReconciler:
    """Reproduce a claim's evidence at the resolved base, then rule on it.

    ``UPHELD`` needs no proof and is what every path that fails to
    reproduce arrives at.  ``AMENDED`` is reached only when the
    reconciler read every address the claim offered at the resolved base
    and found the quoted text in the bytes it read back.  A read that
    FAILED is never one of these outcomes: it raises, because a claim
    nobody could read the evidence for has not been ruled on at all.
    """

    def __init__(
        self,
        *,
        tracker: TrackerCriteriaReader,
        source: GitSourceReader,
    ) -> None:
        self._tracker = tracker
        self._source = source
        self._log: BoundLogger = get_logger(__name__)

    async def reconcile(
        self,
        claim: AmendmentClaim,
        *,
        issue_key: str,
        repository: str,
        base_ref: str,
    ) -> AmendmentVerdict:
        """The verdict on *claim*, measured against *base_ref*'s own commit."""
        criteria = await self._tracker.read_criteria(issue_key=issue_key)
        self._require_subject(claim, criteria)
        base_sha = await self._source.resolve_commit(cwd=repository, ref=base_ref)
        reproduced = await self._reproduce(
            claim.asserted_evidence,
            repository=repository,
            base_sha=base_sha,
        )
        if reproduced is None:
            await self._log.ainfo(
                "amendment_upheld",
                subject_id=str(claim.subject_id),
                asserted_ground=claim.asserted_ground.value,
                base_sha=base_sha,
            )
            return AmendmentVerdict(
                subject_id=claim.subject_id,
                decision=AmendmentDecision.UPHELD,
            )
        await self._log.ainfo(
            "amendment_amended",
            subject_id=str(claim.subject_id),
            ground=claim.asserted_ground.value,
            base_sha=base_sha,
        )
        return AmendmentVerdict(
            subject_id=claim.subject_id,
            decision=AmendmentDecision.AMENDED,
            ground=claim.asserted_ground,
            reproduced=reproduced,
        )

    def _require_subject(
        self,
        claim: AmendmentClaim,
        criteria: Sequence[TrackerIssue],
    ) -> None:
        """Refuse a claim whose subject is not one of the issue's criteria.

        A subject the issue does not own is not an amendment this
        reconciler can rule on either way, so it is neither upheld nor
        amended: it refuses.
        """
        if not any(criterion.issue_key == claim.subject_id for criterion in criteria):
            msg = "the claimed subject is not a criterion sub-issue of the issue"
            raise ValueError(msg)

    async def _reproduce(
        self,
        evidence: Sequence[GroundEvidence],
        *,
        repository: str,
        base_sha: str,
    ) -> tuple[GroundEvidence, ...] | None:
        """*evidence* re-read at *base_sha*, or ``None`` when any of it fails.

        Every address has to hold.  Offering five addresses of which one
        bears out is not four fifths of a ground; and offering none is
        nothing to reproduce, which is why the empty claim lands here
        rather than in a vacuous truth.
        """
        if not evidence:
            return None
        for item in evidence:
            blob = await self._source.find_source(
                cwd=repository,
                commit_sha=base_sha,
                path=item.path,
            )
            if blob is None or item.quote.encode("utf-8") not in blob.content:
                return None
        return tuple(evidence)
