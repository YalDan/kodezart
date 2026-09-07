"""Cold replay recognizes real artifact cleanup without accepting changed code."""

import subprocess

import pytest

from kodezart.adapters.git_artifact_persister import GitArtifactPersister
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.domain.errors import DeliveryContextError
from kodezart.types.domain.consolidation import ChangesetDigest
from tests.chains.test_delivery_replay import existing_fixture
from tests.chains.test_delivery_runtime import (
    BASE,
    HEAD,
    REPOSITORY,
    SHA,
    context,
    deliver,
    setup,
)
from tests.fakes import FakeGitService, FakeWorkspaceProvider


async def test_real_cleanup_then_cold_replay_retains_original_fire_identity(tmp_path):
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    subprocess.run(["git", "init", str(checkout)], check=True, capture_output=True)

    def git(*arguments):
        return subprocess.run(
            ["git", *arguments],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (checkout / "README.md").write_text("Initial code\n")
    git("add", ".")
    git("commit", "-m", "initial")
    git("branch", BASE)
    git("checkout", "-b", HEAD)
    artifacts = checkout / ".kodezart"
    artifacts.mkdir()
    (artifacts / "ticket.json").write_text("{}")
    git("add", ".")
    git("commit", "-m", "workflow metadata")
    fire_sha = git("rev-parse", "HEAD")
    git("remote", "add", "upstream", str(remote))
    git("push", "upstream", HEAD, BASE)
    service = SubprocessGitService(remote="upstream")
    cleaner = GitArtifactPersister(
        git=service,
        workspace=FakeWorkspaceProvider(workspace_path=str(checkout)),
        committer_name="Test",
        committer_email="test@example.invalid",
    )
    first_fixture = setup(git=service, cleaner=cleaner)
    facts = context(
        execution=context().execution.model_copy(update={"repo_path": str(checkout)})
    )
    first = await deliver(first_fixture.coordinator, facts=facts, sha=fire_sha)
    clean_sha = git("rev-parse", "HEAD")
    assert clean_sha != fire_sha and not artifacts.exists()
    assert (
        await service.remote_branch_sha(
            cwd=str(checkout), remote="upstream", branch=HEAD
        )
        == clean_sha
    )
    first_fixture.query.open_prs[(REPOSITORY, HEAD)] = (first.pr.url, first.pr.number)
    # A new coordinator has no remembered cleanup SHA; the native Git proof
    # and the existing PR are sufficient for the same original fire handoff.
    replay = setup(
        git=service,
        cleaner=cleaner,
        forge=first_fixture.forge,
        query=first_fixture.query,
        editor=first_fixture.editor,
    )
    assert await deliver(replay.coordinator, facts=facts, sha=fire_sha) == first
    assert git("rev-parse", "HEAD") == clean_sha
    assert len(replay.forge.calls) == 1
    assert not [call for call in replay.editor.calls if call["method"] == "edit_pr"]
    assert replay.runner.calls[0]["run_identity"] == facts.execution.run_identity

    (checkout / "README.md").write_text("Different code\n")
    git("add", ".")
    git("commit", "-m", "later work")
    git("push", "upstream", HEAD)
    with pytest.raises(DeliveryContextError, match="remote head differs"):
        await deliver(replay.coordinator, facts=facts, sha=fire_sha)
    assert len(replay.runner.calls) == len(replay.forge.calls) == 1


@pytest.mark.parametrize(
    "ancestor,paths,existing",
    [
        (False, [".kodezart/ticket.json"], True),
        (True, ["src/changed.py"], True),
        (True, [".kodezart-backup/file"], True),
        (True, [], True),
        (True, [".kodezart/ticket.json"], False),
    ],
)
async def test_replay_does_not_relabel_other_remote_movements_as_cleanup(
    ancestor, paths, existing
):
    observed = "c" * 40
    git = FakeGitService(
        remote_branch_shas={HEAD: observed, BASE: "b" * 40},
        ancestor_pairs={(SHA, observed)} if ancestor else set(),
        diff_digests={
            (SHA, observed): ChangesetDigest(
                file_paths=paths, commit_subjects=["later"], commit_count=1
            )
        },
    )
    prior = existing_fixture()
    fixture = setup(
        git=git,
        query=prior.query if existing else None,
        editor=prior.editor if existing else None,
    )
    with pytest.raises(DeliveryContextError, match="remote head differs"):
        await deliver(fixture.coordinator)
    assert fixture.runner.calls == fixture.forge.calls == fixture.monitor.calls == []
