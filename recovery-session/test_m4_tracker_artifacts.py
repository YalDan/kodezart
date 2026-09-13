"""Actual tracker adapter, lease, reread and loop with only MCP/agent boundaries doubled."""

import asyncio

import pytest

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.core.backoff import RetryPolicy
from kodezart.domain.errors import WriteBackReadError
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.dispatch import SelfWriteLedger
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.chains.test_write_back_verifier import (
    ABSENT_PATH, FIRST_OUTPUT, HEAD, ISSUE, PATHS_AT_REF, REAL_PATH,
    EvidenceWriteBack, PathCheckingJudge, StubbornWriteBack,
)
from tests.fakes import FakeLinearMcpServer, FakeMcpIssue
from tests.tracker.conftest import FIXTURE_NOW, QUEUE_STATE_LABELS, STATE_TYPES, TEAM_IDENTIFIERS, WORKFLOW_STATE_NAMES
from tests.tracker.marker_config import MARKER_PREFIXES


def adapter():
    server = FakeLinearMcpServer(issues=[FakeMcpIssue(id=ISSUE)], state_types=STATE_TYPES)
    tracker = LinearMcpTracker(
        caller=server, marker_prefixes={**MARKER_PREFIXES, "evidence": "fixture-evidence"},
        queue_state_labels=QUEUE_STATE_LABELS, workflow_state_names=WORKFLOW_STATE_NAMES,
        team_identifiers=TEAM_IDENTIFIERS, retry=RetryPolicy(attempts=1, initial_delay=0),
        clock=lambda: FIXTURE_NOW, ledger=SelfWriteLedger(),
    )
    return tracker, server


@pytest.mark.parametrize("repair", [False, True])
async def test_actual_adapter_rounds_reread_and_repair(repair):
    tracker, _ = adapter()
    judge = PathCheckingJudge(paths_at_ref=PATHS_AT_REF)
    step_type = EvidenceWriteBack if repair else StubbornWriteBack
    step = step_type(tracker=tracker, first_output=FIRST_OUTPUT, correction=REAL_PATH)
    result = await WriteBackVerifier(tracker=tracker, judge=judge, max_rounds=2).write_back(step=step, ref=HEAD)
    assert len(result.rounds) == 2
    assert result.rounds[0].cited_refs == (ABSENT_PATH,)
    assert step.findings == [None, result.rounds[0]]
    assert result.verdict is (AuditVerdict.HOLDS if repair else AuditVerdict.UNVERIFIABLE)
    assert result.artifact == judge.seen[-1][0]
    assert result.artifact.native_ref
    assert all(ref == HEAD for _, ref in judge.seen)


@pytest.mark.parametrize("kind", [kind for kind in SurfaceKind if kind not in {
    SurfaceKind.ISSUE_DESCRIPTION, SurfaceKind.MARKER_COMMENT, SurfaceKind.CONTAINER_DESCRIPTION,
}])
async def test_unavailable_surface_refuses_before_any_write(kind):
    tracker, server = adapter()
    class Step:
        surface = WritableSurface(kind=kind, ref=ScopeRef(kind=ScopeKind.ISSUE, key=ISSUE))
        calls = 0
        async def write(self, *, finding):
            self.calls += 1
    step = Step()
    judge = PathCheckingJudge(paths_at_ref=PATHS_AT_REF)
    with pytest.raises(WriteBackReadError, match="whole-surface read is unavailable"):
        await WriteBackVerifier(tracker=tracker, judge=judge, max_rounds=2).write_back(step=step, ref=HEAD)
    assert step.calls == 0 and not judge.seen and not server.calls


@pytest.mark.parametrize("failure", [asyncio.CancelledError, RuntimeError])
async def test_actual_writer_failure_escapes_without_replay_or_judgment(failure):
    tracker, server = adapter()
    original = server.call_tool
    async def fail_save(name, arguments):
        if name == "save_comment" and "fixture-evidence" in str(arguments):
            raise failure("external failure")
        return await original(name, arguments)
    server.call_tool = fail_save
    judge = PathCheckingJudge(paths_at_ref=PATHS_AT_REF)
    step = EvidenceWriteBack(tracker=tracker, first_output=FIRST_OUTPUT, correction=REAL_PATH)
    with pytest.raises(failure):
        await WriteBackVerifier(tracker=tracker, judge=judge, max_rounds=3).write_back(step=step, ref=HEAD)
    assert step.findings == [None] and not judge.seen
