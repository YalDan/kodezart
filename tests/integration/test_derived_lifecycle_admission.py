"""The two composed derived comments retain their point-in-time admission."""

import pytest

from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.outcome import WorkflowOutcome
from tests.fakes import FakeTrackerPort, make_tracker_issue
from tests.integration.test_authored_aggregate_admission import (
    RecordedTextJudge,
    gate_with_judge,
)


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("privacy", [False, True])
@pytest.mark.parametrize("visibility", [RepoVisibility.PUBLIC, RepoVisibility.UNKNOWN])
async def test_existing_lifecycle_writers_keep_native_comments_without_a_text_session(
    tmp_path, failed, privacy, visibility
):
    issue = make_tracker_issue("sample/1")
    tracker = FakeTrackerPort(issues=[issue])
    judge = RecordedTextJudge("job-record-1", None)
    gate = await gate_with_judge(tmp_path, judge, privacy=privacy)
    writer = TrackerLifecycleWriter(
        marker_prefixes={"run_outcome": "fixture-outcome"},
        surface_lease_seconds=900,
        tracker=tracker,
        gate=gate,
    )
    if failed:
        await writer.on_run_failed(
            issue_key=issue.issue_key,
            job_id="job-record-1",
            pre_claim_state=issue.state_name,
            failure_class="RuntimeError",
            step=None,
            visibility=visibility,
        )
    else:
        await writer.on_terminal_outcome(
            issue_key=issue.issue_key,
            job_id="job-record-1",
            outcome=WorkflowOutcome.ci_passed,
            visibility=visibility,
        )
    (comment,) = tracker.comments
    assert comment.issue_key == issue.issue_key
    assert "job-record-1" in comment.body
    assert (
        "RuntimeError" if failed else WorkflowOutcome.ci_passed.value
    ) in comment.body
    assert judge.calls == []
