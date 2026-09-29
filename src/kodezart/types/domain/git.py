"""Git ref value objects."""

from pydantic import BaseModel, ConfigDict, Field

#: A full commit name, SHA-1 or SHA-256.
_COMMIT_SHA = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"


class LsRemoteEntry(BaseModel):
    """A single ref from ``git ls-remote`` output."""

    model_config = ConfigDict(frozen=True)

    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    ref: str = Field(pattern=r"^refs/")


class TrackedHead(BaseModel):
    """A local branch head beside the remote-tracking ref of the same name."""

    model_config = ConfigDict(frozen=True)

    ref: str = Field(pattern=r"^refs/heads/")
    sha: str = Field(pattern=_COMMIT_SHA)
    remote_sha: str = Field(pattern=_COMMIT_SHA)
    checked_out: bool
