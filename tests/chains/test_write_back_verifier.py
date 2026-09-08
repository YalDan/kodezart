"""Native re-reads and fresh judgments bound the caller's write/repair loop."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from kodezart.chains.write_back_verifier import TrackerWriteBackVerifier
from kodezart.core.config import AppConfig
from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.core.protocols import WriteBackVerifier
from kodezart.domain.errors import WriteBackReadError
from kodezart.types.domain.agent import WRITE_BACK_SCHEMA, ResultEvent
from kodezart.types.domain.audit import AuditVerdict, WriteBackRequest, WriteBackResult
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.subagents import NO_SUBAGENTS
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeGitService,
    FakeMcpIssue,
    FakeWorkspaceProvider,
)
from tests.prompts.sets import OPUS_SET, V5_SET
from tests.prompts.test_prompt_wiring import load_registry
from tests.tracker.conftest import fixture_server
from tests.tracker.conftest import tracker as tracker
from tests.tracker.test_scope_reads import PROJECT
from tests.tracker.test_scope_reads import scope_fixture as scope_fixture

ISSUE = "write/lane"
HEAD = "a" * 40
REF = ScopeRef(kind=ScopeKind.ISSUE, key=ISSUE)
SURFACE = WritableSurface(kind=SurfaceKind.ISSUE_DESCRIPTION, ref=REF)
REQUEST = WriteBackRequest(
    surface=SURFACE,
    verification_goal="Every claimed test exists and reproduces the stated behavior.",
    repo_url="https://forge.invalid/team/project",
    head_sha=HEAD,
)


def judgment(verdict):
    return ResultEvent(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="independent-result",
        structured_output={
            "verdict": verdict,
            "evidence": "Read and executed the named test.",
        },
    )


class Runner(FakeAgentRunner):
    def __init__(self, verdicts):
        super().__init__([])
        self.verdicts = verdicts
        self.arguments = []
        self.during = None

    async def stream_in_workspace(self, **kwargs):
        self.arguments.append(kwargs)
        if self.during is not None:
            await self.during()
        yield judgment(self.verdicts[len(self.arguments) - 1])


@pytest.fixture
def server():
    server = fixture_server()
    server.issues[ISSUE] = FakeMcpIssue(id=ISSUE, description="initial")
    return server


@pytest.fixture
async def setup(tracker):
    runner = Runner(["refuted", "holds"])
    git = FakeGitService()
    workspace = FakeWorkspaceProvider()
    actions = []

    async def write():
        actions.append("write")
        await tracker.update_issue(issue_key=ISSUE, body="tests/missing.py passes")

    async def repair(artifact, result):
        actions.append(("repair", artifact.content, result.verdict))
        await tracker.update_issue(issue_key=ISSUE, body="tests/existing.py passes")

    def build(rounds=2, set_name=V5_SET):
        return TrackerWriteBackVerifier(
            tracker=tracker,
            runner=runner,
            workspace=workspace,
            git=git,
            prompts=load_registry(default_set=set_name),
            skills=SUPPRESS_ALL_SKILLS,
            config=AppConfig(write_back_max_verify_rounds=rounds),
        )

    return build, runner, git, workspace, actions, write, repair


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
async def test_native_write_read_repair_reverify_exposes_only_corrected_artifact(
    setup, set_name
):
    build, runner, _, workspace, actions, write, repair = setup
    verifier = build(set_name=set_name)
    assert isinstance(verifier, WriteBackVerifier)
    result = await verifier.verify(REQUEST, write=write, repair=repair)
    assert result.verdict is AuditVerdict.HOLDS
    assert result.verified_artifact.content == "tests/existing.py passes"
    assert result.verified_artifact.native_ref == ISSUE
    assert [r.verdict for r in result.rounds] == [
        AuditVerdict.REFUTED,
        AuditVerdict.HOLDS,
    ]
    assert actions == [
        "write",
        ("repair", "tests/missing.py passes", AuditVerdict.REFUTED),
    ]
    first, second = runner.arguments
    assert "tests/missing.py passes" in first["prompt"]
    assert "tests/missing.py passes" not in second["prompt"]
    assert "tests/existing.py passes" in second["prompt"]
    for args in runner.arguments:
        assert args["session_id"] is None
        assert args["session_type"] is SessionType.SCHEDULED_PASS
        assert args["permission_mode"] == EVAL_PERMISSION_MODE
        assert args["allowed_tools"] == list(EVAL_TOOLS)
        assert args["agents"] == NO_SUBAGENTS
        assert args["output_format"]["schema"] == WRITE_BACK_SCHEMA
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


@pytest.mark.parametrize("rounds", [1, 2, 4, 10])
@pytest.mark.parametrize("verdict", ["refuted", "unverifiable"])
async def test_exhaustion_is_unverifiable_with_no_downstream_artifact(
    setup, rounds, verdict
):
    build, runner, _, _, actions, write, repair = setup
    runner.verdicts = [verdict] * rounds
    result = await build(rounds).verify(REQUEST, write=write, repair=repair)
    assert result.verdict is AuditVerdict.UNVERIFIABLE
    assert result.verified_artifact is None
    assert len(result.rounds) == len(runner.arguments) == rounds
    assert len(actions) == rounds  # one write, rounds minus one repairs


async def test_first_holds_performs_no_repair(setup):
    build, runner, _, _, actions, write, repair = setup
    runner.verdicts = ["holds"]
    result = await build().verify(REQUEST, write=write, repair=repair)
    assert result.verdict is AuditVerdict.HOLDS
    assert actions == ["write"] and len(runner.arguments) == 1


@pytest.mark.parametrize(
    "kind",
    [
        SurfaceKind.ISSUE_LABEL_SET,
        SurfaceKind.CRITERION_SUB_ISSUE,
        SurfaceKind.CONTAINER_STATUS_UPDATE,
    ],
)
async def test_unsupported_whole_surface_refuses_before_write_or_workspace(setup, kind):
    build, runner, _, workspace, actions, write, repair = setup
    ref = (
        ScopeRef(kind=ScopeKind.PROJECT, key="project")
        if kind is SurfaceKind.CONTAINER_STATUS_UPDATE
        else REF
    )
    request = REQUEST.model_copy(
        update={"surface": WritableSurface(kind=kind, ref=ref)}
    )
    with pytest.raises(WriteBackReadError, match="whole-surface"):
        await build().verify(request, write=write, repair=repair)
    assert not actions and not workspace.calls and not runner.arguments


@pytest.mark.parametrize("damage", ["artifact", "head", "dirty"])
async def test_changes_during_verification_cannot_expose_artifact(
    setup, tracker, monkeypatch, damage
):
    build, runner, git, workspace, _, write, repair = setup
    runner.verdicts = ["holds"]

    async def mutate():
        if damage == "artifact":
            await tracker.update_issue(issue_key=ISSUE, body="concurrent edit")
        elif damage == "head":
            monkeypatch.setattr(git, "current_sha", AsyncMock(return_value="b" * 40))
        else:
            git.has_changes_result = True

    runner.during = mutate
    with pytest.raises(WriteBackReadError):
        await build().verify(REQUEST, write=write, repair=repair)
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


async def test_missing_marker_after_write_is_not_reconstructed_from_action(setup):
    build, runner, _, _, _, _, repair = setup
    request = REQUEST.model_copy(
        update={
            "surface": WritableSurface(
                kind=SurfaceKind.MARKER_COMMENT, ref=REF, marker="fixture-marker"
            )
        }
    )
    write = AsyncMock()
    with pytest.raises(WriteBackReadError, match="cannot be re-read"):
        await build().verify(request, write=write, repair=repair)
    write.assert_awaited_once()
    assert not runner.arguments


async def test_marker_repair_reads_actual_native_comment(setup, tracker):
    build, runner, *_ = setup
    surface = WritableSurface(
        kind=SurfaceKind.MARKER_COMMENT, ref=REF, marker="fixture-marker"
    )
    request = REQUEST.model_copy(update={"surface": surface})

    async def write():
        await tracker.upsert_comment(
            target=ISSUE, marker=surface.marker, body="first evidence"
        )

    async def repair(artifact, result):
        assert "first evidence" in artifact.content
        await tracker.upsert_comment(
            target=ISSUE, marker=surface.marker, body="corrected evidence"
        )

    result = await build().verify(request, write=write, repair=repair)
    actual = await tracker.list_comments(issue_key=ISSUE)
    assert len(actual) == 1
    assert result.verified_artifact.native_ref == actual[0].comment_key
    assert result.verified_artifact.content == actual[0].body
    assert "corrected evidence" in runner.arguments[-1]["prompt"]


@pytest.mark.parametrize("phase", ["acquire", "write", "repair", "release"])
async def test_repeated_cancellation_settles_owned_actions_and_workspace(
    setup, monkeypatch, phase
):
    build, _, _, workspace, _, write, repair = setup
    entered = asyncio.Event()
    settle = asyncio.Event()
    finished = []

    async def blocked():
        entered.set()
        await settle.wait()
        finished.append(phase)

    if phase == "acquire":

        async def acquire(**kwargs):
            await blocked()
            return "/tmp/fake-workspace"

        monkeypatch.setattr(workspace, "acquire", acquire)
    elif phase == "write":
        write = blocked
    elif phase == "repair":

        async def repair(*args):
            await blocked()
    else:
        original = workspace.release

        async def release(path):
            await blocked()
            await original(path)

        monkeypatch.setattr(workspace, "release", release)
    task = asyncio.create_task(build().verify(REQUEST, write=write, repair=repair))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    settle.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert finished == [phase]
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


@pytest.mark.parametrize("value", [0, 11, 1.5])
def test_invalid_configured_bound_refuses(value):
    with pytest.raises(ValidationError):
        AppConfig(write_back_max_verify_rounds=value)


@pytest.mark.parametrize("value", [1, 10])
def test_bound_loads_through_environment(monkeypatch, value):
    monkeypatch.setenv("KODEZART_WRITE_BACK_MAX_VERIFY_ROUNDS", str(value))
    assert AppConfig().write_back_max_verify_rounds == value


def test_declared_default_is_minimum_useful_repair_cycle():
    assert AppConfig.model_fields["write_back_max_verify_rounds"].default == 2


async def test_result_model_rejects_holds_without_verified_artifact(setup):
    with pytest.raises(ValidationError, match="verified artifact"):
        WriteBackResult(
            verdict="holds",
            rounds=[{"verdict": "holds", "evidence": "measured"}],
            verified_artifact=None,
        )


@pytest.mark.parametrize("damage", ["head", "dirty"])
async def test_initial_workspace_damage_refuses_before_write(
    setup, monkeypatch, damage
):
    build, runner, git, workspace, actions, write, repair = setup
    if damage == "head":
        monkeypatch.setattr(git, "current_sha", AsyncMock(return_value="b" * 40))
    else:
        git.has_changes_result = True
    with pytest.raises(WriteBackReadError):
        await build().verify(REQUEST, write=write, repair=repair)
    assert not actions and not runner.arguments
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


@pytest.mark.parametrize("phase", ["write", "repair", "session", "read"])
async def test_failure_propagates_without_result_and_releases(
    setup, tracker, monkeypatch, phase
):
    build, runner, _, workspace, _, write, repair = setup
    failure = RuntimeError("unreachable verification resource")
    if phase == "write":
        write = AsyncMock(side_effect=failure)
    elif phase == "repair":
        repair = AsyncMock(side_effect=failure)
    elif phase == "session":
        runner.during = AsyncMock(side_effect=failure)
    else:
        monkeypatch.setattr(tracker, "read_issue", AsyncMock(side_effect=failure))
    with pytest.raises(RuntimeError, match="unreachable"):
        await build().verify(REQUEST, write=write, repair=repair)
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


@pytest.mark.parametrize("damage", ["empty", "error", "invalid"])
async def test_structured_session_failures_do_not_return_verified_artifact(
    setup, monkeypatch, damage
):
    from kodezart.core.errors import NoStructuredOutputError

    build, runner, _, workspace, _, write, repair = setup

    async def stream(**kwargs):
        event = judgment("holds")
        if damage == "empty":
            event = event.model_copy(update={"structured_output": None})
        elif damage == "error":
            event = event.model_copy(update={"is_error": True})
        else:
            event = event.model_copy(
                update={"structured_output": {"verdict": True, "evidence": "unproved"}}
            )
        yield event

    monkeypatch.setattr(runner, "stream_in_workspace", stream)
    with pytest.raises((NoStructuredOutputError, ValidationError)):
        await build().verify(REQUEST, write=write, repair=repair)
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


async def test_actual_agent_service_preserves_fresh_identity_and_pinned_workspace(
    setup, tracker, monkeypatch
):
    from kodezart.services.agent_service import AgentService
    from tests.fakes import FakeAgentExecutor

    _, _, git, workspace, _, write, repair = setup
    acquire = AsyncMock(wraps=workspace.acquire)
    monkeypatch.setattr(workspace, "acquire", acquire)
    executor = FakeAgentExecutor([judgment("holds")])
    service = AgentService(
        executor=executor, workspace=workspace, git_base_url="https://forge.invalid"
    )
    verifier = TrackerWriteBackVerifier(
        tracker=tracker,
        runner=service,
        workspace=workspace,
        git=git,
        prompts=load_registry(default_set=V5_SET),
        skills=SUPPRESS_ALL_SKILLS,
        config=AppConfig(),
    )
    result = await verifier.verify(REQUEST, write=write, repair=repair)
    assert result.verdict is AuditVerdict.HOLDS
    acquire.assert_awaited_once_with(
        repo_url=REQUEST.repo_url, ref=HEAD, create_branch=False, cache_key=None
    )
    (call,) = executor.calls
    assert call["session_id"] is None
    assert call["cwd"] == "/tmp/fake-workspace"
    assert call["permission_mode"] == EVAL_PERMISSION_MODE
    assert call["allowed_tools"] == list(EVAL_TOOLS)
    assert call["output_format"]["schema"] == WRITE_BACK_SCHEMA


async def test_container_description_is_read_from_native_storage(scope_fixture):
    tracker = scope_fixture.tracker
    surface = WritableSurface(kind=SurfaceKind.CONTAINER_DESCRIPTION, ref=PROJECT)
    request = REQUEST.model_copy(update={"surface": surface})
    runner = Runner(["holds"])
    workspace = FakeWorkspaceProvider()
    verifier = TrackerWriteBackVerifier(
        tracker=tracker,
        runner=runner,
        workspace=workspace,
        git=FakeGitService(),
        prompts=load_registry(default_set=V5_SET),
        skills=SUPPRESS_ALL_SKILLS,
        config=AppConfig(),
    )

    async def write():
        # Simulate a caller's external container write. TrackerPort has no
        # container writer; this proves its real native read, not adoption.
        scope_fixture.server.projects[PROJECT.key]["description"] = (
            "Native stored evidence"
        )
        current = scope_fixture.fake.scope_containers[PROJECT]
        scope_fixture.fake.scope_containers[PROJECT] = current.model_copy(
            update={"description": "Native stored evidence"}
        )

    result = await verifier.verify(request, write=write, repair=AsyncMock())
    assert result.verified_artifact.content == "Native stored evidence"
    assert result.verified_artifact.native_ref == PROJECT.key
    assert "Native stored evidence" in runner.arguments[0]["prompt"]


async def test_repair_cannot_start_next_judgment_in_mutated_workspace(setup):
    build, runner, git, workspace, _, write, _ = setup

    async def repair(*args):
        git.has_changes_result = True

    with pytest.raises(WriteBackReadError, match="uncommitted"):
        await build().verify(REQUEST, write=write, repair=repair)
    assert len(runner.arguments) == 1
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


@pytest.mark.parametrize("phase", ["current_sha", "has_changes"])
@pytest.mark.parametrize("read_number", [1, 3])
async def test_native_git_read_cancellation_settles_before_workspace_release(
    setup, monkeypatch, tmp_path, phase, read_number
):
    from tests.git_read_cancellation import assert_git_read_settles_before_release

    build, _, git, workspace, _, write, repair = setup
    await assert_git_read_settles_before_release(
        invoke=lambda: build().verify(REQUEST, write=write, repair=repair),
        git=git,
        workspace=workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase=phase,
        read_number=read_number,
    )
