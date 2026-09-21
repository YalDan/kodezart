"""Observed terminal facts travel through the watcher into actual Notion properties."""

from datetime import datetime

import pytest

from kodezart.services.fire_record_facts import observe_fire_facts
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    AuthoredWorkflowCompleteEvent,
    CriterionResult,
    ErrorEvent,
    WorkflowIterationEvent,
    WorkflowScopeBaseEvent,
    WorkflowVisibilityEvent,
)
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_records import FireRecordFacts, RunIdentity
from kodezart.types.domain.trajectory import IterationRecord, LoopTrajectory
from tests.fakes import (
    FakeFireReport,
    FakeJobQueue,
    FakeTrackerPort,
    PassThroughGate,
    make_tracker_issue,
)
from tests.probes.notion_records import NotionLogServer
from tests.services.test_lifecycle_watcher import (
    ISSUE,
    JOB_ID,
    PR_EVENT,
    PRE_CLAIM_STATE,
    REPO_URL,
    claim_heartbeat,
    registry_holding,
)
from tests.services.test_structured_run_records import destination, recorder


def observed_events():
    return [
        WorkflowVisibilityEvent(visibility=RepoVisibility.PUBLIC, repo_url=REPO_URL),
        WorkflowScopeBaseEvent(base_branch="release/base", base_role=None),
        PR_EVENT,
    ]


def terminal():
    return AuthoredWorkflowCompleteEvent(
        feature_branch="feature",
        ralph_branch="ralph",
        total_iterations=3,
        accepted=True,
        outcome=WorkflowOutcome.pr_opened,
        pr_url=PR_EVENT.pr_url,
    )


def iteration(number: int) -> WorkflowIterationEvent:
    """One iteration event, with the minimum the model requires.

    The trajectory is stated rather than folded, so no fold semantics ride
    into a case about what the watcher observed.
    """
    return WorkflowIterationEvent(
        iteration=number,
        branch="ralph",
        verdict=AcceptVerdict.rejected,
        evaluation=AcceptanceCriteriaOutput(
            criteria_results=[
                CriterionResult(
                    criterion_id="AC-1",
                    criterion="a check",
                    passed=False,
                    reasoning="observed",
                )
            ]
        ),
        trajectory=LoopTrajectory(
            records=[
                IterationRecord(
                    iteration=number, passed_count=0, failing_criterion_ids=["AC-1"]
                )
            ],
            never_passed_ids=["AC-1"],
            best_passed_count=0,
            best_iteration=number,
            plateaued=False,
        ),
    )


def watch_over(server, events, *, writer=None, registry=None):
    tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
    return LifecycleWatcher(
        queue=FakeJobQueue(events=events),
        registry=registry if registry is not None else registry_holding(),
        writer=writer
        or TrackerLifecycleWriter(
            marker_prefixes={"run_outcome": "fixture-outcome"},
            surface_lease_seconds=900,
            tracker=tracker,
            gate=PassThroughGate(),
        ),
        heartbeat=claim_heartbeat(tracker),
        recorder=recorder(
            server,
            destination(
                options={
                    "workflow.pr_opened": "PR opened",
                    "run.failed": "Failed",
                    "run.never_started": "Failed",
                }
            ),
        ),
        report=FakeFireReport(),
    )


@pytest.mark.parametrize("completed", [True, False])
@pytest.mark.parametrize("session_row", [True, False])
async def test_completed_and_failed_fires_fill_structured_columns(
    completed, session_row
):
    server = NotionLogServer()
    registry = registry_holding()
    identity = RunIdentity(
        kind=RunKind.FIRE, name=ISSUE, started_at=registry.records[JOB_ID].submitted_at
    )
    narrative = {"rich_text": [{"plain_text": "I inspected the failed check."}]}
    if session_row:
        server.seed(identity.title(), properties={"What happened": narrative})
    events = observed_events()
    # A loop whose local counter reset makes "highest" and "last" differ, so
    # the repeated 1 is what distinguishes the two readings inside the
    # watcher arm as well as in the fold test below.
    events.extend([iteration(1), iteration(2), iteration(1)])
    events.append(
        terminal()
        if completed
        else ErrorEvent(error="stopped", error_kind="RuntimeError")
    )
    watcher = watch_over(server, events, registry=registry)
    await watcher.watch(issue_key=ISSUE, job_id=JOB_ID, pre_claim_state=PRE_CLAIM_STATE)
    assert len(server.rows) == 1
    row = next(iter(server.rows.values()))
    properties = row["properties"]
    assert properties["Run"]["title"] == [{"plain_text": identity.title()}]
    assert properties["Disposition"]["select"]["name"] == (
        "PR opened" if completed else "Failed"
    )
    assert properties["Repository"]["select"]["name"] == "owner/repo"
    assert properties["Pull request"]["url"] == PR_EVENT.pr_url
    assert properties["Base"]["rich_text"] == [{"plain_text": "release/base"}]
    began = datetime.fromisoformat(properties["Began"]["date"]["start"])
    ended = datetime.fromisoformat(properties["Ended"]["date"]["start"])
    assert began == identity.started_at
    assert ended >= began
    assert properties["Minutes"]["number"] == pytest.approx(
        (ended - began).total_seconds() / 60
    )
    # The row carries the facts the run made known. A completed run's number
    # is the loop's own total, which keeps its authority over the highest
    # iteration the watcher saw go by (3 over the observed 2); a failed fire
    # that observed an iteration knows how far it got. One that observed none
    # writes no column at all, which the two cases below keep pinning.
    assert properties["Iterations"]["number"] == (3 if completed else 2)
    if session_row:
        assert properties["What happened"] == narrative
        assert server.writes()[0][0] == "API-patch-page"
    else:
        assert "What happened" not in properties
        assert server.writes()[0][0] == "API-post-page"


async def test_never_started_fire_does_not_invent_repo_pr_base_or_zero_iterations():
    server = NotionLogServer()
    watcher = watch_over(server, [])
    await watcher.watch(issue_key=ISSUE, job_id=JOB_ID, pre_claim_state=PRE_CLAIM_STATE)
    properties = next(iter(server.rows.values()))["properties"]
    assert set(properties) == {"Run", "Disposition", "Began", "Ended", "Minutes"}


async def test_terminal_facts_survive_a_later_lifecycle_write_failure():
    class BrokenTerminalWriter(TrackerLifecycleWriter):
        async def on_terminal_outcome(self, **kwargs):
            raise RuntimeError("terminal tracker write refused")

    server = NotionLogServer()
    tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
    watcher = watch_over(
        server,
        [*observed_events(), terminal()],
        writer=BrokenTerminalWriter(
            tracker=tracker,
            gate=PassThroughGate(),
            marker_prefixes={"run_outcome": "fixture-outcome"},
            surface_lease_seconds=900,
        ),
    )
    watcher.follow(issue_key=ISSUE, job_id=JOB_ID, pre_claim_state=PRE_CLAIM_STATE)
    await watcher.drain()
    assert server.rows == {}
    await watcher.record_unfinished()
    properties = next(iter(server.rows.values()))["properties"]
    assert properties["Disposition"]["select"]["name"] == "PR opened"
    assert properties["Iterations"]["number"] == 3
    assert properties["Repository"]["select"]["name"] == "owner/repo"
    assert properties["Base"]["rich_text"] == [{"plain_text": "release/base"}]


async def test_observation_precedes_a_first_frame_lifecycle_failure():
    class BrokenDequeueWriter(TrackerLifecycleWriter):
        async def on_dequeue(self, **kwargs):
            raise RuntimeError("dequeue tracker write refused")

    server = NotionLogServer()
    tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
    watcher = watch_over(
        server,
        observed_events(),
        writer=BrokenDequeueWriter(
            tracker=tracker,
            gate=PassThroughGate(),
            marker_prefixes={"run_outcome": "fixture-outcome"},
            surface_lease_seconds=900,
        ),
    )
    watcher.follow(issue_key=ISSUE, job_id=JOB_ID, pre_claim_state=PRE_CLAIM_STATE)
    await watcher.drain()
    await watcher.record_unfinished()
    properties = next(iter(server.rows.values()))["properties"]
    assert properties["Repository"]["select"]["name"] == "owner/repo"
    assert "Iterations" not in properties
    assert "Base" not in properties


def test_iterations_are_the_highest_observed_until_completion_states_the_total() -> (
    None
):
    """Unknown is never coerced to a number, and a later lower one is not it."""
    facts = FireRecordFacts()
    assert facts.iterations is None
    facts = observe_fire_facts(facts, iteration(2))
    assert facts.iterations == 2
    facts = observe_fire_facts(facts, iteration(1))
    assert facts.iterations == 2
    assert observe_fire_facts(facts, terminal()).iterations == 3


def test_unknown_and_observed_zero_iterations_are_distinct():
    assert FireRecordFacts().iterations is None
    assert FireRecordFacts(iterations=0).iterations == 0
