"""The explicit grading facts stored inside a criterion's Evidence field."""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel

#: A complete commit identity, in either of the two hash lengths git writes.
#: The Evidence record is the one place a graded sha is read, so this is the
#: one expression for it; nothing outside the record re-states the shape.
GRADED_SHA_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"


class CriterionEvidence(CamelCaseModel):
    """A complete commit identity and the test or recorded observation it names."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    graded_sha: str = Field(pattern=GRADED_SHA_PATTERN)
    test: str = Field(min_length=1, pattern=r"\S")
