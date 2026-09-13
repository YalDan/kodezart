"""Recorded grading resolves against real remote Git, including rewritten history."""

import asyncio
import os
import subprocess
import sys

import pytest

from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.domain.errors import AuditEvidenceReadError
from kodezart.types.domain.audit import AuditVerdict
from tests.tracker import test_audit_evidence as fixtures
from tests.tracker.test_audit_claim import CHILD, REQUEST

setup = fixtures.setup
server = fixtures.server
claim_setup = fixtures.claim_setup


def command(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    remote, author, observer = (
        tmp_path / name for name in ("remote", "author", "cache")
    )
    remote.mkdir()
    command(remote, "init", "--bare", "-q")
    author.mkdir()
    command(author, "init", "-q", "-b", "ordinary-name")
    command(author, "config", "user.name", "Fixture")
    command(author, "config", "user.email", "fixture@example.invalid")
    (author / "check.txt").write_text("prior committed contents\n")
    command(author, "add", "--all")
    command(author, "commit", "-qm", "prior")
    prior = command(author, "rev-parse", "HEAD")
    command(author, "remote", "add", "configured-remote", str(remote))
    command(author, "push", "-q", "configured-remote", "ordinary-name")
    command(
        tmp_path,
        "clone",
        "--bare",
        "-q",
        "-o",
        "configured-remote",
        str(remote),
        str(observer),
    )
    (author / "check.txt").write_text("current committed contents\n")
    command(author, "add", "--all")
    command(author, "commit", "-qm", "current")
    head = command(author, "rev-parse", "HEAD")
    command(author, "push", "-q", "configured-remote", "ordinary-name")
    assert command(observer, "rev-parse", "ordinary-name") == prior
    return remote, author, observer, prior, head


@pytest.mark.parametrize(
    "case", ["lapse", "current", "review-rewrite", "off-branch", "missing"]
)
async def test_actual_git_evidence_is_bound_to_the_remote_not_a_stale_local_branch(
    setup, tracker, tracker_writes, repository, monkeypatch, tmp_path, case
):
    build, runner, git, _, cache, workspace, *_ = setup
    remote, author, observer, prior, head = repository
    graded = head if case == "current" else prior
    if case in {"review-rewrite", "off-branch"}:
        command(author, "checkout", "--orphan", "rewritten")
        (author / "check.txt").write_text("replacement history\n")
        command(author, "add", "--all")
        command(author, "commit", "-qm", "replacement")
        head = command(author, "rev-parse", "HEAD")
        command(
            author, "push", "-q", "--force", "configured-remote", "HEAD:ordinary-name"
        )
    if case == "missing":
        graded = "e" * 40
    await tracker.update_issue(issue_key=CHILD, body=fixtures.body(graded))
    if case == "review-rewrite":
        await tracker.restore_workflow_state(issue_key=CHILD, state_name="In Review")
    native = SubprocessGitService(remote="configured-remote")
    for method in (
        "fetch",
        "remote_branch_sha",
        "is_ancestor",
        "current_sha",
        "has_changes",
    ):
        monkeypatch.setattr(git, method, getattr(native, method))
    cache._repo_path = str(observer)
    verification = tmp_path / "verification"
    acquired = []
    released = []

    async def acquire(**kwargs):
        assert kwargs["create_branch"] is False
        assert kwargs["ref"] == head
        acquired.append(head)
        await native.create_worktree(
            str(observer), head, str(verification), create_branch=False
        )
        return str(verification)

    async def release(path):
        await native.remove_worktree(str(observer), path)
        released.append(path)

    async def during():
        assert await native.current_sha(str(verification)) == head
        assert not await native.has_changes(str(verification))
        assert (verification / "check.txt").read_text() == (
            author / "check.txt"
        ).read_text()

    monkeypatch.setattr(workspace, "acquire", acquire)
    monkeypatch.setattr(workspace, "release", release)
    runner.during = during
    request = REQUEST.model_copy(update={"repo_url": remote.as_uri()})
    writes = tracker_writes()
    verifier = build(source=SubprocessGitSourceReader())
    if case in {"off-branch", "missing"}:
        with pytest.raises(AuditEvidenceReadError) as raised:
            await verifier.observe(request)
        assert raised.value.criterion_key == CHILD
        assert acquired == released == []
    else:
        observed = await verifier.observe(request)
        assert observed.recorded_evidence.graded_sha == graded
        assert observed.head_sha == head
        assert observed.verdict is (
            AuditVerdict.UNVERIFIABLE if case == "lapse" else AuditVerdict.HOLDS
        )
        assert observed.is_lapse is (case == "lapse")
        if case == "lapse":
            assert acquired == released == []
        else:
            assert acquired == [head] and released == [str(verification)]
            assert runner.arguments["session_id"] is None
            assert fixtures.TEST not in runner.arguments["prompt"]
    assert command(observer, "rev-parse", "ordinary-name") == prior
    assert tracker_writes() == writes
    assert not verification.exists()


@pytest.mark.parametrize("phase", ["head-fetch", "cache-fetch", "cache-clone"])
async def test_actual_repository_child_is_reaped_before_cancellation_returns(
    setup, monkeypatch, tmp_path, phase
):
    build, _, git, *_ = setup
    native = SubprocessGitService(remote="configured-remote")
    started, ready, finish, completed = (
        tmp_path / name for name in ("pid", "ready", "finish", "completed")
    )
    original = git.fetch

    async def blocked(*args):
        program = (
            "from pathlib import Path\nimport os, time\n"
            f"Path({str(started)!r}).write_text(str(os.getpid()))\n"
            f"Path({str(ready)!r}).touch()\n"
            f"while not Path({str(finish)!r}).exists(): time.sleep(0.01)\n"
            f"Path({str(completed)!r}).touch()\n"
        )
        await native._run_output([sys.executable, "-c", program], cwd=str(tmp_path))
        if phase == "head-fetch":
            await original(*args)

    if phase == "head-fetch":
        monkeypatch.setattr(git, "fetch", blocked)
        verifier = build()
    else:
        exists = phase == "cache-fetch"
        monkeypatch.setattr(native, "is_repo", lambda _path: exists)
        monkeypatch.setattr(native, "fetch" if exists else "clone_bare", blocked)
        verifier = build(
            cache=LocalBareRepoCache(git=native, base_dir=str(tmp_path / "cache"))
        )
    task = asyncio.create_task(verifier.observe(REQUEST))
    try:
        async with asyncio.timeout(5):
            while not ready.exists():
                await asyncio.sleep(0.01)
        pid = int(started.read_text())
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and not completed.exists()
        finish.touch()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert completed.exists()
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        finish.touch()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        async with asyncio.timeout(5):
            while started.exists() and not completed.exists():
                await asyncio.sleep(0.01)
