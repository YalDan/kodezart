"""Coherent, portable evidence returned by one check watch."""

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel


class ObservedChecks(CamelCaseModel):
    """Facts from one complete terminal check set, never a new forge query."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["completed"] = "completed"
    commit_sha: str = Field(min_length=1)
    checks_passed: bool
    check_names: frozenset[str] = Field(min_length=1)
    failed_check_names: frozenset[str]
    summary: str

    @model_validator(mode="after")
    def coherent_verdict(self) -> Self:
        """The verdict and failure subset describe the same complete roster."""
        if not self.commit_sha.strip() or any(
            not name.strip() for name in self.check_names
        ):
            raise ValueError("completed checks require a commit and named checks")
        if not self.failed_check_names <= self.check_names:
            raise ValueError("failed checks must belong to the observed roster")
        if self.checks_passed == bool(self.failed_check_names):
            raise ValueError("the verdict must agree with the failing check subset")
        return self


class AbsentChecks(CamelCaseModel):
    """No run appeared within the watch's configured grace window."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["absent"] = "absent"
    summary: str


class IncompleteChecks(CamelCaseModel):
    """The poll bound expired without a complete terminal check set.

    Retain the last partial observation, including every known commit,
    without presenting any of those facts as a completed roster or verdict.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["incomplete"] = "incomplete"
    commit_shas: frozenset[str]
    check_names: frozenset[str]
    failed_check_names: frozenset[str]
    observed_count: int = Field(ge=0)
    expected_count: int = Field(ge=0)
    summary: str


type CIWatchResult = Annotated[
    ObservedChecks | AbsentChecks | IncompleteChecks, Field(discriminator="kind")
]
