"""Native delivery reads settle before cancellation can leave their caller."""

import asyncio
import os
import subprocess
import sys

import pytest

from kodezart.adapters.local_bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.domain.git_url import cache_dir_for_repo
from tests.chains.test_delivery_replay import existing_fixture
from tests.chains.test_delivery_runtime import BASE, HEAD, context, deliver, setup


def command(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    remote.mkdir()
    checkout.mkdir()
    command(remote, "init", "--bare", "-q")
    command(checkout, "init", "-q", "-b", BASE)
    command(checkout, "config", "user.name", "Fixture")
    command(checkout, "config", "user.email", "fixture@example.invalid")
    (checkout / "README.md").write_text("Committed code\n")
    command(checkout, "add", ".")
    command(checkout, "commit", "-qm", "initial")
    command(checkout, "checkout", "-qb", HEAD)
    artifact = checkout / ".kodezart"
    artifact.mkdir()
    (artifact / "ticket.json").write_text("{}")
    command(checkout, "add", ".")
    command(checkout, "commit", "-qm", "fire metadata")
    fire_sha = command(checkout, "rev-parse", "HEAD")
    command(checkout, "rm", "-qr", ".kodezart")
    command(checkout, "commit", "-qm", "clean metadata")
    head_sha = command(checkout, "rev-parse", "HEAD")
    command(checkout, "remote", "add", "upstream", str(remote))
    command(checkout, "push", "-q", "upstream", BASE, HEAD)
    return remote, checkout, fire_sha, head_sha


@pytest.mark.parametrize("cancellation_count", [1, 3])
@pytest.mark.parametrize(
    "phase,read_number",
    [
        ("clone_bare", 1),
        ("cache_fetch", 1),
        ("remote_branch_sha", 1),
        ("remote_branch_sha", 2),
        ("fetch", 1),
        ("is_ancestor", 1),
        ("diff_summary", 1),
    ],
)
async def test_public_delivery_owns_native_repository_read(
    tmp_path, monkeypatch, repository, phase, read_number, cancellation_count
):
    remote, checkout, fire_sha, head_sha = repository
    native = SubprocessGitService(remote="upstream")
    # Cache acquisition's native adapter uses its declared clone remote.
    cache_git = SubprocessGitService(remote="origin")
    cache = LocalBareRepoCache(git=cache_git, base_dir=str(tmp_path / "cache"))
    prior = existing_fixture()
    fixture = setup(git=native, cache=cache, query=prior.query, editor=prior.editor)
    execution = context().execution.model_copy(update={"repo_path": str(checkout)})
    facts = context(execution=execution)
    requested_sha = fire_sha
    method = phase
    if phase in {"clone_bare", "cache_fetch"}:
        execution = execution.model_copy(
            update={"repo_path": None, "repo_url": remote.as_uri(), "cache_key": None}
        )
        facts = context(execution=execution)
        requested_sha = head_sha
        if phase == "cache_fetch":
            await cache.ensure_available(remote.as_uri())
            method = "fetch"
        observed_adapter = cache_git
    else:
        observed_adapter = native
    original = getattr(observed_adapter, method)
    started = tmp_path / "started"
    finish = tmp_path / "finish"
    completed = tmp_path / "completed"
    call_count = 0
    settled_reads = []

    async def observed(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        selected = call_count == read_number
        if selected:
            program = (
                "from pathlib import Path\nimport os, time\n"
                f"pending = Path({str(started) + '.pending'!r})\n"
                "pending.write_text(str(os.getpid()))\n"
                f"pending.replace({str(started)!r})\n"
                f"while not Path({str(finish)!r}).exists(): time.sleep(0.01)\n"
                f"Path({str(completed)!r}).write_text('complete')\n"
            )
            # This is an actual child through the same native Git transport;
            # after release the selected real Git operation also completes.
            await observed_adapter._run_output(
                [sys.executable, "-c", program], cwd=str(tmp_path)
            )
        result = await original(*args, **kwargs)
        if selected:
            settled_reads.append(True)
        return result

    monkeypatch.setattr(observed_adapter, method, observed)
    task = asyncio.create_task(
        deliver(fixture.coordinator, facts=facts, sha=requested_sha)
    )
    try:
        async with asyncio.timeout(5):
            while not started.exists():
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        pid = int(started.read_text())
        for _ in range(cancellation_count):
            task.cancel()
            await asyncio.sleep(0.01)
        assert not task.done(), "delivery returned while its native child was alive"
        assert not completed.exists()
        finish.write_text("settle")
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert completed.exists() and settled_reads == [True]
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert (
            fixture.runner.calls == fixture.forge.calls == fixture.monitor.calls == []
        )
        assert fixture.gate.calls == []
        assert not [c for c in fixture.editor.calls if c["method"] == "edit_pr"]
        if phase == "clone_bare":
            cached = cache_dir_for_repo(str(tmp_path / "cache"), remote.as_uri())
            assert cache_git.is_repo(cached)
        assert await native.current_sha(str(checkout)) == head_sha
    finally:
        finish.write_text("settle")
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # Old-source controls deliberately fail before the native probe exits.
        async with asyncio.timeout(5):
            while started.exists() and not completed.exists():
                await asyncio.sleep(0.01)
