"""Commit identity and structured verdict from a completed check watch."""

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel


class ObservedChecks(CamelCaseModel):
    """Facts from one complete terminal check set, never a new forge query."""

    model_config = ConfigDict(frozen=True)

    commit_sha: str = Field(min_length=1)
    checks_passed: bool
    failed_names: frozenset[str]

    @model_validator(mode="after")
    def _coherent_verdict(self) -> "ObservedChecks":
        if self.checks_passed == bool(self.failed_names):
            raise ValueError("terminal check verdict disagrees with failing names")
        if any(not name for name in self.failed_names):
            raise ValueError("a failing check must have a name")
        return self
