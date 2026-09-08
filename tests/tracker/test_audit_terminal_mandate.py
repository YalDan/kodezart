"""The actual sweep completes terminal mandates without criterion impersonation."""

import asyncio
from dataclasses import replace

import pytest

from kodezart.core.constants import EVAL_PERMISSION_MODE
from kodezart.domain.errors import AuditClaimReadError
from kodezart.domain.lane_record import render_lane_record
from kodezart.types.domain.agent import AUDIT_MANDATE_SCHEMA
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_terminal import TerminalDiscrepancy
from kodezart.types.domain.pr_state import PRLifecycle
from kodezart.types.domain.session import ToolPreset
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.tracker.test_audit_requests import PREFIXES
from tests.tracker.test_audit_sweep import CHILD, HEAD, REPO, ROOT, state
from tests.tracker.test_audit_sweep import server as server
from tests.tracker.test_audit_sweep import setup as setup


async def terminal_ready(tracker, server, forge):
    await state(tracker, server, ROOT, "In Review", WorkflowStateKind.STARTED)
    await state(tracker, server, CHILD, "Done", WorkflowStateKind.COMPLETED)
    forge.records[(REPO, 7)] = forge.records[(REPO, 7)].model_copy(
        update={"lifecycle": PRLifecycle.CLOSED}
    )


@pytest.mark.parametrize("outcome", ["holds", "refuted", "unverifiable"])
async def test_native_terminal_refutation_reuses_actual_mandate_hunt(
    setup, tracker, server, tracker_writes, monkeypatch, outcome
):
    build, executor, _, _, workspace, forge, *_ = setup
    await terminal_ready(tracker, server, forge)
    sweep = build()
    terminal = await sweep._terminals.observe(
        (await sweep._requests.read(scope=sweep._scope)).targets[1].request
    )
    if outcome == "holds":
        executor.mandate_output = {
            "verdict": "holds",
            "source_index": 1,
            "evidence": "The native source instructs this defect.",
            "finding": {
                "issue_id": ROOT,
                "defect_class": terminal.defect_class(),
                "role": "mandate",
                "mandate_text": "Explicit parent instructions.",
                "evidence": "Exact native quote.",
            },
        }
    if outcome == "unverifiable":
        original_observe = sweep._terminals.observe
        original_get = tracker.read_issue
        fail = False
        failed = False

        async def observe(request):
            nonlocal fail
            observed = await original_observe(request)
            fail = not failed
            return observed

        async def get(*, issue_key):
            nonlocal fail, failed
            if fail and issue_key == CHILD:
                fail = False
                failed = True
                raise RuntimeError("native body temporarily unreadable")
            return await original_get(issue_key=issue_key)

        monkeypatch.setattr(sweep._terminals, "observe", observe)
        monkeypatch.setattr(tracker, "read_issue", get)
    before = tracker_writes()
    result = await sweep.run()
    parent = result.observations[1]
    assert parent.unavailable_reason is None
    assert parent.terminal == terminal == parent.terminal_report.observation
    assert parent.terminal_report.mandate.verdict.value == outcome
    calls = [
        call
        for call in executor.calls
        if call["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA
    ]
    assert len(calls) == (0 if outcome == "unverifiable" else 1)
    if calls:
        call = calls[0]
        assert (
            call["session_id"] is None
            and call["permission_mode"] == EVAL_PERMISSION_MODE
        )
        assert call["allowed_tools"] == ToolPreset.EVALUATION
        assert terminal.refutation_evidence() in call["prompt"]
        assert terminal.defect_class() in call["prompt"] and HEAD in call["prompt"]
        assert '"criterion_key"' not in terminal.refutation_evidence()
        assert '"verdict"' not in terminal.refutation_evidence()
        assert workspace.calls[-1][0] == "release"
    else:
        assert parent.terminal_report.mandate.unreadable[0].surface.ref.key == CHILD
    assert tracker_writes() == before
    with pytest.raises(ValueError, match="differs"):
        replace(parent, terminal=terminal.model_copy(update={"record_ref": "foreign"}))
    with pytest.raises(ValueError, match="unavailable"):
        replace(parent, unavailable_reason="contradictory")
    with pytest.raises(ValueError, match="completion"):
        replace(parent, terminal_report=None)


async def test_no_branch_keeps_exact_observation_without_historical_head_substitution(
    setup, tracker, server, tracker_writes
):
    build, executor, git, _, _, forge, *_ = setup
    await terminal_ready(tracker, server, forge)
    git._remote_branch_shas["ordinary-name"] = None
    before = tracker_writes()
    parent = (await build().run()).observations[1]
    assert parent.terminal.branch_head is None
    assert TerminalDiscrepancy.NO_BRANCH in parent.terminal.discrepancies
    assert (
        parent.terminal_report is None
        and "no observed branch head" in parent.unavailable_reason
    )
    assert not executor.calls and tracker_writes() == before


@pytest.mark.parametrize(
    "failure", ["session", "wrong-defect", "wrong-source", "inexact-quote"]
)
async def test_mandate_failure_retains_native_terminal_and_refuses_complete_report(
    setup, tracker, server, failure
):
    build, executor, _, _, _, forge, *_ = setup
    await terminal_ready(tracker, server, forge)
    if failure == "session":

        async def during(kwargs):
            if kwargs["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA:
                raise RuntimeError("actual mandate session unavailable")

        executor.during = during
    else:
        sweep = build()
        terminal = await sweep._terminals.observe(
            (await sweep._requests.read(scope=sweep._scope)).targets[1].request
        )
        executor.mandate_output = {
            "verdict": "holds",
            "source_index": 1,
            "evidence": "Claimed mandate",
            "finding": {
                "issue_id": ROOT,
                "defect_class": terminal.defect_class(),
                "role": "mandate",
                "mandate_text": "Explicit parent instructions.",
                "evidence": "Quote",
            },
        }
        field = {
            "wrong-defect": "defect_class",
            "wrong-source": "issue_id",
            "inexact-quote": "mandate_text",
        }[failure]
        executor.mandate_output["finding"][field] = "foreign"
    result = await build().run()
    child, parent = result.observations
    assert child.claim is not None
    assert parent.terminal.verdict is AuditVerdict.REFUTED
    assert parent.terminal_report is None and parent.unavailable_reason


@pytest.mark.parametrize("change", ["head", "pr", "body", "record"])
async def test_terminal_facts_changing_during_mandate_refuse_whole_sweep(
    setup, tracker, server, change
):
    build, executor, git, _, _, forge, *_ = setup
    await terminal_ready(tracker, server, forge)
    sweep = build()
    source = (await sweep._requests.read(scope=sweep._scope)).targets[1].source

    async def during(kwargs):
        if kwargs["output_format"]["schema"] != AUDIT_MANDATE_SCHEMA:
            return
        if change == "head":
            git._remote_branch_shas["ordinary-name"] = "c" * 40
        elif change == "pr":
            forge.records[(REPO, 7)] = forge.records[(REPO, 7)].model_copy(
                update={"lifecycle": PRLifecycle.OPEN}
            )
        elif change == "record":
            rendered = render_lane_record(
                record=source.record.model_copy(
                    update={"files_changed": source.record.files_changed + 1}
                ),
                marker_prefixes=PREFIXES,
            )
            marker, _, body = rendered.partition("\n")
            updated = await tracker.upsert_comment(
                target=ROOT, marker=marker, body=body
            )
            assert updated.comment_key == source.comment.comment_key
            reparsed = await sweep._terminals._records.read(
                issue_key=ROOT,
                lane_key=source.record.lane_key,
                record_ref=source.comment.comment_key,
            )
            assert reparsed[1].files_changed == source.record.files_changed + 1
        else:
            await tracker.update_issue(issue_key=ROOT, body="Changed instructions.")

    executor.during = during
    with pytest.raises(AuditClaimReadError):
        await build().run()


async def test_actual_terminal_mandate_cancellation_propagates_after_release(
    setup, tracker, server
):
    build, executor, _, _, workspace, forge, *_ = setup
    await terminal_ready(tracker, server, forge)

    async def during(kwargs):
        if kwargs["output_format"]["schema"] == AUDIT_MANDATE_SCHEMA:
            raise asyncio.CancelledError

    executor.during = during
    with pytest.raises(asyncio.CancelledError):
        await build().run()
    assert workspace.calls[-1][0] == "release"
