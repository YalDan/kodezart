"""Supervisor collectors read both membership sets and actual criterion closure."""

import pytest

from kodezart.core.config import AppConfig
from kodezart.domain.errors import RunShapeReadError
from kodezart.services.mandate_graph import (
    observe_ruling_growth,
    observe_structural_write,
    read_lane_graph,
)
from kodezart.types.domain.run_alarm import AlarmSubject, AlarmSubjectKind
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.domain.test_mandate_graph import reading, ruling
from tests.fakes import FakeMcpIssue, FakeTrackerPort
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    CLAIMED_ISSUE,
    fixture_server,
    linear_over_fake_mcp,
)
from tests.tracker.test_scope_reads import (
    MILESTONE,
    ROOT,
    ScopeMcpIssue,
    ScopeMcpServer,
)


@pytest.fixture
def server():
    server = fixture_server()
    child = FakeMcpIssue(
        id="criterion/open",
        parent_id=APPROVED_ISSUE,
        labels=["acceptance-condition"],
        description="**Check:** A concrete condition.",
        status="Todo",
        status_type="unstarted",
    )
    server.issues[child.id] = child
    for name, kind in [("Canceled", "canceled"), ("Duplicate", "duplicate")]:
        server.issues[name] = FakeMcpIssue(id=name, status=name, status_type=kind)
    return server


def arguments():
    snapshot = {"laneKey": "lane", "issueKeys": [APPROVED_ISSUE], "rulings": []}
    current = {
        **snapshot,
        "rulings": [
            ruling("a", issue=APPROVED_ISSUE),
            ruling("b", issue=APPROVED_ISSUE),
        ],
    }
    return {
        "config": AppConfig(_env_file=None, run_alarm_max_rulings_without_closure=1),
        "subject": AlarmSubject(
            kind=AlarmSubjectKind.LANE, scope_key="scope", lane_key="lane"
        ),
        "baseline_rulings": reading(snapshot),
        "current_rulings": reading(current),
        "previous_open": reading(["criterion/open"]),
        "supersession_refs": {},
        "raised_at_sha": "head",
        "raised_by": "holder",
    }


async def test_ruling_service_uses_current_criterion_state_and_actual_config(
    tracker, tracker_writes
):
    before = tracker_writes()
    alarm = await observe_ruling_growth(tracker=tracker, **arguments())
    assert alarm is not None
    assert alarm.bound.configured_value == 1
    assert alarm.bound.observed_value == 2
    assert tracker_writes() == before
    await tracker.restore_workflow_state(issue_key="criterion/open", state_name="Done")
    before = tracker_writes()
    assert await observe_ruling_growth(tracker=tracker, **arguments()) is None
    assert tracker_writes() == before


@pytest.mark.parametrize("change", ["missing", "unlabelled", "reparented"])
async def test_disappearing_obligation_never_manufactures_closure(
    tracker, server, change
):
    if isinstance(tracker, FakeTrackerPort):
        if change == "missing":
            del tracker.issues["criterion/open"]
        else:
            update = (
                {"issue_labels": frozenset()}
                if change == "unlabelled"
                else {"parent_key": "elsewhere"}
            )
            tracker.issues["criterion/open"] = tracker.issues[
                "criterion/open"
            ].model_copy(update=update)
    elif change == "missing":
        del server.issues["criterion/open"]
    elif change == "unlabelled":
        server.issues["criterion/open"].labels = []
    else:
        server.issues["criterion/open"].parent_id = "elsewhere"
    assert await observe_ruling_growth(tracker=tracker, **arguments()) is not None


@pytest.mark.parametrize("state", ["Canceled", "Duplicate"])
async def test_supersession_is_required_for_canceled_criterion_closure(tracker, state):
    await tracker.restore_workflow_state(issue_key="criterion/open", state_name=state)
    assert await observe_ruling_growth(tracker=tracker, **arguments()) is not None
    kwargs = arguments()
    kwargs["supersession_refs"] = {"criterion/open": "recorded/successor"}
    assert await observe_ruling_growth(tracker=tracker, **kwargs) is None


async def test_closure_on_another_declared_lane_issue_is_observed(tracker, server):
    child = FakeMcpIssue(
        id="other/criterion",
        parent_id=CLAIMED_ISSUE,
        labels=["acceptance-condition"],
        description="**Check:** Another condition.",
        status="Done",
        status_type="completed",
    )
    server.issues[child.id] = child
    if isinstance(tracker, FakeTrackerPort):
        tracker.issues[child.id] = await linear_over_fake_mcp(server).read_issue(
            issue_key=child.id
        )
    kwargs = arguments()
    snapshot = {
        "laneKey": "lane",
        "issueKeys": [APPROVED_ISSUE, CLAIMED_ISSUE],
        "rulings": [
            ruling("a", issue=APPROVED_ISSUE),
            ruling("b", issue=CLAIMED_ISSUE),
        ],
    }
    kwargs["current_rulings"] = reading(snapshot)
    kwargs["previous_open"] = reading(["other/criterion"])
    assert await observe_ruling_growth(tracker=tracker, **kwargs) is None


@pytest.fixture(params=["linear", "fake"])
async def graph_fixture(request):
    server = ScopeMcpServer()
    for issue in server.issues.values():
        issue.status = "Done"
        issue.status_type = "completed"
    real = linear_over_fake_mcp(server)
    if request.param == "linear":
        return real, server
    fake = FakeTrackerPort()
    # Hydrate the exact domain membership fields and seed the explicit scope.
    for issue in await real.scope_issues(ref=MILESTONE):
        fake.issues[issue.issue_key] = issue
    for issue in await real.scope_issues(ref=ROOT):
        fake.issues[issue.issue_key] = issue
    fake.scope_memberships[MILESTONE] = tuple(
        issue.issue_key for issue in await real.scope_issues(ref=MILESTONE)
    )
    return fake, server


async def previous_graph(tracker):
    return await read_lane_graph(
        tracker=tracker,
        lane_key="lane",
        fire_key=ROOT.key,
        milestone=MILESTONE,
        supersession_refs={},
        source_ref="graph/record",
    )


async def observe_graph(tracker, previous):
    return await observe_structural_write(
        tracker=tracker,
        subject=AlarmSubject(
            kind=AlarmSubjectKind.LANE, scope_key="scope", lane_key="lane"
        ),
        previous=previous,
        fire_key=ROOT.key,
        milestone=MILESTONE,
        supersession_refs={},
        raised_at_sha="head",
        raised_by="holder",
    )


@pytest.mark.parametrize("attachment", ["subtree", "milestone"])
async def test_real_membership_collector_detects_structural_addition(
    graph_fixture, attachment
):
    tracker, server = graph_fixture
    previous = await previous_graph(tracker)
    assert await observe_graph(tracker, previous) is None
    added = ScopeMcpIssue(
        id="NEW",
        parent_id=ROOT.key if attachment == "subtree" else None,
        milestone_key=None if attachment == "subtree" else MILESTONE.key,
        status="Todo",
        status_type="unstarted",
    )
    server.issues[added.id] = added
    if isinstance(tracker, FakeTrackerPort):
        tracker.issues[added.id] = await linear_over_fake_mcp(server).read_issue(
            issue_key=added.id
        )
        if attachment == "milestone":
            tracker.scope_memberships[MILESTONE] += (added.id,)
    before = len(server.calls)
    alarm = await observe_graph(tracker, previous)
    assert alarm is not None
    assert all(
        name not in {"save_issue", "save_comment"} for name, _ in server.calls[before:]
    )
    if isinstance(tracker, FakeTrackerPort):
        assert not tracker.issue_writes and not tracker.comment_writes
    else:
        lists = [args for name, args in server.calls[before:] if name == "list_issues"]
        assert lists
        assert all(args["includeArchived"] is True for args in lists)
        assert any("parentId" in args for args in lists)


async def test_collector_state_only_reopening_stays_quiet(graph_fixture):
    tracker, server = graph_fixture
    previous = await previous_graph(tracker)
    if isinstance(tracker, FakeTrackerPort):
        tracker.issues["FIX-2"] = tracker.issues["FIX-2"].model_copy(
            update={
                "state_kind": WorkflowStateKind.STARTED,
                "state_name": "In Progress",
            }
        )
    else:
        server.issues["FIX-2"].status = "In Progress"
        server.issues["FIX-2"].status_type = "started"
    assert await observe_graph(tracker, previous) is None


async def test_collector_refuses_missing_fire_membership(graph_fixture):
    tracker, server = graph_fixture
    if isinstance(tracker, FakeTrackerPort):
        tracker.scope_memberships[MILESTONE] = ("FIX-2",)
    else:
        server.issues[ROOT.key].milestone_key = None
    with pytest.raises(RunShapeReadError):
        await previous_graph(tracker)
