"""Current-head judgments read actual tracker claims without old conclusions."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from kodezart.chains.audit_pass import AuditClaimVerifier
from kodezart.core.config import AppConfig
from kodezart.core.constants import EVAL_PERMISSION_MODE
from kodezart.core.errors import NoStructuredOutputError
from kodezart.domain.errors import AuditClaimReadError, InvalidFireCriterionError
from kodezart.domain.lane_record import render_lane_record
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.agent import AUDIT_CLAIM_SCHEMA, ResultEvent
from kodezart.types.domain.audit import (
    AuditClaimJudgment,
    AuditClaimRequest,
    AuditVerdict,
)
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_state import LaneRunState
from kodezart.types.domain.session import SessionType, ToolPreset
from kodezart.types.domain.subagents import NO_SUBAGENTS
from tests.domain.test_lane_record import record_data
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeGitService,
    FakeMcpIssue,
    FakeRepoCache,
    FakeWorkspaceProvider,
)
from tests.prompts.sets import OPUS_SET, V5_SET
from tests.prompts.test_prompt_wiring import load_registry
from tests.tracker.conftest import fixture_server
from tests.tracker.lease_fixtures import leased_comment

ROOT = "audit/lane"
CHILD = "audit/check"
HEAD = "a" * 40
PREFIXES = {"run_state": "audit-fixture"}
OPERATION = OperationConfig(
    operation_name="fixture", workspace="fixture", marker_prefixes=PREFIXES
)
CHECK = "The command reverses a sequence, including an empty sequence."
BODY = (
    f"**Check:** {CHECK}\n\n**Evidence:** OLD_RED_VERDICT\n\n"
    "**Do:** AUTHOR_TRANSCRIPT\n\n**Class:** hard"
)
REQUEST = AuditClaimRequest(
    criterion_key=CHILD,
    lane_issue_key=ROOT,
    lane_key="lane:alpha",
    repo_url="https://forge.invalid/team/project",
    cache_key="configured-cache",
)


def result_event(**changes):
    fields = {
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "session_id": "fresh-result",
    }
    return ResultEvent(**{**fields, **changes})


@pytest.fixture
def server():
    server = fixture_server()
    server.issues[ROOT] = FakeMcpIssue(id=ROOT)
    server.issues[CHILD] = FakeMcpIssue(
        id=CHILD, parent_id=ROOT, labels=["acceptance-condition"], description=BODY
    )
    return server


class Runner(FakeAgentRunner):
    async def stream_in_workspace(self, **kwargs):
        self.arguments = kwargs
        if getattr(self, "during", None):
            await self.during()
        async for event in super().stream_in_workspace(**kwargs):
            yield event


@pytest.fixture
async def setup(tracker):
    record = LaneRunState.model_validate(record_data())
    stored = await tracker.post_comment(
        issue_key=ROOT, body=render_lane_record(record=record, marker_prefixes=PREFIXES)
    )
    runner = Runner(
        [
            result_event(
                subtype="success",
                session_id="new-audit-session",
                structured_output={
                    "criterionKey": CHILD,
                    "verdict": "holds",
                    "evidence": "Executed the reversal cases at the selected head.",
                },
            )
        ]
    )
    git = FakeGitService(remote_branch_shas={record.branch: HEAD})
    cache = FakeRepoCache()
    workspace = FakeWorkspaceProvider()

    def build(set_name=V5_SET, *, remote="configured-remote"):
        return AuditClaimVerifier(
            tracker=tracker,
            records=LaneRecordReader(tracker=tracker, operation=OPERATION),
            cache=cache,
            git=git,
            workspace=workspace,
            runner=runner,
            prompts=load_registry(default_set=set_name),
            skills=SUPPRESS_ALL_SKILLS,
            remote=remote,
        )

    return build, runner, git, cache, workspace, stored


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
async def test_actual_session_reads_current_check_and_live_head(
    setup, tracker_writes, set_name
):
    build, runner, git, cache, workspace, stored = setup
    writes = tracker_writes()
    result = await build(set_name).verify(REQUEST)
    assert result.judgment.verdict is AuditVerdict.HOLDS
    assert result.head_sha == HEAD and result.record_ref == stored.comment_key
    assert result.check == CHECK
    args = runner.arguments
    assert CHECK in args["prompt"] and HEAD in args["prompt"]
    assert (
        "OLD_RED_VERDICT" not in args["prompt"]
        and "AUTHOR_TRANSCRIPT" not in args["prompt"]
    )
    assert "head-full-identity" not in args["prompt"]
    assert args["session_id"] is None
    assert args["session_type"] is SessionType.SCHEDULED_PASS
    assert args["permission_mode"] == EVAL_PERMISSION_MODE
    assert args["allowed_tools"] == ToolPreset.EVALUATION
    assert args["agents"] == NO_SUBAGENTS
    assert args["output_format"]["schema"] == AUDIT_CLAIM_SCHEMA
    assert cache.calls == [{"url": REQUEST.repo_url, "cache_key": REQUEST.cache_key}]
    assert workspace.calls == [
        ("acquire", "/tmp/fake-cache", HEAD),
        ("release", "/tmp/fake-workspace"),
    ]
    assert (
        "remote_branch_sha",
        "/tmp/fake-cache",
        "configured-remote",
        "ordinary-name",
    ) in git.calls
    assert tracker_writes() == writes


@pytest.mark.parametrize("verdict", list(AuditVerdict))
async def test_all_three_judgments_preserved_without_writes(
    setup, tracker_writes, verdict
):
    build, runner, *_ = setup
    runner._events[0] = result_event(
        subtype="success",
        structured_output={
            "criterionKey": CHILD,
            "verdict": verdict.value,
            "evidence": "Measured evidence or missing resource.",
        },
    )
    writes = tracker_writes()
    assert (await build().verify(REQUEST)).judgment.verdict is verdict
    assert tracker_writes() == writes
    with pytest.raises(TypeError):
        bool(verdict)


@pytest.mark.parametrize(
    "body",
    [
        "**Evidence:** no Check",
        "**Check:**\n**Evidence:** old",
        "**Check:** one\n**Check:** two",
        "<!-- **Check:** invisible -->",
    ],
)
async def test_missing_or_ambiguous_check_refuses_before_git_and_session(
    setup, tracker, body
):
    build, runner, _, cache, workspace, _ = setup
    await tracker.update_issue(issue_key=CHILD, body=body)
    with pytest.raises(InvalidFireCriterionError):
        await build().verify(REQUEST)
    assert not runner.calls and not cache.calls and not workspace.calls


@pytest.mark.parametrize("head", [None, ""])
async def test_missing_remote_refuses_without_session(setup, head):
    build, runner, git, _, workspace, _ = setup
    git._remote_branch_shas["ordinary-name"] = head
    with pytest.raises(AuditClaimReadError, match="live remote"):
        await build().verify(REQUEST)
    assert not runner.calls and not workspace.calls


@pytest.mark.parametrize(
    "damage",
    [
        "criterion",
        "head",
        "workspace",
        "record",
        "identity",
        "empty-output",
        "error",
        "cancel",
    ],
)
async def test_inflight_changes_and_failed_sessions_never_return_observation(
    setup, tracker, monkeypatch, damage
):
    build, runner, git, _, workspace, stored = setup

    async def during():
        if damage == "criterion":
            await tracker.update_issue(
                issue_key=CHILD, body=BODY.replace(CHECK, "Changed Check.")
            )
        elif damage == "record":
            await leased_comment(
                tracker,
                target=ROOT,
                marker=stored.body.splitlines()[0],
                body=stored.body.partition("\n")[2].replace(
                    "head-full-identity", "changed-record-head"
                ),
            )
        elif damage == "head":
            git._remote_branch_shas["ordinary-name"] = "b" * 40
        elif damage == "workspace":
            monkeypatch.setattr(git, "current_sha", AsyncMock(return_value="b" * 40))
        elif damage == "cancel":
            raise asyncio.CancelledError

    runner.during = during
    error = AuditClaimReadError
    if damage == "identity":
        runner._events[0] = result_event(
            subtype="success",
            structured_output={
                "criterionKey": "other",
                "verdict": "holds",
                "evidence": "other evidence",
            },
        )
    elif damage == "empty-output":
        runner._events = []
        error = NoStructuredOutputError
    elif damage == "error":
        runner._events[0] = result_event(
            subtype="error",
            is_error=True,
            structured_output={
                "criterionKey": CHILD,
                "verdict": "holds",
                "evidence": "not a successful session",
            },
        )
        error = NoStructuredOutputError
    elif damage == "cancel":
        error = asyncio.CancelledError
    with pytest.raises(error):
        await build().verify(REQUEST)
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


@pytest.mark.parametrize("value", [True, False, "accepted", "", None])
def test_verdict_cannot_collapse_or_expand(value):
    with pytest.raises(ValidationError):
        AuditClaimJudgment(criterion_key=CHILD, verdict=value, evidence="evidence")


@pytest.mark.parametrize("kind", ["missing", "duplicate", "wrong-parent"])
async def test_invalid_membership_refuses_before_repository_activity(
    setup, tracker, monkeypatch, kind
):
    build, runner, _, cache, workspace, _ = setup
    rows = list(await tracker.read_criteria(issue_key=ROOT))
    if kind == "missing":
        rows = []
    elif kind == "duplicate":
        rows = rows + rows
    else:
        rows = [row.model_copy(update={"parent_key": "different-lane"}) for row in rows]
    monkeypatch.setattr(tracker, "read_criteria", AsyncMock(return_value=rows))
    with pytest.raises(AuditClaimReadError):
        await build().verify(REQUEST)
    assert not runner.calls and not cache.calls and not workspace.calls


async def test_cold_repeated_verification_reexecutes_without_session_resume(setup):
    build, runner, *_ = setup
    first = await build().verify(REQUEST)
    second = await build().verify(REQUEST)
    assert first == second
    assert len(runner.calls) == 2
    assert all(call["session_id"] is None for call in runner.calls)


async def test_wrong_initial_workspace_refuses_before_session_and_releases(
    setup, monkeypatch
):
    build, runner, git, _, workspace, _ = setup
    monkeypatch.setattr(git, "current_sha", AsyncMock(return_value="wrong-head"))
    with pytest.raises(AuditClaimReadError, match="workspace"):
        await build().verify(REQUEST)
    assert not runner.calls
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


@pytest.mark.parametrize(
    "damage", ["blank-evidence", "extra-prior-verdict", "wrong-verdict"]
)
async def test_malformed_judgment_refuses_and_releases(setup, damage):
    build, runner, _, _, workspace, _ = setup
    payload = {"criterionKey": CHILD, "verdict": "holds", "evidence": "evidence"}
    if damage == "blank-evidence":
        payload["evidence"] = "   "
    elif damage == "extra-prior-verdict":
        payload["priorVerdict"] = "holds"
    else:
        payload["verdict"] = True
    runner._events = [result_event(subtype="success", structured_output=payload)]
    with pytest.raises(ValidationError):
        await build().verify(REQUEST)
    assert workspace.calls[-1] == ("release", "/tmp/fake-workspace")


async def test_runner_double_records_stream_session_identity():
    runner = FakeAgentRunner([])
    async for _ in runner.stream(
        prompt="read claim",
        permission_mode=EVAL_PERMISSION_MODE,
        allowed_tools=ToolPreset.EVALUATION,
        session_id="writer-session",
    ):
        pass
    assert runner.calls[0]["session_id"] == "writer-session"


async def test_actual_agent_service_forwards_fresh_dispatch_and_detached_workspace(
    setup, tracker, monkeypatch
):
    from kodezart.services.agent_service import AgentService
    from tests.fakes import FakeAgentExecutor

    _, runner, git, cache, workspace, _ = setup
    acquire = AsyncMock(wraps=workspace.acquire)
    monkeypatch.setattr(workspace, "acquire", acquire)
    executor = FakeAgentExecutor(runner._events)
    service = AgentService(
        executor=executor, workspace=workspace, git_base_url="https://forge.invalid"
    )
    verifier = AuditClaimVerifier(
        tracker=tracker,
        records=LaneRecordReader(tracker=tracker, operation=OPERATION),
        cache=cache,
        git=git,
        workspace=workspace,
        runner=service,
        prompts=load_registry(default_set=V5_SET),
        skills=SUPPRESS_ALL_SKILLS,
        remote=AppConfig(git={"remote": "configured-remote"}).git.remote,
    )
    observation = await verifier.verify(REQUEST)
    assert observation.judgment.verdict is AuditVerdict.HOLDS
    acquire.assert_awaited_once_with(
        repo_path="/tmp/fake-cache",
        repo_url=None,
        ref=HEAD,
        create_branch=False,
        cache_key=None,
    )
    (call,) = executor.calls
    assert call["session_id"] is None
    assert call["permission_mode"] == EVAL_PERMISSION_MODE
    assert call["allowed_tools"] == ToolPreset.EVALUATION
    assert call["output_format"]["schema"] == AUDIT_CLAIM_SCHEMA
    assert call["cwd"] == "/tmp/fake-workspace"
    assert "OLD_RED_VERDICT" not in call["prompt"]


async def test_repeated_cancellation_must_settle_release(setup, monkeypatch):
    build, runner, _, _, workspace, _ = setup
    running = asyncio.Event()
    releasing = asyncio.Event()
    settle = asyncio.Event()
    released = []

    async def during():
        running.set()
        await asyncio.Future()

    async def release(path):
        releasing.set()
        await settle.wait()
        released.append(path)

    runner.during = during
    monkeypatch.setattr(workspace, "release", release)
    task = asyncio.create_task(build().verify(REQUEST))
    await asyncio.wait_for(running.wait(), 2)
    task.cancel()
    await asyncio.wait_for(releasing.wait(), 2)
    task.cancel()
    settle.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released == ["/tmp/fake-workspace"]


async def test_cancellation_during_acquisition_must_recover_owned_workspace(
    setup, monkeypatch
):
    build, runner, _, _, workspace, _ = setup
    created = asyncio.Event()
    acquired = asyncio.Event()
    released = []

    async def acquire(**kwargs):
        created.set()
        await acquired.wait()
        return "/tmp/created-workspace"

    async def release(path):
        released.append(path)

    monkeypatch.setattr(workspace, "acquire", acquire)
    monkeypatch.setattr(workspace, "release", release)
    task = asyncio.create_task(build().verify(REQUEST))
    await asyncio.wait_for(created.wait(), 2)
    task.cancel()
    acquired.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released == ["/tmp/created-workspace"]
    assert not runner.calls


@pytest.mark.parametrize("when", ["before", "during"])
@pytest.mark.parametrize("kind", ["tracked", "staged", "untracked", "clean", "ignored"])
async def test_real_git_workspace_integrity_for_pinned_observation(
    setup, tmp_path, monkeypatch, when, kind
):
    import subprocess

    from kodezart.adapters.subprocess_git_service import SubprocessGitService

    def command(*args):
        return subprocess.check_output(
            ["git", *args], cwd=tmp_path, stderr=subprocess.STDOUT, text=True
        ).strip()

    command("init")
    command("config", "user.email", "fixture@example.invalid")
    command("config", "user.name", "Fixture")
    tracked = tmp_path / "evidence.txt"
    tracked.write_text("committed evidence\n")
    (tmp_path / ".gitignore").write_text("generated/\n")
    command("add", "evidence.txt", ".gitignore")
    command("commit", "-m", "fixture")
    head = command("rev-parse", "HEAD")
    command("checkout", "--detach", head)
    build, runner, git, _, workspace, _ = setup
    native = SubprocessGitService(remote="configured-remote")
    git._remote_branch_shas["ordinary-name"] = head
    monkeypatch.setattr(git, "current_sha", native.current_sha)
    monkeypatch.setattr(git, "has_changes", native.has_changes)
    monkeypatch.setattr(workspace, "acquire", AsyncMock(return_value=str(tmp_path)))

    async def dirty():
        if kind == "clean":
            return
        if kind == "ignored":
            generated = tmp_path / "generated"
            generated.mkdir()
            (generated / "output.txt").write_text("test output\n")
            assert not await native.has_changes(str(tmp_path))
            return
        path = tracked if kind != "untracked" else tmp_path / "extra-evidence.txt"
        path.write_text("evidence not present in the selected commit\n")
        if kind == "staged":
            command("add", "evidence.txt")
        assert command("rev-parse", "HEAD") == head
        assert await native.has_changes(str(tmp_path))

    if when == "before":
        await dirty()
    else:
        runner.during = dirty
    if kind in {"clean", "ignored"}:
        observation = await build().verify(REQUEST)
        assert observation.judgment.verdict is AuditVerdict.HOLDS
        assert observation.head_sha == head
    else:
        with pytest.raises(AuditClaimReadError, match="workspace"):
            await build().verify(REQUEST)
    assert workspace.calls[-1] == ("release", str(tmp_path))
    if when == "before" and kind not in {"clean", "ignored"}:
        assert not runner.calls


@pytest.mark.parametrize("phase", ["current_sha", "has_changes"])
@pytest.mark.parametrize("read_number", [1, 2])
async def test_native_git_read_cancellation_settles_before_workspace_release(
    setup, monkeypatch, tmp_path, phase, read_number
):
    from tests.git_read_cancellation import assert_git_read_settles_before_release

    build, _, git, _, workspace, _ = setup
    await assert_git_read_settles_before_release(
        invoke=lambda: build().verify(REQUEST),
        git=git,
        workspace=workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase=phase,
        read_number=read_number,
    )


@pytest.mark.parametrize("configured", [None, "deployment-remote"])
async def test_parsed_remote_value_reaches_the_actual_claim_reads(
    setup, monkeypatch, configured
):
    if configured is None:
        monkeypatch.delenv("KODEZART_GIT__REMOTE", raising=False)
    else:
        monkeypatch.setenv("KODEZART_GIT__REMOTE", configured)
    remote = AppConfig(_env_file=None).git.remote
    build, _, git, *_ = setup
    observed = await build(remote=remote).verify(REQUEST)
    assert observed.head_sha == HEAD
    calls = [call for call in git.calls if call[0] == "remote_branch_sha"]
    assert len(calls) == 2
    assert {call[2] for call in calls} == {configured or "origin"}
