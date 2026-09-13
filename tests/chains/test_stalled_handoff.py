"""The actual stalled fire hands off the same published head as its PR."""

import pytest

from kodezart.types.domain.agent import WorkflowCompleteEvent, WorkflowPREvent
from kodezart.types.domain.consolidation import (
    ConsolidationOutcome,
    ConsolidationStatus,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from tests.chains.test_ralph_workflow import _PEAK_SHA, _stalled_run
from tests.fakes import FakeBranchMerger, FakePRCreator, FakeRefPublisher


@pytest.mark.parametrize(
    "status,expected_sha",
    [
        (ConsolidationStatus.FAST_FORWARDED, _PEAK_SHA),
        (ConsolidationStatus.ALREADY_INTEGRATED, "a" * 40),
        (ConsolidationStatus.DIVERGENT, _PEAK_SHA),
    ],
)
async def test_stalled_terminal_and_pr_identify_the_same_published_head(
    status, expected_sha
):
    forge = FakePRCreator()
    publisher = FakeRefPublisher()
    events = await _stalled_run(
        pr_creator=forge,
        ref_publisher=publisher,
        merger=FakeBranchMerger(
            consolidation_outcomes=[
                ConsolidationOutcome(
                    status=status,
                    feature_tip_sha="a" * 40
                    if status is ConsolidationStatus.DIVERGENT
                    else expected_sha,
                )
            ]
        ),
    )
    pr = next(event for event in events if isinstance(event, WorkflowPREvent))
    terminal = next(
        event for event in events if isinstance(event, WorkflowCompleteEvent)
    )
    created = next(call for call in forge.calls if call["method"] == "create_pr")
    assert terminal.feature_branch == pr.feature_branch == created["head"]
    assert terminal.final_commit_sha == pr.feature_tip_sha == expected_sha
    assert terminal.outcome is WorkflowOutcome.stalled_pr_opened
    assert terminal.accepted is False
    assert pr.delivered is False
    if status is ConsolidationStatus.DIVERGENT:
        assert terminal.feature_branch == publisher.calls[0]["ref"]
        assert terminal.final_commit_sha == publisher.calls[0]["commit_sha"]
