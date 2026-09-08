"""Actual ruling sessions retain the resolved Git bytes and release ownership."""

import asyncio
import subprocess
from functools import partial
from pathlib import Path

import pytest

from kodezart.domain.errors import RulingProposalError
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import RULING_PROPOSAL_SCHEMA
from tests.adapters.test_git_worktree_provider import git_repo as git_repo
from tests.adapters.test_git_worktree_provider import provider as provider
from tests.fakes import FakeAgentExecutor, FakeRepoCache
from tests.git_read_cancellation import assert_git_read_settles_before_release
from tests.tracker import test_rule_open_questions as fixtures
from tests.tracker.test_audit_claim import result_event

server = fixtures.server
setup = fixtures.setup
proposal = fixtures.proposal


def git(path, *args):
    return subprocess.check_output(
        ["git", "--no-replace-objects", *args],
        cwd=path,
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


@pytest.fixture
async def native(proposal, setup, git_repo, provider):
    (git_repo / "evidence.txt").write_text("The resolved dispatch source.\n")
    git(git_repo, "add", "evidence.txt")
    git(git_repo, "commit", "-qm", "Dispatch source")
    head = git(git_repo, "rev-parse", "HEAD")
    cache = FakeRepoCache(str(git_repo))
    build_validator, *_ = setup
    build, runner, *_ = proposal
    validator = build_validator(git=provider._git, workspace=provider, cache=cache)
    request = fixtures.source.REQUEST.model_copy(update={"head_sha": head})

    def consumer(**changes):
        return build(
            **{
                "validator": validator,
                "git": provider._git,
                "workspace": provider,
                "cache": cache,
                **changes,
            }
        )

    return consumer, runner, request, validator


async def test_actual_agent_service_reads_detached_source_and_advances_no_branch(
    native, provider, git_repo, tracker_writes
):
    build, runner, request, _ = native
    before_refs = git(git_repo, "show-ref")
    before_writes = tracker_writes()
    paths = []

    class Executor(FakeAgentExecutor):
        async def stream(self, **kwargs):
            path = Path(kwargs["cwd"])
            paths.append(path)
            assert kwargs["agents"] == fixtures.NO_SUBAGENTS
            assert (
                path / "evidence.txt"
            ).read_text() == "The resolved dispatch source.\n"
            assert git(path, "rev-parse", "HEAD") == request.head_sha
            assert not git(path, "status", "--porcelain")
            detached = subprocess.run(
                ["git", "symbolic-ref", "-q", "HEAD"], cwd=path, capture_output=True
            )
            assert detached.returncode == 1
            async for event in super().stream(**kwargs):
                yield event

    executor = Executor(
        [result_event(subtype="success", structured_output=runner.output)]
    )
    service = AgentService(
        executor=executor, workspace=provider, git_base_url="https://forge.invalid"
    )
    result = await build(runner=service).propose(request)
    assert len(result.rulings) == 1
    (args,) = executor.calls
    assert args["session_id"] is None
    assert args["output_format"]["schema"] == RULING_PROPOSAL_SCHEMA
    assert args["allowed_tools"] == list(fixtures.EVAL_TOOLS)
    assert args["permission_mode"] == fixtures.EVAL_PERMISSION_MODE
    assert git(git_repo, "show-ref") == before_refs
    assert git(git_repo, "rev-parse", "HEAD") == request.head_sha
    assert tracker_writes() == before_writes
    assert not provider._workspaces
    assert paths and all(not path.exists() for path in paths)


@pytest.mark.parametrize(
    "kind", ["dirty", "advance", "replacement", "configured_replace"]
)
@pytest.mark.parametrize("phase", ["before", "during"])
async def test_native_changed_bytes_or_commit_cannot_return_a_proposal(
    native, provider, git_repo, kind, phase, monkeypatch
):
    build, runner, request, _ = native
    if kind == "configured_replace":
        monkeypatch.setenv("GIT_REPLACE_REF_BASE", "refs/ruling-replacement/")
    paths = []
    original = provider.acquire

    async def acquire(**kwargs):
        path = await original(**kwargs)
        paths.append(path)
        if len(paths) == 2 and phase == "before":
            await mutate()
        return path

    monkeypatch.setattr(provider, "acquire", acquire)

    async def mutate():
        workspace = Path(paths[-1])
        (workspace / "evidence.txt").write_text("Different source bytes.\n")
        if kind != "dirty":
            git(workspace, "add", "evidence.txt")
            git(workspace, "commit", "-qm", "Changed source")
            changed = git(workspace, "rev-parse", "HEAD")
            if "replace" in kind:
                git(workspace, "checkout", "--detach", request.head_sha)
                git(workspace, "replace", request.head_sha, changed)
                git(workspace, "pack-refs", "--all")
                # Materialize the substituted tree while retaining the original
                # visible commit identity; cleanliness alone must not accept it.
                subprocess.run(
                    ["git", "reset", "--hard", request.head_sha],
                    cwd=workspace,
                    check=True,
                    capture_output=True,
                )
                assert (
                    subprocess.check_output(
                        ["git", "status", "--porcelain"], cwd=workspace
                    )
                    == b""
                )
                assert git(workspace, "rev-parse", "HEAD") == request.head_sha
                assert (
                    workspace / "evidence.txt"
                ).read_text() == "Different source bytes.\n"

    if phase == "during":
        runner.during = mutate
    with pytest.raises(RulingProposalError):
        await build().propose(request)
    assert bool(runner.arguments) is (phase == "during")
    assert not provider._workspaces
    assert all(not Path(path).exists() for path in paths)
    assert git(git_repo, "rev-parse", "HEAD") == request.head_sha


@pytest.mark.parametrize("phase", ["acquire", "release", "session_then_release"])
async def test_repeated_cancellation_settles_actual_proposal_worktree(
    native, provider, git_repo, phase, monkeypatch
):
    build, runner, request, _ = native
    original_acquire = provider.acquire
    original_remove = provider._git.remove_worktree
    acquired, released = [], []
    entered, finish, in_session = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def acquire(**kwargs):
        path = await original_acquire(**kwargs)
        acquired.append(path)
        if len(acquired) == 2 and phase == "acquire":
            entered.set()
            await finish.wait()
        return path

    async def remove(repo_path, path):
        if len(acquired) == 2 and phase != "acquire":
            entered.set()
            await finish.wait()
        await original_remove(repo_path, path)
        released.append(path)

    async def wait():
        in_session.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(provider, "acquire", acquire)
    monkeypatch.setattr(provider._git, "remove_worktree", remove)
    if phase == "session_then_release":
        runner.during = wait
    task = asyncio.create_task(build().propose(request))
    try:
        if phase == "session_then_release":
            await asyncio.wait_for(in_session.wait(), 5)
            task.cancel()
        await asyncio.wait_for(entered.wait(), 5)
        assert len(acquired) == 2 and released == acquired[:1]
        assert Path(acquired[-1]).is_dir()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and released == acquired[:1]
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert released == acquired
        assert not provider._workspaces
        assert all(not Path(path).exists() for path in acquired)
        assert git(git_repo, "rev-parse", "HEAD") == request.head_sha
        if phase == "acquire":
            assert not runner.arguments
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("phase", ["current_sha", "has_changes", "has_replace_refs"])
@pytest.mark.parametrize("read_number", [1, 2])
async def test_proposal_native_read_settles_before_release(
    proposal, setup, monkeypatch, tmp_path, phase, read_number
):
    build, _, _, native_git, _, workspace = proposal
    build_validator, *_ = setup
    request = fixtures.source.REQUEST
    observed = await build_validator().validate(request)
    workspace.calls.clear()

    class Validated:
        async def validate(self, actual):
            assert actual == request
            return observed

    await assert_git_read_settles_before_release(
        invoke=partial(build(validator=Validated()).propose, request),
        git=native_git,
        workspace=workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase=phase,
        read_number=read_number,
    )


@pytest.mark.parametrize("existing", [False, True])
async def test_proposal_cache_read_settles_before_workspace(
    proposal, setup, monkeypatch, tmp_path, existing
):
    from tests.git_read_cancellation import assert_cache_read_settles

    build, runner, _, _, cache, workspace = proposal
    build_validator, *_ = setup
    request = fixtures.source.REQUEST
    observed = await build_validator().validate(request)
    workspace.calls.clear()

    class Validated:
        async def validate(self, actual):
            assert actual == request
            return observed

    await assert_cache_read_settles(
        invoke=partial(build(validator=Validated()).propose, request),
        cache=cache,
        workspace=workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        existing=existing,
    )
    assert not runner.arguments and not workspace.calls
