"""Interleavings at the actual escalation writes preserve Organize authority."""

import pytest

from kodezart.domain.errors import OrganizeWriteRefusalError
from tests.chains.test_organize import result
from tests.chains.test_organize_owner import factory, run_owner
from tests.tracker.conftest import CLAIMED_ISSUE


@pytest.mark.parametrize("approve_after_comment", [False, True])
async def test_escalation_rechecks_approval_before_its_second_write(
    monkeypatch, approve_after_comment
):
    owner, board, executor = factory()
    original_stream = executor.stream
    original_call = board.call_tool
    comments = []

    async def human_decision(**kwargs):
        async for event in original_stream(**kwargs):
            if kwargs["output_format"]["schema"].get("title") == "AdmissionJudgment":
                event = result(
                    structured_output={
                        "issue_id": CLAIMED_ISSUE,
                        "verdict": "not_buildable",
                        "invented_decision": "Which source is authoritative?",
                        "evidence": "The current sources disagree.",
                        "refusal_kind": "human_decision",
                    }
                )
            yield event

    async def boundary(*, name, arguments):
        response = await original_call(name=name, arguments=arguments)
        if name == "save_comment" and '"interimReading"' in arguments.get("body", ""):
            comments.append(arguments)
            if approve_after_comment:
                board.server.issues[CLAIMED_ISSUE].labels.append("approved scope")
        return response

    monkeypatch.setattr(executor, "stream", human_decision)
    monkeypatch.setattr(board, "call_tool", boundary)
    try:
        report = await run_owner(owner)
        assert not approve_after_comment
        assert report.halt.cause == "human_decision"
    except OrganizeWriteRefusalError:
        assert approve_after_comment
    assert len(comments) == 1
    assert ("needs decision" in board.server.issues[CLAIMED_ISSUE].labels) is (
        not approve_after_comment
    )
    assert board.grants() == []
