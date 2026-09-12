"""Admission judgments preserve both refusal and unavailable evidence."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, RootModel, field_validator, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.prompts import PromptKey


class AdmissionVerdict(StrEnum):
    """A buildability finding is a three-way decision, never a boolean."""

    BUILDABLE = "buildable"
    NOT_BUILDABLE = "not_buildable"
    UNVERIFIABLE = "unverifiable"

    def __bool__(self) -> bool:
        raise TypeError("AdmissionVerdict requires an explicit three-state comparison")


class RefusalKind(StrEnum):
    """Whether re-authoring can repair a refusal without a human decision."""

    SPEC_GAP = "spec_gap"
    HUMAN_DECISION = "human_decision"


class AdmissionRoute(StrEnum):
    """The next admission action; choosing one performs no tracker write."""

    MARK_COMPLETE = "mark_complete"
    REAUTHOR = "reauthor"
    ESCALATE = "escalate"


class DefectRole(StrEnum):
    """A defect instance or the instruction that makes writers reproduce it."""

    INSTANCE = "instance"
    MANDATE = "mandate"


class SpecFinding(CamelCaseModel):
    """Evidence for a class in the selected rubric, with any mandate verbatim."""

    model_config = ConfigDict(frozen=True)

    issue_id: str = Field(
        min_length=1, description="Tracker key owning the source finding."
    )
    defect_class: str = Field(
        min_length=1, description="Defect class from the selected rubric."
    )
    evidence: str = Field(description="Concrete evidence establishing the finding.")
    role: DefectRole = Field(
        description="An instance or the instruction that mandates it."
    )
    mandate_text: str | None = Field(
        default=None,
        description="Exact instructing sentence for MANDATE, absent for INSTANCE.",
    )

    @model_validator(mode="after")
    def _require_mandate_evidence(self) -> Self:
        if self.role is DefectRole.MANDATE:
            if self.mandate_text is None or not self.mandate_text.strip():
                raise ValueError("MANDATE requires a nonempty mandate_text")
        elif self.mandate_text is not None:
            raise ValueError("INSTANCE requires mandate_text to be None")
        return self


class _AdmissionFields(CamelCaseModel):
    verdict: AdmissionVerdict
    model_config = ConfigDict(frozen=True)

    issue_id: str = Field(min_length=1, pattern=r"\S")
    evidence: str = Field(min_length=1, pattern=r"\S")
    findings: tuple[SpecFinding, ...] = ()


class BuildableAdmission(_AdmissionFields):
    """No invented decision or unavailable artifact is carried by success."""

    verdict: Literal[AdmissionVerdict.BUILDABLE]


class RefusedAdmission(_AdmissionFields):
    """A refusal names the decision and the route it requires."""

    verdict: Literal[AdmissionVerdict.NOT_BUILDABLE]
    invented_decision: str = Field(min_length=1, pattern=r"\S")
    refusal_kind: RefusalKind


class UnverifiableAdmission(_AdmissionFields):
    """Unavailable evidence retains its named dependency without inventing it."""

    verdict: Literal[AdmissionVerdict.UNVERIFIABLE]
    missing_artifact: str = Field(min_length=1, pattern=r"\S")
    pending_blocker_id: str = Field(min_length=1, pattern=r"\S")


AdmissionDecision = Annotated[
    BuildableAdmission | RefusedAdmission | UnverifiableAdmission,
    Field(discriminator="verdict"),
]


class _AdmissionView[T: _AdmissionFields](RootModel[T]):
    model_config = ConfigDict(frozen=True)

    @property
    def issue_id(self) -> str:
        return self.root.issue_id

    @property
    def evidence(self) -> str:
        return self.root.evidence

    @property
    def findings(self) -> tuple[SpecFinding, ...]:
        return self.root.findings

    @property
    def verdict(self) -> AdmissionVerdict:
        return self.root.verdict

    @property
    def refusal_kind(self) -> RefusalKind | None:
        return (
            self.root.refusal_kind if isinstance(self.root, RefusedAdmission) else None
        )

    @property
    def invented_decision(self) -> str | None:
        return (
            self.root.invented_decision
            if isinstance(self.root, RefusedAdmission)
            else None
        )

    @property
    def pending_blocker_id(self) -> str | None:
        return (
            self.root.pending_blocker_id
            if isinstance(self.root, UnverifiableAdmission)
            else None
        )

    @property
    def missing_artifact(self) -> str | None:
        return (
            self.root.missing_artifact
            if isinstance(self.root, UnverifiableAdmission)
            else None
        )


class AdmissionJudgment(_AdmissionView[AdmissionDecision]):
    """The agent sees the same discriminated legal states its consumer validates."""


class _AdmittedRevision(CamelCaseModel):
    admitted_body_digest: str = Field(min_length=1, pattern=r"\S")


class _BuildableResult(BuildableAdmission, _AdmittedRevision):
    pass


class _RefusedResult(RefusedAdmission, _AdmittedRevision):
    pass


class _UnverifiableResult(UnverifiableAdmission, _AdmittedRevision):
    pass


BoundAdmissionDecision = Annotated[
    _BuildableResult | _RefusedResult | _UnverifiableResult,
    Field(discriminator="verdict"),
]


class AdmissionResult(_AdmissionView[BoundAdmissionDecision]):
    """The caller binds each legal judgment to the exact body it examined."""

    @property
    def admitted_body_digest(self) -> str:
        return self.root.admitted_body_digest


class MandateKind(StrEnum):
    """The phases of one organize pass, before scope approval."""

    GROOM = "groom"
    TICKET = "ticket"
    CRITERIA = "criteria"


class OrganizeLabelNamespace(StrEnum):
    """The operation mappings a phase may reference explicitly."""

    SCOPE = "scope_labels"
    ISSUE = "issue_labels"


def split_label_key(reference: str) -> tuple[OrganizeLabelNamespace, str]:
    """Parse a qualified mapping key without guessing from the phase kind."""
    namespace, separator, key = reference.partition(".")
    if not separator or not key.strip():
        raise ValueError(
            "label reference requires a namespace and nonempty mapping key"
        )
    return OrganizeLabelNamespace(namespace), key


class MandateSpec(CamelCaseModel):
    """One phase's configured differences, with explicit mapping references."""

    model_config = ConfigDict(frozen=True)

    kind: MandateKind
    gate_label_key: str
    rubric_prompt_key: PromptKey
    admission_prompt_key: PromptKey
    terminal_marker_key: str

    @field_validator("gate_label_key", "terminal_marker_key")
    @classmethod
    def _require_qualified_label_key(cls, value: str) -> str:
        split_label_key(value)
        return value

    @field_validator("terminal_marker_key")
    @classmethod
    def _require_issue_phase_marker(cls, value: str) -> str:
        namespace, _ = split_label_key(value)
        if namespace is not OrganizeLabelNamespace.ISSUE:
            raise ValueError("an organize phase terminates on an issue_labels marker")
        return value


class ResolvedMandateSpec(CamelCaseModel):
    """A validated phase specification and the configured labels it names."""

    model_config = ConfigDict(frozen=True)

    spec: MandateSpec
    gate_label: str
    terminal_marker: str


class OrganizeAdmissionRequest(CamelCaseModel):
    """Source identity, rubric and repository base for one fresh judgment."""

    model_config = ConfigDict(frozen=True)

    issue_key: str = Field(min_length=1)
    mandate_rubric: str = Field(min_length=1)
    repo_url: str = Field(min_length=1)
    base_ref: str = Field(min_length=1)
    cache_key: str | None = None
    defect_classes: tuple[str, ...] = ()
    admission_prompt_key: PromptKey = PromptKey.ORGANIZE_ASSESS
