"""Git settings reach native repositories and composed runtime consumers."""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from kodezart.composition.workspace import build_git_stack
from kodezart.core.config import AppConfig
from kodezart.types.domain.agent import ResultEvent
from kodezart.types.domain.gating import RepoVisibility
from tests.core.test_retired_config import _from_source
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    PassThroughGate,
    make_prompt_provider,
)

DEFAULTS = {
    "remote": "origin",
    "base_url": "https://github.com",
    "clone_cache_dir": "/tmp/kodezart-clones",
    "integration_workspace_dir": "/tmp/kodezart-integration",
    "committer_name": "kodezart",
    "committer_email": "kodezart@noreply.dev",
}
OLD = [
    "git_remote",
    "git_base_url",
    "clone_cache_dir",
    "integration_workspace_dir",
    "git_committer_name",
    "git_committer_email",
]


def from_source(source, values, tmp_path, monkeypatch):
    if source == "init":
        return AppConfig(_env_file=None, git=values)
    if source == "env":
        for key, value in values.items():
            monkeypatch.setenv("KODEZART_GIT__" + key.upper(), value)
        return AppConfig(_env_file=None)
    if source == "dotenv":
        path = tmp_path / ".env"
        path.write_text(
            "\n".join(
                f"KODEZART_GIT__{key.upper()}='{value}'"
                for key, value in values.items()
            )
        )
        return AppConfig(_env_file=path)
    (tmp_path / "KODEZART_GIT").write_text(json.dumps(values))
    return AppConfig(_env_file=None, _secrets_dir=tmp_path)


async def git(cwd, *args):
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    assert proc.returncode == 0, err.decode()
    return out.decode().strip()


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
async def test_git_sources_reach_native_cache_remote_and_commit_consumers(
    source, tmp_path, monkeypatch
):
    values = DEFAULTS | {
        "remote": "configured",
        "clone_cache_dir": str(tmp_path / "cache"),
        "committer_name": "Configured Writer",
        "committer_email": "writer@example.invalid",
    }
    config = from_source(source, values, tmp_path, monkeypatch)
    assert config.git.model_dump() == values
    origin = tmp_path / "origin.git"
    await git(tmp_path, "init", "--bare", str(origin))
    seed = tmp_path / "seed"
    await git(tmp_path, "init", "-b", "main", str(seed))
    await git(seed, "commit", "--allow-empty", "-m", "seed")
    await git(seed, "push", str(origin), "main")
    await git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    stack = build_git_stack(
        settings=config.git,
        github_token=None,
        prompts=make_prompt_provider(),
        gate=PassThroughGate(),
    )
    cache = await stack.cache.ensure_available(origin.as_uri())
    assert Path(cache).is_relative_to(tmp_path / "cache")
    assert await git(cache, "remote") == "configured"
    workspace = await stack.workspace.acquire(
        repo_path=cache, ref="main", branch_name="work"
    )
    try:
        (Path(workspace) / "change.txt").write_text("changed")
        result = await stack.persister.persist(
            workspace_path=workspace,
            branch="work",
            executor=FakeAgentExecutor(
                events=[
                    ResultEvent(
                        subtype="result",
                        duration_ms=1,
                        duration_api_ms=1,
                        is_error=False,
                        num_turns=1,
                        session_id="fixture",
                        structured_output={"title": "Fixture change", "body": ""},
                    )
                ]
            ),
            backup_ref_id_prefix="fixture",
            skills=SUPPRESS_ALL_SKILLS,
            visibility=RepoVisibility.PRIVATE,
        )
        assert result is not None
        assert await git(origin, "rev-parse", "work") == result.commit_sha
        assert await git(origin, "log", "-1", "--format=%an|%ae|%cn|%ce", "work") == (
            "Configured Writer|writer@example.invalid|"
            "Configured Writer|writer@example.invalid"
        )
    finally:
        await stack.workspace.release(workspace)
    await stack.artifact_persister.persist(
        repo_path=cache,
        repo_url=None,
        branch="artifact",
        base_branch="main",
        artifacts={"fixture.txt": "record"},
    )
    assert await git(origin, "log", "-1", "--format=%an|%ae|%cn|%ce", "artifact") == (
        "Configured Writer|writer@example.invalid|"
        "Configured Writer|writer@example.invalid"
    )
    assert (
        len((await git(cache, "worktree", "list", "--porcelain")).split("worktree "))
        == 2
    )
    assert await stack.cache.ensure_available(origin.as_uri()) == cache


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
@pytest.mark.parametrize("field", OLD)
def test_old_flat_git_names_refuse(source, field, tmp_path, monkeypatch):
    with pytest.raises(ValidationError, match="Extra inputs") as caught:
        _from_source(source, field, "fixture", tmp_path, monkeypatch)
    assert field in str(caught.value).casefold()
    assert "input_value" not in str(caught.value)


def test_defaults_and_existing_source_precedence(tmp_path, monkeypatch):
    assert AppConfig(_env_file=None).git.model_dump() == DEFAULTS
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "KODEZART_GIT").write_text(json.dumps({"remote": "secret"}))
    dotenv = tmp_path / ".env"
    dotenv.write_text("KODEZART_GIT__REMOTE=dotenv\n")
    monkeypatch.setenv("KODEZART_GIT__REMOTE", "environment")
    kwargs = {"_env_file": dotenv, "_secrets_dir": secrets}
    assert AppConfig(**kwargs, git={"remote": "init"}).git.remote == "init"
    assert AppConfig(**kwargs).git.remote == "environment"
    monkeypatch.delenv("KODEZART_GIT__REMOTE")
    assert AppConfig(**kwargs).git.remote == "dotenv"
    assert AppConfig(_env_file=None, _secrets_dir=secrets).git.remote == "secret"


@pytest.mark.parametrize(
    "values", [{"remtoe": "upstream"}, {"remote": False}, {"clone_cache_dir": []}]
)
def test_invalid_nested_git_configuration_refuses(values):
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, git=values)


@pytest.mark.parametrize("custom", [False, True])
async def test_composed_dispatch_uses_remote_and_integration_directory(
    custom, tmp_path
):
    from kodezart.composition.passes import build_dispatch_runtime
    from kodezart.composition.tracker import DialledTracker
    from kodezart.core.logging import get_logger
    from kodezart.services.run_recorder import RunRecorder
    from kodezart.types.domain.branch import WorkRef, WorkRefRole
    from kodezart.types.domain.operation import QueueState
    from kodezart.types.domain.tracker import WorkflowStateKind
    from tests.fakes import (
        FIXTURE_EPOCH,
        FakeAgentRunner,
        FakeDeliveryProbe,
        FakeGitService,
        FakeJobQueue,
        FakeRepoCache,
        FakeTrackerPort,
        FakeWorkspaceProvider,
        ManagedFakeLinearMcpServer,
        make_tracker_issue,
    )
    from tests.services.test_dispatch_pass import operation_config

    config = AppConfig(
        _env_file=None,
        git={
            "remote": "configured",
            "integration_workspace_dir": str(tmp_path / "integration"),
        }
        if custom
        else {},
    )
    target = make_tracker_issue("K-1", blocked_by=["K-2", "K-3"])
    refs = {
        key: [
            WorkRef(
                issue_id=key,
                role=WorkRefRole.DELIVERABLE,
                branch=branch,
                pushed_head_sha=sha,
                recorded_at=FIXTURE_EPOCH,
            )
        ]
        for key, branch, sha in [
            ("K-2", "branch-two", "2" * 40),
            ("K-3", "branch-three", "3" * 40),
        ]
    }
    tracker = FakeTrackerPort(
        issues=[
            target,
            *[
                make_tracker_issue(
                    key,
                    queue_states=[QueueState.DONE],
                    state_kind=WorkflowStateKind.COMPLETED,
                )
                for key in refs
            ],
        ],
        recorded_work_refs=refs,
    )
    git_service = FakeGitService(
        remote_branch_shas={"branch-two": "2" * 40, "branch-three": "3" * 40}
    )
    queue = FakeJobQueue()
    operation = operation_config()
    runtime = await build_dispatch_runtime(
        workspace=FakeWorkspaceProvider(),
        config=config,
        operation=operation,
        dialled=DialledTracker(
            tracker=tracker,
            ledger=tracker.self_writes,
            caller=ManagedFakeLinearMcpServer(),
            operation=operation,
        ),
        github_api=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=git_service,
        cache=FakeRepoCache(),
        prompts=make_prompt_provider(),
        runner=FakeAgentRunner(events=[]),
        skills=SUPPRESS_ALL_SKILLS,
        recorder=RunRecorder(records={}, sinks={}),
        log=get_logger(__name__),
    )
    try:
        (dispatch,) = [
            entry
            for entry in runtime.scheduler.passes
            if entry.name.startswith("dispatch:")
        ]
        await dispatch.run(FIXTURE_EPOCH)
        assert len(queue.submissions) == 1
        _, request = queue.submissions[0]
        assert request.base_spec.base_role is WorkRefRole.INTEGRATION
        creates = [call for call in git_service.calls if call[0] == "create_worktree"]
        assert len(creates) == 1
        assert Path(creates[0][3]).parent == Path(config.git.integration_workspace_dir)
        probes = [call for call in git_service.calls if call[0] == "remote_branch_sha"]
        assert len(probes) == 2
        assert all(call[2] == config.git.remote for call in probes)
    finally:
        if runtime.lifecycle is not None:
            await runtime.lifecycle.drain()
