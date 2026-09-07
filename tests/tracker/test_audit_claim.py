"""Current-head judgments read actual tracker claims without old conclusions."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from kodezart.chains.audit_pass import AuditClaimVerifier
from kodezart.core.config import AppConfig
from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
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
from kodezart.types.domain.session import SessionType
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

    def build(set_name=V5_SET):
        return AuditClaimVerifier(
            tracker=tracker,
            records=LaneRecordReader(tracker=tracker, operation=OPERATION),
            cache=cache,
            git=git,
            workspace=workspace,
            runner=runner,
            prompts=load_registry(default_set=set_name),
            skills=SUPPRESS_ALL_SKILLS,
            config=AppConfig(git_remote="configured-remote"),
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
    assert args["allowed_tools"] == list(EVAL_TOOLS)
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
            await tracker.upsert_comment(
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
        runner._events[0] = result_event(subtype="error", is_error=True)
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
        allowed_tools=list(EVAL_TOOLS),
        session_id="writer-session",
    ):
        pass
    assert runner.calls[0]["session_id"] == "writer-session"
