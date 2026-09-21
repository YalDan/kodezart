"""Actual native worktree identities retained across an interrupted writer."""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.amendment import CommitSha, Nonblank


class GitWorktreeIdentity(CamelCaseModel):
    """Read-only Git and filesystem identity, with no captured file contents."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: Nonblank
    root_device: int
    root_inode: int
    common_dir: Nonblank
    common_device: int
    common_inode: int
    git_dir: Nonblank
    git_device: int
    git_inode: int
    branch: Nonblank
    head_sha: CommitSha
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceSnapshot(CamelCaseModel):
    """An actual acquisition and the native parent job that can resume it."""

    workspace_path: Nonblank
    workspace_id: Nonblank
    repository_path: Nonblank
    repository_device: int
    repository_inode: int
    holder: Nonblank
    identity: GitWorktreeIdentity
