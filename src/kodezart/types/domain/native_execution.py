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

    spec: TrackerSpec
    criteria: TrackerCriterionSet
    base_ref: Nonblank
    holder: Nonblank

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
