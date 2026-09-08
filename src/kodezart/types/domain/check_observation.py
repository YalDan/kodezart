"""Commit identity and structured verdict from a completed check watch."""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class ObservedChecks(CamelCaseModel):
    """Facts from one complete terminal check set, never a new forge query."""

    model_config = ConfigDict(frozen=True)

    commit_sha: str = Field(min_length=1)
    checks_passed: bool
    check_names: frozenset[str]
