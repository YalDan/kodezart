"""Pure accounting for actual native precommit amendment reports."""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from kodezart.types.domain.amendment import (
    AmendedAmendment,
    AmendmentClaim,
    AmendmentJudgment,
    AmendmentReport,
    AmendmentSubject,
    CriterionSubject,
    RepeatedUpheld,
    RulingSubject,
    UpheldReason,
)
from kodezart.types.domain.criteria import CriterionVerdict
from kodezart.types.domain.operation import CheckPrerequisite
from kodezart.types.domain.ruling_id import RulingId
from kodezart.types.domain.write_back import WriteBackResult

if TYPE_CHECKING:
    from kodezart.types.domain.agent import WorkflowIterationEvent


class NativeWriteRefusalError(Exception):
    """A native writer cannot authorize a harness commit or publication."""


class NativeAmendmentRefusalError(NativeWriteRefusalError):
    """An ending UPHELD attempt, with its real report and prior observation."""

    def __init__(
        self,
        *,
        report: AmendmentReport,
        last_iteration: "WorkflowIterationEvent | None",
    ) -> None:
        self.report = report
        self.last_iteration = last_iteration
        super().__init__(
            "The final native departure remains UPHELD; no new evaluation "
            "or acceptance was produced for that unactioned attempt"
        )


class AmendmentWriteBackRefusalError(NativeWriteRefusalError):
    """An unsettled canonical write, retaining the actual artifact and all rounds."""

    def __init__(self, *, result: WriteBackResult) -> None:
        self.result = result
        super().__init__(
            "Canonical amendment write-back exhausted its configured bound"
        )


class AssertionWeakenedError(NativeWriteRefusalError):
    """A harness commit loses an assertion a pinned record designates.

    ``marks`` holds the keys of the criteria the loss was minted as, in the
    order they were minted, and is empty when the refusal came before any
    mint could be made.
    """

    def __init__(self, *, lane_key: str, marks: tuple[str, ...]) -> None:
        self.lane_key = lane_key
        self.marks = marks
        super().__init__(
            "The harness commit loses an assertion a pinned record designates; "
            "it is not published and the lane carries the obligation"
        )


def amended_records(report: AmendmentReport) -> frozenset[RulingId]:
    """The pinned records this report amended, by their own identities.

    Only an applied amendment of a record exempts it: an upheld departure
    changed nothing, and a criterion subject designates no test at all.
    """
    return frozenset(
        verdict.subject.id
        for verdict in report.verdicts
        if isinstance(verdict, AmendedAmendment)
        and isinstance(verdict.subject, RulingSubject)
    )


def upheld_reason(
    claim: AmendmentClaim,
    judgment: AmendmentJudgment,
    *,
    environment: Mapping[CheckPrerequisite, bool] | None,
) -> UpheldReason | None:
    """Keep cost and environment limits separate from reproduced semantic grounds.

    None is a candidate requiring canonical amendment writes, never AMENDED.
    Source/identity verification and those writes belong to the consuming owner.
    """
    cost = judgment.finding.cost_claim
    if cost is not None:
        if (
            cost.measurement is None
            or judgment.measured_by is None
            or not judgment.citations
        ):
            return UpheldReason.GROUND_NOT_REPRODUCED
        return (
            UpheldReason.COST_MEASURED_AFFORDABLE
            if cost.measurement.affordable
            else UpheldReason.COST_MEASURED_UNECONOMIC
        )
    if judgment.finding.verdict is CriterionVerdict.unverifiable:
        capability = claim.claimed_capability
        if (
            capability is not None
            and environment is not None
            and environment.get(capability) is False
        ):
            return UpheldReason.ENVIRONMENT_LACKS_CAPABILITY
        return UpheldReason.GROUND_NOT_REPRODUCED
    if not judgment.reproduced:
        return UpheldReason.GROUND_NOT_REPRODUCED
    if (
        judgment.finding.verdict is not CriterionVerdict.infeasible
        or not judgment.citations
    ):
        raise NativeWriteRefusalError("A reproduced ground requires cited refutation")
    return None


def escalation_question(
    *,
    reason: UpheldReason,
    claim: AmendmentClaim,
    judgment: AmendmentJudgment,
) -> str:
    """Compose the one question a refusal at an escalating reason asks a person.

    Every reason is answered here, so a reason that raises no escalation is a
    typed refusal rather than an empty question reaching a canonical write.
    """
    match reason:
        case UpheldReason.COST_MEASURED_UNECONOMIC:
            return f"Resolve the measured uneconomic departure for {claim.subject.id}"
        case UpheldReason.ENVIRONMENT_LACKS_CAPABILITY:
            capability = claim.claimed_capability
            if capability is None:
                raise NativeWriteRefusalError(
                    "A missing-capability escalation requires the typed "
                    "claimed capability"
                )
            # The escalation classifies this issue `decision`, and the plan read
            # refuses while it carries that, so removing it is part of revival.
            revival = (
                f"Resolve the missing capability {capability.value} for "
                f"{claim.subject.id}: the demonstration needs "
                f"{judgment.finding.missing_resource}, which the declared "
                "runner environment does not provide. Declaring "
                f"{capability.value} in the repository's runner environment "
                "and removing the decision classification from this issue, "
                "then firing again, revives it"
            )
            if isinstance(claim.subject, CriterionSubject):
                return (
                    f"{revival}; otherwise a person cancels the criterion "
                    "with a supersession."
                )
            return f"{revival}."
        case _:
            raise NativeWriteRefusalError(f"No escalation is raised at {reason.value}")


def repeated_upheld(reports: Sequence[AmendmentReport]) -> tuple[RepeatedUpheld, ...]:
    """Count exact subjects and reasons without changing loop trajectory."""
    counts: Counter[tuple[str, str, UpheldReason]] = Counter()
    subjects: dict[tuple[str, str, UpheldReason], AmendmentSubject] = {}
    for report in reports:
        for upheld in report.upheld:
            key = upheld.subject.kind, upheld.subject.id, upheld.reason
            counts[key] += 1
            subjects[key] = upheld.subject
    return tuple(
        RepeatedUpheld(subject=subjects[key], reason=key[2], count=count)
        for key, count in sorted(counts.items())
        if count >= 2
    )
