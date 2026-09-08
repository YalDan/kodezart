"""The existing dispatcher retains unknown-landing base records and refusals."""

import pytest

from kodezart.types.domain.branch import BaseSpec, WorkRef, WorkRefLanding, WorkRefRole
from kodezart.types.domain.dispatch import DispatchOutcome
from kodezart.types.domain.operation import QueueState
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import (
    FIXTURE_EPOCH,
    FakeGitService,
    FakeTrackerPort,
    make_tracker_issue,
)
from tests.services.test_fire_dispatcher import BLOCKER_SHA, TRUNK, dispatcher


def recorded(key, branch, landing=WorkRefLanding.UNKNOWN):
    return WorkRef(
        issue_id=key,
        role=WorkRefRole.DELIVERABLE,
        branch=branch,
        pushed_head_sha=BLOCKER_SHA,
        landing=landing,
        recorded_at=FIXTURE_EPOCH,
    )


def tracker_with(refs):
    return FakeTrackerPort(
        issues=[
            make_tracker_issue("K-1", blocked_by=list(refs)),
            *(
                make_tracker_issue(
                    key,
                    queue_states=[QueueState.DONE],
                    state_kind=WorkflowStateKind.COMPLETED,
                )
                for key in refs
            ),
        ],
        recorded_work_refs={key: [value] for key, value in refs.items()},
    )


CASES = {
    "none": ({}, (), {"inputs": [], "baseBranch": TRUNK, "baseRole": None}),
    "one": (
        {"K-2": "feature/a"},
        (),
        {
            "inputs": [
                {"blockerIssueId": "K-2", "branch": "feature/a", "sha": BLOCKER_SHA}
            ],
            "baseBranch": "feature/a",
            "baseRole": "deliverable",
        },
    ),
    "duplicate": (
        {"K-3": "feature/a", "K-2": "feature/a"},
        (),
        {
            "inputs": [
                {"blockerIssueId": "K-2", "branch": "feature/a", "sha": BLOCKER_SHA}
            ],
            "baseBranch": "feature/a",
            "baseRole": "deliverable",
        },
    ),
    "contained": (
        {"K-2": "feature/a", "K-3": "feature/b"},
        (("feature/a", "feature/b"),),
        {
            "inputs": [
                {"blockerIssueId": "K-3", "branch": "feature/b", "sha": BLOCKER_SHA}
            ],
            "baseBranch": "feature/b",
            "baseRole": "deliverable",
        },
    ),
    "independent": (
        {"K-3": "feature/b", "K-2": "feature/a"},
        (),
        {
            "inputs": [
                {"blockerIssueId": "K-2", "branch": "feature/a", "sha": BLOCKER_SHA},
                {"blockerIssueId": "K-3", "branch": "feature/b", "sha": BLOCKER_SHA},
            ],
            "baseBranch": (
                "K-1-integration-"
                "b14d58fa72081551a1265ff610224d3f77549219c624510892f50d5a58aca764"
            ),
            "baseRole": "integration",
        },
    ),
}


@pytest.mark.parametrize("case", CASES)
async def test_unknown_present_preserves_the_prechange_dispatch_record(case):
    branches, containment, expected = CASES[case]
    tracker = tracker_with(
        {key: recorded(key, branch) for key, branch in branches.items()}
    )
    git = FakeGitService(
        ancestor_pairs=containment,
        remote_branch_shas=dict.fromkeys(branches.values(), BLOCKER_SHA),
    )
    fire, queue, _ = dispatcher(tracker, git=git)
    result = await fire.run_pass()
    assert result.outcome is DispatchOutcome.fire_enqueued
    assert result.base == BaseSpec.model_validate(expected)
    assert (await tracker.read_base_spec(issue_key="K-1")) == result.base
    assert len(queue.submissions) == 1
    assert queue.submissions[0][1].base_spec == result.base
    assert queue.submissions[0][1].implied_base == result.base
    assert result.superseded_base is None
