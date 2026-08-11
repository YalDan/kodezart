"""Tests for GitRefPublisher — KOD-40's route from a commit to a forge ref."""

import pytest

from kodezart.adapters.git_ref_publisher import GitRefPublisher
from kodezart.core.protocols import RefPublisher
from kodezart.domain.agent import best_iteration_ref
from tests.fakes import FakeGitService, FakeWorkspaceProvider

BEST_SHA = "b" * 40


def _publisher(
    *,
    git: FakeGitService | None = None,
    workspace: FakeWorkspaceProvider | None = None,
) -> GitRefPublisher:
    return GitRefPublisher(
        git=git or FakeGitService(),
        workspace=workspace or FakeWorkspaceProvider(),
    )


def test_the_adapter_satisfies_the_port() -> None:
    """The engine depends on the protocol, never on this class."""
    assert isinstance(_publisher(), RefPublisher)


async def test_publish_checks_out_the_commit_and_pushes_it_as_the_ref() -> None:
    """The workspace is acquired AT the commit; the ref comes from the push."""
    git = FakeGitService()
    workspace = FakeWorkspaceProvider()
    publisher = _publisher(git=git, workspace=workspace)

    await publisher.publish(
        repo_path=None,
        repo_url="https://github.com/owner/repo",
        commit_sha=BEST_SHA,
        ref="kodezart/x-12345678-best",
        cache_key="job-1",
    )

    assert ("acquire", "https://github.com/owner/repo", BEST_SHA) in workspace.calls
    assert ("push", "/tmp/fake-workspace", "kodezart/x-12345678-best") in git.calls


async def test_publish_names_no_local_branch() -> None:
    """A named local branch would outlive the worktree and shadow a re-publish.

    The provider is asked for a detached workspace, so nothing but the
    push refspec ever writes the ref.
    """
    workspace = FakeWorkspaceProvider()
    await _publisher(workspace=workspace).publish(
        repo_path="/tmp/repo",
        repo_url=None,
        commit_sha=BEST_SHA,
        ref=best_iteration_ref("kodezart/x-12345678"),
    )

    acquires = [call for call in workspace.calls if call[0] == "acquire"]
    assert [call[2] for call in acquires] == [BEST_SHA]


async def test_publish_releases_the_workspace_even_when_the_push_fails() -> None:
    """A rejected push must not strand a worktree in the shared repository."""
    push_error = RuntimeError("push rejected")
    git = FakeGitService(push_error=push_error)
    workspace = FakeWorkspaceProvider()

    with pytest.raises(RuntimeError, match="push rejected"):
        await _publisher(git=git, workspace=workspace).publish(
            repo_path="/tmp/repo",
            repo_url=None,
            commit_sha=BEST_SHA,
            ref="kodezart/x-12345678-best",
        )

    assert ("release", "/tmp/fake-workspace") in workspace.calls


def test_the_ref_name_is_derived_from_the_feature_branch_and_is_stable() -> None:
    """Two calls in one run name one ref — the run's identity is the suffix."""
    feature = "kodezart/add-a-thing-12345678"
    assert best_iteration_ref(feature) == f"{feature}-best"
    assert best_iteration_ref(feature) == best_iteration_ref(feature)
