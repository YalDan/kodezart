"""Admission judgments preserve both refusal and unavailable evidence."""

from enum import StrEnum
from typing import Self

from pydantic import ConfigDict, Field, field_validator, model_validator

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


class AdmissionJudgment(CamelCaseModel):
    """Session-authored finding, without caller-owned revision metadata."""

    model_config = ConfigDict(frozen=True)

    issue_id: str = Field(min_length=1, description="Identity of the issue assessed.")
    verdict: AdmissionVerdict = Field(
        description="Buildable, not buildable, or unverifiable from available evidence."
    )
    invented_decision: str | None = Field(
        default=None,
        description="Decision the implementer must invent; required for refusal.",
    )
    missing_artifact: str | None = Field(
        default=None,
        description="Unavailable evidence preventing verification; otherwise null.",
    )
    pending_blocker_id: str | None = Field(
        default=None,
        description="Existing blocker for unavailable evidence; otherwise null.",
    )
    evidence: str = Field(
        description="Concrete source evidence supporting this judgment."
    )
    refusal_kind: RefusalKind | None = Field(
        default=None,
        description="Re-authoring or human decision required by refusal; else null.",
    )

    @model_validator(mode="after")
    def _require_refusal_evidence(self) -> Self:
        if self.verdict is AdmissionVerdict.NOT_BUILDABLE:
            if self.invented_decision is None or not self.invented_decision.strip():
                raise ValueError("NOT_BUILDABLE requires a nonempty invented_decision")
            if self.refusal_kind is None:
                raise ValueError("NOT_BUILDABLE requires refusal_kind")
        elif self.refusal_kind is not None:
            raise ValueError("refusal_kind must be None unless NOT_BUILDABLE")
        if self.verdict is AdmissionVerdict.UNVERIFIABLE:
            if self.missing_artifact is None or not self.missing_artifact.strip():
                raise ValueError("UNVERIFIABLE requires a nonempty missing_artifact")
            if self.pending_blocker_id is None or not self.pending_blocker_id.strip():
                raise ValueError("UNVERIFIABLE requires a nonempty pending_blocker_id")
        return self


class AdmissionResult(AdmissionJudgment):
    """A judgment bound by its caller to the exact body revision examined."""

    admitted_body_digest: str = Field(min_length=1, pattern=r"\S")


class DefectRole(StrEnum):
    """A defect instance or the instruction that makes writers reproduce it."""

    INSTANCE = "instance"
    MANDATE = "mandate"


class SpecFinding(CamelCaseModel):
    """Evidence for a class in the selected rubric, with any mandate verbatim."""

    model_config = ConfigDict(frozen=True)

    issue_id: str = Field(min_length=1)
    defect_class: str = Field(min_length=1)
    evidence: str
    role: DefectRole
    mandate_text: str | None = None

    @model_validator(mode="after")
    def _require_mandate_evidence(self) -> Self:
        if self.role is DefectRole.MANDATE:
            if self.mandate_text is None or not self.mandate_text.strip():
                raise ValueError("MANDATE requires a nonempty mandate_text")
        elif self.mandate_text is not None:
            raise ValueError("INSTANCE requires mandate_text to be None")
        return self


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
