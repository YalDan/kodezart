"""Actual native audit publication receipts and incomplete coverage evidence."""

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.audit import (
    AuditClaimReport,
    AuditCoverageResult,
    AuditVerdict,
    TrackerArtifact,
)
from kodezart.types.domain.audit_detection_removal import (
    DetectorRemovalObservation,
    DetectorRemovalReportEntry,
)
from kodezart.types.domain.audit_evidence import AuditEvidenceObservation
from kodezart.types.domain.audit_forge import AuditForgeObservation
from kodezart.types.domain.audit_overclaim import (
    AuditOverclaimObservation,
    OverclaimReportEntry,
)
from kodezart.types.domain.audit_terminal import (
    AuditTerminalObservation,
    AuditTerminalReport,
)
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.surface import WritableSurface
from kodezart.types.domain.write_back import WriteBackFinding, WriteBackResult


class AuditClaimPublication(CamelCaseModel):
    """One completed detector report, preserving its native finding identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["claim"] = "claim"
    detector: Literal["current_check"] = Field(
        description="The native claim detector which produced this completed report."
    )
    report: AuditClaimReport


class AuditForgePublication(CamelCaseModel):
    """Historical checks at recorded Evidence, distinct from a live branch head."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["forge"] = "forge"
    graded_sha: str = Field(
        pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$",
        description="Actual historical Evidence commit observed by the forge reader.",
    )
    report: AuditClaimReport

    @model_validator(mode="after")
    def _recorded_revision_matches_report(self) -> Self:
        if self.report.claim.head_sha != self.graded_sha:
            raise ValueError("the forge report differs from its recorded Evidence SHA")
        return self


class AuditOverclaimPublication(CamelCaseModel):
    """Preserve the exact standing category and its completed mandate report."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["overclaim"] = "overclaim"
    entry: OverclaimReportEntry

    @property
    def report(self) -> AuditClaimReport:
        return self.entry.report


class AuditRemovalPublication(CamelCaseModel):
    """Preserve the actual source-checked finding, never an ordinal identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["detector_removal"] = "detector_removal"
    entry: DetectorRemovalReportEntry

    @property
    def report(self) -> AuditClaimReport:
        return self.entry.report


class AuditTerminalPublication(CamelCaseModel):
    """A completed native terminal observation, without state-change authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["terminal"] = "terminal"
    report: AuditTerminalReport


type AuditPublication = Annotated[
    AuditClaimPublication
    | AuditForgePublication
    | AuditOverclaimPublication
    | AuditRemovalPublication
    | AuditTerminalPublication,
    Field(discriminator="kind"),
]


class AuditPublishedArtifact(CamelCaseModel):
    """Publication requires the actual completed escalation for an instruction."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    publication: AuditPublication
    escalation_refs: tuple[str, ...]

    @model_validator(mode="after")
    def _instruction_has_recorded_escalation(self) -> Self:
        verdict = (
            self.publication.report.observation.verdict
            if isinstance(self.publication, AuditTerminalPublication)
            else self.publication.report.claim.judgment.verdict
        )
        if verdict is AuditVerdict.UNVERIFIABLE:
            raise ValueError(
                "an unverifiable observation is not an authorized publication"
            )
        if any(not ref.strip() for ref in self.escalation_refs) or len(
            set(self.escalation_refs)
        ) != len(self.escalation_refs):
            raise ValueError("escalation references must be nonblank and unique")
        mandate = self.publication.report.mandate
        if mandate is not None and mandate.verdict is AuditVerdict.HOLDS:
            if not self.escalation_refs or any(
                not ref.strip() for ref in self.escalation_refs
            ):
                raise ValueError(
                    "an instructed mandate requires actual escalation references"
                )
        return self


class AuditUnavailable(CamelCaseModel):
    """An actual identity which did not establish completed audit coverage."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    subject: ScopeRef
    reason: str = Field(min_length=1, pattern=r"\S")


class _AuditScopeReport(CamelCaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scope: ScopeRef
    writes: tuple[WriteBackResult, ...]
    observations: tuple[AuditPublication, ...] = Field(
        default=(),
        description=(
            "Actual completed read reports, including fresh repair "
            "observations in attempt order."
        ),
    )
    raw_observations: tuple[
        AuditEvidenceObservation
        | AuditForgeObservation
        | AuditTerminalObservation
        | AuditOverclaimObservation
        | DetectorRemovalObservation,
        ...,
    ] = ()


class AuditScopeComplete(_AuditScopeReport):
    """Only a completely published selection can carry successful coverage."""

    status: Literal["complete"] = "complete"
    coverage: AuditCoverageResult
    writes: tuple[WriteBackResult, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _completed_coverage_is_own_and_verified(self) -> Self:
        if self.coverage.scope != self.scope:
            raise ValueError("completed audit coverage belongs to another scope")
        if any(result.verdict is not AuditVerdict.HOLDS for result in self.writes):
            raise ValueError("completed audit coverage contains an unverified write")
        return self


class AuditRepairInput(CamelCaseModel):
    """Actual prior judgment delivered to an interrupted canonical repair step."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    surface: WritableSurface = Field(
        description="Full address of the interrupted writing step."
    )
    ref: str = Field(
        pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$",
        description="Actual immutable commit passed to the canonical verifier.",
    )
    preceding_round: int = Field(
        ge=1,
        strict=True,
        description="Actual ordinal of the prior judgment supplied to this repair.",
    )
    finding: WriteBackFinding = Field(
        description=(
            "The exact received repair input; not a new judgment or completed result."
        )
    )


class AuditScopeIncomplete(_AuditScopeReport):
    """Retain successful writes alongside the actual reason coverage stopped."""

    status: Literal["incomplete"] = "incomplete"
    unavailable: tuple[AuditUnavailable, ...] = Field(min_length=1)
    repair_inputs: tuple[AuditRepairInput, ...] = Field(
        default=(),
        description=(
            "Received repair inputs for attempts interrupted before "
            "a completed WriteBackResult existed."
        ),
    )


type AuditScopeReport = Annotated[
    AuditScopeComplete | AuditScopeIncomplete, Field(discriminator="status")
]


class AuditRunReport(CamelCaseModel):
    """The real scheduler identity and each independently attempted binding."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    identity: RunIdentity
    scopes: tuple[AuditScopeReport, ...]

    @model_validator(mode="after")
    def _actual_audit_identity_and_unique_scope(self) -> Self:
        if self.identity.kind is not RunKind.AUDIT:
            raise ValueError("an audit report requires the actual audit run kind")
        scopes = [entry.scope for entry in self.scopes]
        if len(set(scopes)) != len(scopes):
            raise ValueError("an audit invocation cannot duplicate a scope binding")
        return self


class AuditScopeSummary(CamelCaseModel):
    """A report references only the actual successfully verified native records."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    identity: RunIdentity
    coverage: AuditCoverageResult
    record_refs: tuple[Annotated[str, Field(min_length=1, pattern=r"\S")], ...]
    records: tuple[TrackerArtifact, ...] = Field(
        description=(
            "Exact addressed native artifacts reread before publication, supplying "
            "the referenced record contents to the independent canonical judge."
        ),
    )

    @model_validator(mode="after")
    def _references_resolve_to_distinct_addressed_records(self) -> Self:
        if self.identity.kind is not RunKind.AUDIT:
            raise ValueError("an audit summary requires the actual audit run kind")
        if self.record_refs != tuple(record.native_ref for record in self.records):
            raise ValueError(
                "summary references must match the observed native records"
            )
        if len(set(self.record_refs)) != len(self.record_refs):
            raise ValueError("summary records must have distinct native identities")
        if len({record.surface for record in self.records}) != len(self.records):
            raise ValueError("summary records must have distinct full addresses")
        return self
