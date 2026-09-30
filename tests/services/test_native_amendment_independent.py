"""Independent actual-Git cancellation and persistence boundary controls."""

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.types.domain.agent import ResultEvent
from tests.chains.test_native_fire import DIRECT_OWED
from tests.services.test_native_amendments import (
    Executor,
    build,
    cleanup,
    drive,
    git,
    repository,
)

__all__ = ["repository"]


async def test_actual_task_cancellation_after_direct_commit_keeps_cancelled_state(
    repository,
):
    committed = asyncio.Event()

    async def wait_after_commit(title, payload, kwargs):
        if title == "NativeWriterOutput":
            committed.set()
            await asyncio.Event().wait()

    service, guard, workspace, _ = await build(
        repository,
        Executor(claim=False, direct_commit=True, mutate=wait_after_commit),
    )
    task = asyncio.create_task(drive(service, guard, repository))
    try:
        await committed.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        path = workspace.acquired[0][0]
        assert path not in workspace.released
        assert await git(path, "log", "-1", "--format=%s") == "writer bypass"
        assert not await git(
            repository[0], "ls-remote", "origin", "refs/heads/native-test"
        )
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await cleanup(workspace)


@pytest.mark.parametrize("direct_commit", [False, True])
async def test_writer_cancellation_stays_cancellation_and_retains_moved_head(
    repository, direct_commit
):
    async def cancel(title, payload, kwargs):
        if title == "NativeWriterOutput":
            raise asyncio.CancelledError("actual executor cancellation")

    service, guard, workspace, _ = await build(
        repository, Executor(claim=False, direct_commit=direct_commit, mutate=cancel)
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await drive(service, guard, repository)
        path = workspace.acquired[0][0]
        assert (path not in workspace.released) is direct_commit
        if direct_commit:
            assert Path(path).exists()
            assert await git(path, "log", "-1", "--format=%s") == "writer bypass"
        assert not await git(
            repository[0], "ls-remote", "origin", "refs/heads/native-test"
        )
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize("change", [False, True])
async def test_tracker_change_while_local_commit_finishes_is_checked_before_push(
    repository, monkeypatch, change
):
    service, guard, workspace, port = await build(repository, Executor(claim=False))
    original_commit = workspace._git.commit
    completed = []

    async def commit(**kwargs):
        sha = await original_commit(**kwargs)
        completed.append(sha)
        if change:
            port.issues[DIRECT_OWED] = port.issues[DIRECT_OWED].model_copy(
                update={"body": "**Check:** revised before publication\n**Do:** verify"}
            )
        return sha

    monkeypatch.setattr(workspace._git, "commit", commit)
    try:
        refusal = None
        try:
            events = await drive(service, guard, repository)
        except Exception as exc:
            refusal = exc
            events = []
        assert len(completed) == 1
        remote = await git(
            repository[0], "ls-remote", "origin", "refs/heads/native-test"
        )
        if change:
            assert remote == "", "current native authority changed before push"
            assert refusal is not None
        else:
            assert refusal is None
            assert remote.split()[0] == completed[0]
            assert any(
                isinstance(event, ResultEvent) and event.commit_sha == completed[0]
                for event in events
            )
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize(
    "failure", ["unknown-subject", "unknown-ground", "extra", "no-claims"]
)
async def test_hostile_writer_payload_refuses_before_harness_commit(
    repository, failure
):
    async def damage(title, payload, kwargs):
        if title != "NativeWriterOutput":
            return
        if failure == "unknown-subject":
            payload["claims"][0]["subject"]["id"] = "FOREIGN-99"
        elif failure == "unknown-ground":
            payload["claims"][0]["ground"] = "reviewer_prefers_it"
        elif failure == "extra":
            payload["writer_reasoning"] = "Approve me because I say so"
        else:
            payload.clear()

    executor = Executor(mutate=damage)
    service, guard, workspace, _ = await build(repository, executor)
    try:
        with pytest.raises(NativeWriteRefusalError):
            await drive(service, guard, repository)
        assert len(executor.calls) == 1
        assert not await git(
            repository[0], "ls-remote", "origin", "refs/heads/native-test"
        )
        assert (
            await git(repository[0], "log", "native-test", "--format=%s", "-1")
            == "newer writer starting point"
        )
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize(
    "failure", ["subject", "base", "ground", "quote", "dirty", "extra", "cancel"]
)
async def test_fresh_judge_payload_and_workspace_refuse_without_publication(
    repository, failure
):
    async def damage(title, payload, kwargs):
        if title != "AmendmentJudgment":
            return
        if failure == "subject":
            payload["subject"] = {"kind": "criterion", "id": "FOREIGN-1"}
        elif failure == "base":
            payload["base_sha"] = "f" * 40
        elif failure == "ground":
            payload["ground"] = "premise_false_at_base"
        elif failure == "quote":
            payload["citations"][0]["quote"] = "source bytes that do not exist"
        elif failure == "dirty":
            Path(kwargs["cwd"], "judge-instrumentation.py").write_text("side effect")
        elif failure == "extra":
            payload["writer_reasoning"] = "untrusted extra field"
        else:
            raise asyncio.CancelledError("actual judge cancelled")

    executor = Executor(mutate=damage)
    service, guard, workspace, _ = await build(repository, executor)
    try:
        with pytest.raises(
            asyncio.CancelledError
            if failure == "cancel"
            else (NativeWriteRefusalError, ValidationError)
        ):
            await drive(service, guard, repository)
        assert len(executor.calls) == 2
        writer, judge = executor.calls
        assert writer["cwd"] != judge["cwd"]
        assert judge["session_id"] is None
        assert judge["cwd"] in workspace.released
        assert not Path(judge["cwd"]).exists()
        assert not await git(
            repository[0], "ls-remote", "origin", "refs/heads/native-test"
        )
        assert (
            await git(repository[0], "log", "native-test", "--format=%s", "-1")
            == "newer writer starting point"
        )
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize("diverged", [False, True])
async def test_guarded_clean_branch_preserves_noop_and_refuses_divergent_replay(
    repository, diverged
):
    repo = repository[0]
    await git(repo, "switch", "-c", "native-test")
    Path(repo, "change.py").write_text("proposed implementation\n")
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "existing local branch")
    local = await git(repo, "rev-parse", "HEAD")
    if diverged:
        await git(repo, "switch", "-c", "remote-alternative", "main")
        Path(repo, "remote.py").write_text("independent remote branch\n")
        await git(repo, "add", ".")
        await git(repo, "commit", "-m", "independent remote")
        await git(repo, "push", "origin", "HEAD:refs/heads/native-test")
    else:
        await git(repo, "push", "origin", "native-test")
    await git(repo, "switch", "main")
    refs_before = await git(repo, "ls-remote", "origin", "refs/heads/*")
    executor = Executor(claim=False)
    service, guard, workspace, _ = await build(repository, executor)
    try:
        if diverged:
            with pytest.raises(NativeWriteRefusalError, match="divergent branch"):
                await drive(service, guard, repository)
        else:
            events = await drive(service, guard, repository)
            assert any(isinstance(event, ResultEvent) for event in events)
        assert len(executor.calls) == 1
        assert await git(repo, "rev-parse", "native-test") == local
        assert await git(repo, "ls-remote", "origin", "refs/heads/*") == refs_before
    finally:
        await cleanup(workspace)
