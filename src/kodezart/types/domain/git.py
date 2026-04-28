"""Git ref value objects."""

from pydantic import BaseModel, ConfigDict, Field


class LsRemoteEntry(BaseModel):
    """A single ref from ``git ls-remote`` output."""

    model_config = ConfigDict(frozen=True)

    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    ref: str = Field(pattern=r"^refs/")
