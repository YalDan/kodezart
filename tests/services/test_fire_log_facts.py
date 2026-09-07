"""Observed terminal facts travel through the watcher into actual Notion properties."""

from datetime import datetime

import pytest

from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.agent import (
    ErrorEvent,
    WorkflowCompleteEvent,
    WorkflowScopeBaseEvent,
    WorkflowVisibilityEvent,
)
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_records import FireRecordFacts, RunIdentity
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
    return WorkflowCompleteEvent(
        feature_branch="feature",
        ralph_branch="ralph",
        total_iterations=3,
        accepted=True,
        outcome=WorkflowOutcome.pr_opened,
        pr_url=PR_EVENT.pr_url,
    )


def watch_over(server, events, *, writer=None, registry=None):
    tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE)])
    return LifecycleWatcher(
        queue=FakeJobQueue(events=events),
        registry=registry if registry is not None else registry_holding(),
        writer=writer
        or TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
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
    if completed:
        assert properties["Iterations"]["number"] == 3
    else:
        assert "Iterations" not in properties
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
        writer=BrokenTerminalWriter(tracker=tracker, gate=PassThroughGate()),
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
        writer=BrokenDequeueWriter(tracker=tracker, gate=PassThroughGate()),
    )
    watcher.follow(issue_key=ISSUE, job_id=JOB_ID, pre_claim_state=PRE_CLAIM_STATE)
    await watcher.drain()
    await watcher.record_unfinished()
    properties = next(iter(server.rows.values()))["properties"]
    assert properties["Repository"]["select"]["name"] == "owner/repo"
    assert "Iterations" not in properties
    assert "Base" not in properties


def test_unknown_and_observed_zero_iterations_are_distinct():
    assert FireRecordFacts().iterations is None
    assert FireRecordFacts(iterations=0).iterations == 0
