"""Pure accounting for actual native precommit amendment reports."""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from kodezart.types.domain.amendment import (
    AmendmentClaim,
    AmendmentJudgment,
    AmendmentReport,
    AmendmentSubject,
    RepeatedUpheld,
    UpheldReason,
)
from kodezart.types.domain.criteria import CriterionVerdict
from kodezart.types.domain.operation import CheckPrerequisite

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


class AmendmentRequiresWriteError(NativeWriteRefusalError):
    """A reproduced ground still requires confirmed amendment writes."""

    def __init__(self, judgment: AmendmentJudgment) -> None:
        self.judgment = judgment
        super().__init__(
            "The ground was reproduced, but no confirmed amendment authorizes "
            "this departure; the harness will not commit or publish it."
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
            and not environment.get(capability, False)
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
