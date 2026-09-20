"""Return a refuted finished criterion to unstarted, once, with its evidence."""

from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import CriterionReopener
from kodezart.domain.errors import AuditClaimReadError, CriterionReadError
from kodezart.domain.tracker_writes import classification_surface
from kodezart.services.audit_publication import AuditPublisher
from kodezart.types.domain.audit_runtime import AuditRepairInput
from kodezart.types.domain.surface import WritableSurface
from kodezart.types.domain.tracker import TrackerIssue
from kodezart.types.domain.write_back import WriteBackFinding, WriteBackResult


class _CriterionReopen:
    """The one audit write that moves a criterion; a step the verifier drives.

    The step is defined beside the call that constructs it, with the port
    call inside its own ``write``, which is what the adoption scan
    recognises: a step's own write is verified by construction.  It
    composes no authored content — the evidence is the refutation comment
    already published and read back on the same criterion.
    """

    def __init__(
        self, *, tracker: CriterionReopener, expected: TrackerIssue, job_id: str
    ) -> None:
        self._tracker, self._expected, self._job_id = tracker, expected, job_id

    @property
    def surface(self) -> WritableSurface:
        return classification_surface(self._expected)

    async def write(self, *, finding: WriteBackFinding | None) -> None:
        # A repair round re-drives the same move rather than composing a
        # correction: there is no text to repair, and the port answers an
        # already-unstarted criterion with the criterion it read.
        del finding
        try:
            await settle(
                self._tracker.reset_criterion_pending(
                    expected=self._expected, holder=self._job_id
                )
            )
        except CriterionReadError as exc:
            # The port's own refusal becomes the audit's refusal at this
            # boundary: unhandled it would leave the run and starve every
            # later binding, and it says the same thing either way.
            raise AuditClaimReadError(
                f"the criterion changed before the reopen: {exc}"
            ) from exc


class AuditReopener:
    """Move a refuted finished criterion back to unstarted, once.

    It holds the one role it needs and nothing else.  The lease over the
    criterion's own surface belongs to the publisher, which already leases
    and verifies, so a reopen is one leased write-back like every other
    audit write.
    """

    def __init__(self, *, tracker: CriterionReopener) -> None:
        self._tracker = tracker

    async def reopen(
        self,
        *,
        criterion: TrackerIssue,
        ref: str,
        job_id: str,
        publisher: AuditPublisher,
        interrupted: list[AuditRepairInput],
    ) -> WriteBackResult:
        """Drive the reset of *criterion* through the publisher's write-back."""
        if "criterion" not in criterion.issue_labels or criterion.parent_key is None:
            raise AuditClaimReadError("only a criterion sub-issue is reopened")
        return await publisher.write_leased(
            step=_CriterionReopen(
                tracker=self._tracker, expected=criterion, job_id=job_id
            ),
            ref=ref,
            job_id=job_id,
            interrupted=interrupted,
        )
