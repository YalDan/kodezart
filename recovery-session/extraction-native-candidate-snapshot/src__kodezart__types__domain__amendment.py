"""Native departure claims and independent judgments, before any write."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.audit import AuditVerdict, TrackerArtifact
from kodezart.types.domain.criteria import (
    CriterionId,
    CriterionVerdict,
    FindingEvidence,
)
from kodezart.types.domain.operation import CheckPrerequisite
from kodezart.types.domain.ruling_id import RulingId
from kodezart.types.domain.surface import SurfaceKind
from kodezart.types.domain.write_back import WriteBackResult

Nonblank = Annotated[str, Field(min_length=1, pattern=r"\S")]
CommitSha = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]


class CriterionSubject(CamelCaseModel):
    """A criterion's existing native identity, never a newly minted AC number."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["criterion"] = Field(
        default="criterion", description="An existing native criterion subject."
    )
    id: CriterionId = Field(
        min_length=1,
        pattern=r"\S",
        description="The current criterion sub-issue's exact key.",
    )


class RulingSubject(CamelCaseModel):
    """A read-back ruling's existing identity."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["ruling"] = Field(
        default="ruling", description="An existing pinned ruling subject."
    )
    id: RulingId = Field(
        min_length=1,
        pattern=r"\S",
        description="The exact ruling identity read from the registry.",
    )


AmendmentSubject = Annotated[
    CriterionSubject | RulingSubject, Field(discriminator="kind")
]


class AmendmentGround(StrEnum):
    """The four grounds an independent semantic judgment can reproduce."""

    UNSATISFIABLE_AT_BASE = "unsatisfiable_at_base"
    MUTUALLY_UNSATISFIABLE = "mutually_unsatisfiable"
    PREMISE_FALSE_AT_BASE = "premise_false_at_base"
    REQUIRES_BREAKING_HOUSE_RULE = "requires_breaking_house_rule"


class AmendmentClaim(CamelCaseModel):
    """The departure alone; no writer reasoning or session transcript."""

    model_config = ConfigDict(frozen=True)
    subject: AmendmentSubject = Field(
        description=(
            "The current criterion or pinned ruling from which departure is proposed."
        )
    )
    stage: Literal["implementation"] = Field(
        description=(
            "The actual shared native writing stage, including remediation reentry."
        )
    )
    ground: AmendmentGround = Field(
        description=(
            "One of the four asserted semantic grounds, to be independently tested."
        )
    )
    departure: Nonblank = Field(
        description=(
            "The proposed behavioral departure alone, without writer "
            "reasoning or transcript."
        )
    )
    claimed_capability: CheckPrerequisite | None = Field(
        description=(
            "The typed runner capability claimed absent, or explicit absence "
            "of such a claim."
        )
    )


class NativeWriterOutput(CamelCaseModel):
    """Every native writer explicitly reports its proposed departures."""

    model_config = ConfigDict(frozen=True)
    claims: tuple[AmendmentClaim, ...] = Field(
        description=(
            "Every proposed departure, one per subject; explicitly empty when"
            " none is proposed."
        )
    )

    @model_validator(mode="after")
    def unique_subjects(self) -> Self:
        identities = [(claim.subject.kind, claim.subject.id) for claim in self.claims]
        if len(identities) != len(set(identities)):
            raise ValueError("a writer reports each subject once")
        return self


class BaseCitation(CamelCaseModel):
    """An auditable base source; quotation existence is not semantic proof."""

    model_config = ConfigDict(frozen=True)
    path: Nonblank = Field(
        description=(
            "A normalized repository-relative regular-file path at the exact "
            "base commit."
        )
    )
    quote: Nonblank = Field(
        description=(
            "Exact source bytes supporting the semantic judgment; existence "
            "alone proves no ground."
        )
    )

    @model_validator(mode="after")
    def repository_relative(self) -> Self:
        if (
            self.path.startswith("/")
            or any(part in {"", ".", ".."} for part in self.path.split("/"))
            or "\\" in self.path
        ):
            raise ValueError("a citation uses a normalized repository-relative path")
        return self


class AmendmentJudgment(CamelCaseModel):
    """Independent semantic output; reproduced is not an applied AMENDED verdict.

    The KOD-66 evidence/repair pair remains the shared fault-line vocabulary.
    This judgment adds the semantic ground instead of deriving one from quotes.
    """

    model_config = ConfigDict(frozen=True)
    subject: AmendmentSubject = Field(
        description="The exact claim subject being independently judged."
    )
    base_sha: CommitSha = Field(
        description=(
            "The complete immutable base commit inspected by this fresh session."
        )
    )
    ground: AmendmentGround = Field(
        description="The original asserted ground, preserved exactly."
    )
    reproduced: bool = Field(
        description=(
            "Whether the semantic ground was independently reproduced, "
            "defaulting to false without proof; never an applied AMENDED "
            "verdict."
        )
    )
    finding: FindingEvidence = Field(
        description=(
            "The shared feasibility evidence and smallest-repair pair, "
            "preserving the criterion/environment fault line."
        )
    )
    citations: tuple[BaseCitation, ...] = Field(
        description=(
            "Auditable sources at the exact base; a reproduced ground "
            "requires cited refutation."
        )
    )
    measured_by: Nonblank | None = Field(
        description=(
            "How an actual recorded cost measurement at this base was "
            "produced, or null if none was established."
        )
    )


class UpheldReason(StrEnum):
    """Why the existing subject survives this proposed departure."""

    GROUND_NOT_REPRODUCED = "ground_not_reproduced"
    ENVIRONMENT_LACKS_CAPABILITY = "environment_lacks_capability"
    COST_MEASURED_AFFORDABLE = "cost_measured_affordable"
    COST_MEASURED_UNECONOMIC = "cost_measured_uneconomic"


class UpheldJudgment(CamelCaseModel):
    """An accepted existing subject whose proposed departure was not actioned."""

    model_config = ConfigDict(frozen=True)
    verdict: Literal["upheld"] = "upheld"
    claim: AmendmentClaim
    reason: UpheldReason
    judgment: AmendmentJudgment

    @property
    def subject(self) -> AmendmentSubject:
        """The original claim's address, preserved without another identity map."""
        return self.claim.subject

    @model_validator(mode="after")
    def same_subject(self) -> Self:
        if self.subject != self.judgment.subject:
            raise ValueError("an upheld record retains its judgment's exact subject")
        if self.claim.ground is not self.judgment.ground:
            raise ValueError("the upheld record addresses the claimed ground")
        if self.reason in {
            UpheldReason.COST_MEASURED_AFFORDABLE,
            UpheldReason.COST_MEASURED_UNECONOMIC,
        }:
            cost = self.judgment.finding.cost_claim
            if (
                cost is None
                or cost.measurement is None
                or self.judgment.measured_by is None
            ):
                raise ValueError(
                    "a measured cost reason retains the actual measurement"
                )
            if cost.measurement.affordable != (
                self.reason is UpheldReason.COST_MEASURED_AFFORDABLE
            ):
                raise ValueError("the reason must match the measured affordability")
        if (
            self.reason is UpheldReason.ENVIRONMENT_LACKS_CAPABILITY
            and self.claim.claimed_capability is None
        ):
            raise ValueError(
                "a missing-capability reason requires the typed claimed capability"
            )
        return self


class RecordedRefusal(CamelCaseModel):
    """A refusal whose owning-issue record was independently verified."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["recorded"] = "recorded"
    record: WriteBackResult


class EscalatedRefusal(CamelCaseModel):
    """The measured uneconomic arm, with both actual canonical write results."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["escalated"] = "escalated"
    record: WriteBackResult
    escalation: WriteBackResult


RefusalPublication = Annotated[
    RecordedRefusal | EscalatedRefusal, Field(discriminator="kind")
]


class UpheldAmendment(UpheldJudgment):
    """The completed refusal includes its actual owning-issue publication."""

    publication: RefusalPublication

    @model_validator(mode="after")
    def verified_publication(self) -> Self:
        results = [self.publication.record]
        escalated = isinstance(self.publication, EscalatedRefusal)
        if isinstance(self.publication, EscalatedRefusal):
            results.append(self.publication.escalation)
        if escalated != (self.reason is UpheldReason.COST_MEASURED_UNECONOMIC):
            raise ValueError("only measured uneconomic refusals carry escalation")
        if any(result.verdict is not AuditVerdict.HOLDS for result in results):
            raise ValueError("a completed refusal requires verified publications")
        if any(
            result.artifact.surface.kind is not SurfaceKind.MARKER_COMMENT
            for result in results
        ):
            raise ValueError("refusal publications address canonical comment records")
        return self


class AmendedAmendment(CamelCaseModel):
    """An applied amendment, keeping its source and actual verification results."""

    model_config = ConfigDict(frozen=True)
    verdict: Literal["amended"] = "amended"
    claim: AmendmentClaim
    judgment: AmendmentJudgment
    prior: TrackerArtifact
    archive: WriteBackResult
    applied: WriteBackResult

    @property
    def subject(self) -> AmendmentSubject:
        """The existing native identity carried through this amendment."""
        return self.claim.subject

    @model_validator(mode="after")
    def same_verified_subject(self) -> Self:
        if (
            self.subject != self.judgment.subject
            or self.claim.ground is not self.judgment.ground
            or not self.judgment.reproduced
            or self.judgment.finding.verdict is not CriterionVerdict.infeasible
            or self.judgment.finding.cost_claim is not None
            or not self.judgment.citations
        ):
            raise ValueError("an amendment requires its own reproduced judgment")
        if any(
            result.verdict is not AuditVerdict.HOLDS
            for result in (self.archive, self.applied)
        ):
            raise ValueError("an amendment requires verified archive and applied text")
        if (
            self.applied.artifact.surface != self.prior.surface
            or self.applied.artifact.native_ref != self.prior.native_ref
            or self.archive.artifact.surface.ref != self.prior.surface.ref
            or self.archive.artifact.surface.kind is not SurfaceKind.MARKER_COMMENT
        ):
            raise ValueError("an amendment preserves its addressed native surface")
        expected_kind = (
            SurfaceKind.CRITERION_SUB_ISSUE
            if isinstance(self.subject, CriterionSubject)
            else SurfaceKind.MARKER_COMMENT
        )
        if self.prior.surface.kind is not expected_kind:
            raise ValueError("the amendment surface must match its subject kind")
        if isinstance(self.subject, CriterionSubject) and (
            self.prior.native_ref != self.subject.id
        ):
            raise ValueError("a criterion amendment preserves its native key")
        return self


AmendmentVerdict = Annotated[
    UpheldAmendment | AmendedAmendment, Field(discriminator="verdict")
]


class AmendmentReport(CamelCaseModel):
    """Actual precommit judgments retained by the native execution loop."""

    model_config = ConfigDict(frozen=True)
    verdicts: tuple[AmendmentVerdict, ...]

    @model_validator(mode="after")
    def unique_subjects(self) -> Self:
        identities = [(v.subject.kind, v.subject.id) for v in self.verdicts]
        if len(identities) != len(set(identities)):
            raise ValueError("a completed amendment report addresses each subject once")
        return self

    @property
    def upheld(self) -> tuple[UpheldAmendment, ...]:
        """Actual refusals driving the existing bounded continuation and counts."""
        return tuple(v for v in self.verdicts if isinstance(v, UpheldAmendment))


class RepeatedUpheld(CamelCaseModel):
    """One exact subject-kind, identity and reason observed at least twice."""

    model_config = ConfigDict(frozen=True)
    subject: AmendmentSubject
    reason: UpheldReason
    count: int = Field(ge=2)


class NativeWriterStart(CamelCaseModel):
    """Workspace identity and the registry supplied before the writer starts."""

    model_config = ConfigDict(frozen=True)
    head_sha: CommitSha
    instructions: str
