"""The explicit grading facts stored inside a criterion's Evidence field."""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel

#: A complete commit identity, in either of the two hash lengths git writes.
#: The Evidence field and the authoring-time fillability check answer the
#: same question about a sha, so they read the same expression for it.
GRADED_SHA_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"


class CriterionEvidence(CamelCaseModel):
    """A complete commit identity and the test or recorded observation it names."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    graded_sha: str = Field(pattern=GRADED_SHA_PATTERN)
    test: str = Field(min_length=1, pattern=r"\S")
