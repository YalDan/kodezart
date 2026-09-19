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

    model_config = ConfigDict(frozen=True, extra="forbid")
    phase: Literal["new"] = "new"

class _NativeWorkspacePhase(CamelCaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace: WorkspaceSnapshot
    start: NativeWriterStart
    authority: NativeAuthoritySnapshot
    cache_key: str | None
    run_identity: RunIdentity | None

class PreparedNativeExecution(_NativeWorkspacePhase):
    """A validated acquired workspace and original live authority."""

    phase: Literal["prepared"] = "prepared"

    @model_validator(mode="after")
    def _original_head(self) -> Self:
        if self.workspace.identity.head_sha != self.start.head_sha:
            raise ValueError("prepared workspace differs from the actual starting HEAD")
        return self

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

class WrittenNativeExecution(_WrittenNativeExecution):
    """The actual completed writer output and its uncommitted workspace."""

    phase: Literal["written"] = "written"

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

class ReconciledNativeExecution(_ReconciledNativeExecution):
    """All claimed departures have actual completed amendment receipts."""

    phase: Literal["reconciled"] = "reconciled"

    @model_validator(mode="after")
    def _no_upheld_departure(self) -> Self:
        if self.report.upheld:
            raise ValueError(
                "reconciled persistence cannot contain an UPHELD departure"
            )
        return self

class RefusedNativeExecution(_ReconciledNativeExecution):
    """A completed actual UPHELD report; no harness persistence is authorized."""

    phase: Literal["refused"] = "refused"

    @model_validator(mode="after")
    def _has_upheld_departure(self) -> Self:
        if not self.report.upheld:
            raise ValueError("a native refusal requires an actual UPHELD departure")
        return self

class PersistedNativeExecution(_ReconciledNativeExecution):
    """The persister returned its actual commit/publication receipt."""

    phase: Literal["persisted"] = "persisted"
    receipt: PersistResult

    @model_validator(mode="after")
    def _actual_receipt(self) -> Self:
        if (
            self.report.upheld
            or self.receipt.commit_sha != self.workspace.identity.head_sha
            or self.receipt.branch != self.workspace.identity.branch
        ):
            raise ValueError(
                "native persistence receipt differs from its actual workspace"
            )
        return self

class UnchangedNativeExecution(_ReconciledNativeExecution):
    """Persistence completed and reported no code changes to commit."""

    phase: Literal["unchanged"] = "unchanged"

    @model_validator(mode="after")
    def _no_refused_departure(self) -> Self:
        if self.report.upheld:
            raise ValueError("an UPHELD departure cannot complete persistence")
        return self

type NativeExecutionPhase = Annotated[
    NewNativeExecution
    | PreparedNativeExecution
    | WrittenNativeExecution
    | ReconciledNativeExecution
    | RefusedNativeExecution
    | PersistedNativeExecution
    | UnchangedNativeExecution,
    Field(discriminator="phase"),
]

type ActiveNativeExecution = (
    PreparedNativeExecution
    | WrittenNativeExecution
    | ReconciledNativeExecution
    | RefusedNativeExecution
    | PersistedNativeExecution
    | UnchangedNativeExecution
)
