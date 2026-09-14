"""Serializable native writer phases; runtime collaborators stay outside state."""

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel

from kodezart.types.domain.agent import ResultEvent, Ruling

from kodezart.types.domain.amendment import (
    AmendmentReport,
    CommitSha,
    NativeWriterOutput,
    NativeWriterStart,
    Nonblank,
)

from kodezart.types.domain.audit import TrackerArtifact

from kodezart.types.domain.criteria import TrackerCriterionSet

from kodezart.types.domain.fire_spec import TrackerSpec

from kodezart.types.domain.persist import PersistResult

from kodezart.types.domain.run_records import RunIdentity

from kodezart.types.domain.tracker import TrackerComment, TrackerIssue

from kodezart.types.domain.workspace import WorkspaceSnapshot

class NativeAuthoritySnapshot(CamelCaseModel):
    """The original source facts and only the writer's verified source updates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec: TrackerSpec
    criteria: TrackerCriterionSet
    criterion_issues: tuple[TrackerIssue, ...]
    ruling_records: tuple[tuple[TrackerComment, Ruling], ...]
    archives: tuple[TrackerArtifact, ...]
    base_ref: Nonblank
    base_sha: CommitSha
    holder: Nonblank

class NewNativeExecution(CamelCaseModel):
    """No workspace or writer effect has completed."""

    phase: Literal["new"] = "new"

class _NativeWorkspacePhase(CamelCaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace: WorkspaceSnapshot
    start: NativeWriterStart
    authority: NativeAuthoritySnapshot
    cache_key: str | None
    run_identity: RunIdentity | None

class _WrittenNativeExecution(_NativeWorkspacePhase):
    result: ResultEvent
    output: NativeWriterOutput

    @model_validator(mode="after")
    def _actual_output(self) -> Self:
        if (
            self.result.is_error
            or self.result.structured_output is None
            or NativeWriterOutput.model_validate(self.result.structured_output)
            != self.output
        ):
            raise ValueError(
                "native output differs from the actual successful writer result"
            )
        return self

class _ReconciledNativeExecution(_WrittenNativeExecution):
    report: AmendmentReport

    @model_validator(mode="after")
    def _same_claims(self) -> Self:
        if (
            tuple(verdict.claim for verdict in self.report.verdicts)
            != self.output.claims
        ):
            raise ValueError(
                "reconciliation receipts do not cover the exact writer claims"
            )
        return self
