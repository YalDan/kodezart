"""Construction of the git-backed workspace stack.

``build_git_stack``'s body moved verbatim from the composition root, which
imports and wires rather than defines. ``GitStack`` did not: the root held
these six as six separate locals and the type is new here, so this module
is an extraction plus one addition rather than a pure move.
"""

from dataclasses import dataclass

from kodezart.adapters.git_artifact_persister import GitArtifactPersister
from kodezart.adapters.git_branch_merger import GitBranchMerger
from kodezart.adapters.git_change_persister import GitChangePersister
from kodezart.adapters.git_worktree_provider import GitWorktreeProvider
from kodezart.adapters.github_token_auth import GitHubTokenAuth
from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.core.config import AppConfig
from kodezart.core.protocols import (
    ArtifactPersister,
    BranchMerger,
    ChangePersister,
    GitService,
    OutboundContentGate,
    PromptProvider,
    RepoCache,
    WorkspaceProvider,
)


@dataclass(frozen=True)
class GitStack:
    """The six collaborators every git-touching consumer is built from.

    One bundle rather than six returns because they are not independent:
    each is built from the one above it, they all share a single
    ``GitService``, and a caller that assembled five of them from this
    module and the sixth by hand would be wiring two different gits.
    """

    git: GitService
    cache: RepoCache
    workspace: WorkspaceProvider
    persister: ChangePersister
    merger: BranchMerger
    artifact_persister: ArtifactPersister


def build_git_stack(
    *,
    config: AppConfig,
    prompts: PromptProvider,
    gate: OutboundContentGate,
) -> GitStack:
    """Git, its clone cache, and the four writers that work through them."""
    auth = GitHubTokenAuth(token=config.github_token) if config.github_token else None
    git = SubprocessGitService(remote=config.git_remote, auth=auth)
    cache = LocalBareRepoCache(git=git, base_dir=config.clone_cache_dir)
    workspace = GitWorktreeProvider(
        git=git,
        cache=cache,
        committer_name=config.git_committer_name,
        committer_email=config.git_committer_email,
    )
    persister = GitChangePersister(
        git=git,
        committer_name=config.git_committer_name,
        committer_email=config.git_committer_email,
        remote=config.git_remote,
        prompts=prompts,
        gate=gate,
    )
    merger = GitBranchMerger(git=git, workspace=workspace, remote=config.git_remote)
    artifact_persister = GitArtifactPersister(
        git=git,
        workspace=workspace,
        committer_name=config.git_committer_name,
        committer_email=config.git_committer_email,
    )
    return GitStack(
        git=git,
        cache=cache,
        workspace=workspace,
        persister=persister,
        merger=merger,
        artifact_persister=artifact_persister,
    )
