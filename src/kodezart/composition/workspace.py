"""Construction of the git-backed workspace stack.

``build_git_stack``'s body moved verbatim from the composition root, which
imports and wires rather than defines. ``GitStack`` did not: the root held
these as separate locals and the type is new here, so this module is an
extraction plus one addition rather than a pure move.
"""

from dataclasses import dataclass

from kodezart.adapters.git_artifact_persister import GitArtifactPersister
from kodezart.adapters.git_branch_merger import GitBranchMerger
from kodezart.adapters.git_change_persister import GitChangePersister
from kodezart.adapters.git_ref_publisher import GitRefPublisher
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
    PromptSetProvider,
    RefPublisher,
    RepoCache,
    WorkspaceProvider,
)


@dataclass(frozen=True)
class GitStack:
    """The collaborators every git-touching consumer is built from.

    One bundle rather than one return per member because they are not
    independent: each is built from the one above it, they all share a
    single ``GitService``, and a caller that assembled most of them from
    this module and the rest by hand would be wiring two different gits.
    """

    git: GitService
    cache: RepoCache
    workspace: WorkspaceProvider
    persister: ChangePersister
    merger: BranchMerger
    artifact_persister: ArtifactPersister
    ref_publisher: RefPublisher


def build_git_stack(
    *,
    config: AppConfig,
    prompts: PromptSetProvider,
    gate: OutboundContentGate,
) -> GitStack:
    """Git, its clone cache, and the writers that work through them."""
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
    ref_publisher = GitRefPublisher(git=git, workspace=workspace)
    return GitStack(
        git=git,
        cache=cache,
        workspace=workspace,
        persister=persister,
        merger=merger,
        artifact_persister=artifact_persister,
        ref_publisher=ref_publisher,
    )
