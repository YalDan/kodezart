"""The explicit grading facts stored inside a criterion's Evidence field."""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class CriterionEvidence(CamelCaseModel):
    """A complete commit identity and the test or recorded observation it names."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    graded_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    test: str = Field(min_length=1, pattern=r"\S")
