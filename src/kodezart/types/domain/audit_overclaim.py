"""The four standing over-claim checks and their source-addressed evidence."""

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.audit import AuditVerdict


class OverclaimKind(StrEnum):
    AGGREGATE = "aggregate"
    COMPLETENESS = "completeness"
    ADOPTION = "adoption"
    SELF_RULE = "self_rule"


class AuditBytePair(CamelCaseModel):
    """Repository artifacts whose claimed adoption can be checked mechanically."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    source_sha: str = Field(
        description="Exact supplied graded or current commit of the source."
    )
    source_path: str = Field(
        description="Canonical repository-relative path of the adopted source."
    )
    artifact_path: str = Field(
        description="Canonical repository-relative path of the current rendered artifact."
    )

    @model_validator(mode="after")
    def address_is_canonical(self) -> Self:
        for value in (self.source_path, self.artifact_path):
            path = PurePosixPath(value)
            if (
                not value
                or path.is_absolute()
                or str(path) != value
                or ".." in path.parts
                or "\x00" in value
                or value == "."
            ):
                raise ValueError(
                    "adoption evidence needs canonical repository-relative paths"
                )
        return self


class OverclaimReading(CamelCaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: OverclaimKind = Field(description="The standing proposition being checked.")
    verdict: AuditVerdict = Field(
        description="Whether this category of claim holds, is refuted, or cannot be verified."
    )
    evidence: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Concrete observed source facts; explain absence when no claim of this kind exists.",
    )
    recomputed_value: str | None = Field(
        description="The independently recomputed aggregate, required for an aggregate refutation; otherwise null."
    )
    missing_artifact: str | None = Field(
        description="The specific missing or unreadable falsifying artifact for an unverifiable result; otherwise null."
    )

    @model_validator(mode="after")
    def evidence_supports_state(self) -> Self:
        if (
            self.kind is OverclaimKind.AGGREGATE
            and self.verdict is AuditVerdict.REFUTED
        ):
            if self.recomputed_value is None or not self.recomputed_value.strip():
                raise ValueError("aggregate refutation must carry its recomputation")
        elif self.recomputed_value is not None:
            raise ValueError("only an aggregate refutation carries a recomputed value")
        if self.verdict is AuditVerdict.UNVERIFIABLE:
            if self.missing_artifact is None or not self.missing_artifact.strip():
                raise ValueError("unverifiability must name the missing artifact")
        elif self.missing_artifact is not None:
            raise ValueError(
                "a verified or refuted claim cannot claim a missing witness"
            )
        return self


class AuditOverclaimJudgment(CamelCaseModel):
    """Every standing check is present; no model-authored overall verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    criterion_key: str = Field(
        min_length=1, description="The exact dispatched native criterion key."
    )
    checks: tuple[OverclaimReading, ...] = Field(
        description="Exactly one reading for each of the four standing over-claim categories."
    )
    byte_pairs: tuple[AuditBytePair, ...] = Field(
        description="Every repository adoption pair cited by the adoption check, for native byte comparison; empty when none is claimed or readable."
    )

    @model_validator(mode="after")
    def all_checks_are_covered(self) -> Self:
        kinds = [reading.kind for reading in self.checks]
        if len(kinds) != len(set(kinds)) or set(kinds) != set(OverclaimKind):
            raise ValueError("every standing over-claim category must be checked once")
        if len(self.byte_pairs) != len(set(self.byte_pairs)):
            raise ValueError("duplicate byte-comparison addresses")
        return self

    @property
    def verdict(self) -> AuditVerdict:
        verdicts = {reading.verdict for reading in self.checks}
        if AuditVerdict.REFUTED in verdicts:
            return AuditVerdict.REFUTED
        if AuditVerdict.UNVERIFIABLE in verdicts:
            return AuditVerdict.UNVERIFIABLE
        return AuditVerdict.HOLDS


class AuditOverclaimObservation(CamelCaseModel):
    """An observed claim judgment awaiting mandate completion and publication."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    judgment: AuditOverclaimJudgment
    graded_sha: str
    head_sha: str
    record_ref: str
    check: str
