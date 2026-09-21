"""Source-owned protection references and observed assertion deviation claims."""

from pathlib import PurePosixPath
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel


class ProtectedTestRef(CamelCaseModel):
    """A test explicitly named by its owning ruling, never inferred from prose."""

    model_config = ConfigDict(frozen=True)

    source_ref: str = Field(min_length=1, pattern=r"\S")
    path: str = Field(min_length=1)
    qualified_name: str = Field(min_length=1)

    @model_validator(mode="after")
    def canonical_reference(self) -> Self:
        path = PurePosixPath(self.path)
        if (
            path.is_absolute()
            or str(path) != self.path
            or ".." in path.parts
            or "\x00" in self.path
            or path.suffix != ".py"
            or not all(part.isidentifier() for part in self.qualified_name.split("."))
        ):
            raise ValueError("a protected Python test needs a canonical path and name")
        return self


class AssertionSource(CamelCaseModel):
    """One observed assertion condition, retaining its location and syntax."""

    model_config = ConfigDict(frozen=True)

    line: int = Field(ge=1)
    expression: str = Field(min_length=1)
    structural_form: str = Field(min_length=1)


class AssertionDeviationClaim(CamelCaseModel):
    """A protected assertion changed; this is evidence, not a lane verdict."""

    model_config = ConfigDict(frozen=True)

    protected_test: ProtectedTestRef
    graded_sha: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    graded_blob_sha: str = Field(min_length=1)
    head_blob_sha: str = Field(min_length=1)
    before: tuple[AssertionSource, ...]
    after: tuple[AssertionSource, ...]


class GitSourceBlob(CamelCaseModel):
    """Exact bytes of a regular file from one immutable commit object."""

    model_config = ConfigDict(frozen=True)

    commit_sha: str = Field(min_length=1)
    path: str = Field(min_length=1)
    blob_sha: str = Field(min_length=1)
    content: bytes
