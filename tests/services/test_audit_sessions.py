"""Shared audit execution keeps fresh-context and owned-workspace boundaries."""

from copy import deepcopy
from functools import partial
from unittest.mock import AsyncMock

import pytest

from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.core.errors import NoStructuredOutputError
from kodezart.domain.errors import AuditClaimReadError
from kodezart.services.audit_sessions import FreshAuditSession
from kodezart.types.domain.agent import AUDIT_CLAIM_SCHEMA
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.subagents import NO_SUBAGENTS
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeGitService, FakeWorkspaceProvider
from tests.git_read_cancellation import assert_git_read_settles_before_release
from tests.prompts.test_prompt_wiring import load_registry
from tests.tracker.test_audit_claim import HEAD, Runner, result_event


@pytest.fixture
def session():
    runner = Runner(
        [result_event(subtype="success", structured_output={"fresh": "observation"})]
    )
    git = FakeGitService()
    workspace = FakeWorkspaceProvider()
    return FreshAuditSession(
        git=git,
        workspace=workspace,
        runner=runner,
        prompts=load_registry(),
        skills=SUPPRESS_ALL_SKILLS,
    )


def invoke(session, **changes):
    return session.judge(
        **{
            "repository": "/tmp/fake-cache",
            "head_sha": HEAD,
            "key": PromptKey.AUDIT_CLAIM,
            "prompt": "Fresh current source only.",
            "output_schema": AUDIT_CLAIM_SCHEMA,
            "site": "audit_claim",
            **changes,
        }
    )


async def test_success_keeps_exact_input_and_fresh_read_only_session(session):
    expected_schema = deepcopy(AUDIT_CLAIM_SCHEMA)
    assert await invoke(session) == {"fresh": "observation"}
    args = session._runner.arguments
    assert args["session_id"] is None
    assert args["permission_mode"] == EVAL_PERMISSION_MODE
    assert args["allowed_tools"] == EVAL_TOOLS
    assert args["agents"] == NO_SUBAGENTS
    assert args["prompt"] == "Fresh current source only."
    assert args["output_format"] == {"type": "json_schema", "schema": expected_schema}
    assert args["output_format"]["schema"] is AUDIT_CLAIM_SCHEMA
    assert AUDIT_CLAIM_SCHEMA == expected_schema
    assert session._workspace.calls == [
        ("acquire", "/tmp/fake-cache", HEAD),
        ("release", "/tmp/fake-workspace"),
    ]


@pytest.mark.parametrize("head", ["main", "a" * 7, "", "a" * 39])
async def test_session_requires_an_immutable_commit_before_acquisition(session, head):
    with pytest.raises(AuditClaimReadError):
        await invoke(session, head_sha=head)
    assert not session._workspace.calls and not session._runner.calls


@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("damage", ["head", "dirty"])
async def test_workspace_integrity_is_checked_around_the_session(
    session, monkeypatch, phase, damage
):
    def change():
        target = "current_sha" if damage == "head" else "has_changes"
        monkeypatch.setattr(
            session._git,
            target,
            AsyncMock(return_value="b" * 40 if damage == "head" else True),
        )

    if phase == "before":
        change()
    else:

        async def during():
            change()

        session._runner.during = during
    with pytest.raises(AuditClaimReadError):
        await invoke(session)
    assert session._workspace.calls[-1] == ("release", "/tmp/fake-workspace")
    if phase == "before":
        assert not session._runner.calls


@pytest.mark.parametrize("damage", ["empty", "unstructured", "error"])
async def test_no_observation_is_returned_for_failed_execution(session, damage):
    session._runner._events = (
        []
        if damage == "empty"
        else [
            result_event(
                subtype="success",
                structured_output=None
                if damage == "unstructured"
                else {"ignored": True},
                is_error=damage == "error",
            )
        ]
    )
    with pytest.raises(NoStructuredOutputError):
        await invoke(session)
    assert session._workspace.calls[-1] == ("release", "/tmp/fake-workspace")


@pytest.mark.parametrize("phase", ["current_sha", "has_changes"])
@pytest.mark.parametrize("read_number", [1, 2])
async def test_native_workspace_reads_settle_through_repeated_cancellation(
    session, monkeypatch, tmp_path, phase, read_number
):
    await assert_git_read_settles_before_release(
        invoke=partial(invoke, session),
        git=session._git,
        workspace=session._workspace,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        phase=phase,
        read_number=read_number,
    )
