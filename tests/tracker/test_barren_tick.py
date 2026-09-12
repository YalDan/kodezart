"""Current criterion closure is read without reading the repository."""

import ast
import inspect

import pytest

from kodezart.core.config import AppConfig
from kodezart.domain.errors import CriterionReadError
from kodezart.domain.run_shape import barren_tick_with_diff_growth
from kodezart.services import run_shape
from kodezart.services.run_shape import observe_barren_tick, read_barren_tick
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    CountEvidence,
    ReferencesEvidence,
)
from tests.fakes import FakeMcpIssue, FakeTrackerPort
from tests.tracker.conftest import APPROVED_ISSUE, fixture_server

OLD = "previous/criterion"
NEW = "newly-closed/criterion"


@pytest.fixture
def server():
    server = fixture_server()
    values = [
        FakeMcpIssue(id="canceled-state", status="Canceled", status_type="canceled"),
        FakeMcpIssue(id="duplicate-state", status="Duplicate", status_type="duplicate"),
        FakeMcpIssue(
            id=OLD,
            parent_id=APPROVED_ISSUE,
            labels=["acceptance-condition"],
            description="**Check:** A runnable condition.",
            status="Todo",
            status_type="unstarted",
        ),
        FakeMcpIssue(
            id=NEW,
            parent_id=APPROVED_ISSUE,
            labels=["acceptance-condition"],
            description="**Check:** A newly completed condition.",
            status="Done",
            status_type="completed",
        ),
    ]
    server.issues.update({issue.id: issue for issue in values})
    return server


def inputs():
    return {
        "scope_key": "scope-a",
        "lane_key": "lane-a",
        "issue_key": APPROVED_ISSUE,
        "previous_open": AlarmReading(
            source_ref="previous-tick#open",
            value=ReferencesEvidence(value=("previous/criterion",)),
        ),
        "files_changed": AlarmReading(
            source_ref="lane-record#files", value=CountEvidence(value=11), at_sha="head"
        ),
        "commits_ahead": AlarmReading(
            source_ref="lane-record#commits",
            value=CountEvidence(value=6),
            at_sha="head",
        ),
        "supersession_refs": {},
        "raised_at_sha": "head",
        "raised_by": "supervisor",
    }


@pytest.mark.parametrize(
    "files_limit,commits_limit,expected_field,observed",
    [
        (10, 6, "run_alarm_barren_tick_max_files_changed", 11),
        (11, 5, "run_alarm_barren_tick_max_commits_ahead", 6),
    ],
)
async def test_current_tracker_closure_and_actual_config_feed_each_arm(
    tracker, tracker_writes, files_limit, commits_limit, expected_field, observed
):
    config = AppConfig(
        _env_file=None,
        run_alarm_barren_tick_max_files_changed=files_limit,
        run_alarm_barren_tick_max_commits_ahead=commits_limit,
    )
    before = tracker_writes()
    alarm = await observe_barren_tick(tracker=tracker, config=config, **inputs())
    assert alarm is not None
    assert alarm.bound.config_field == expected_field
    assert alarm.bound.configured_value == getattr(config, expected_field)
    assert alarm.bound.observed_value == observed
    assert tracker_writes() == before
    assert (
        barren_tick_with_diff_growth(
            subject=alarm.subject,
            readings=alarm.readings,
            raised_at_sha=alarm.raised_at_sha,
            raised_by=alarm.raised_by,
        )
        == alarm
    )


async def test_current_done_state_closes_the_same_previously_open_key(
    tracker, tracker_writes
):
    config = AppConfig(_env_file=None)
    assert await observe_barren_tick(tracker=tracker, config=config, **inputs())
    await tracker.restore_workflow_state(issue_key=OLD, state_name="Done")
    before = tracker_writes()
    assert await observe_barren_tick(tracker=tracker, config=config, **inputs()) is None
    assert tracker_writes() == before


@pytest.mark.parametrize("state", ["Canceled", "Duplicate"])
async def test_cancellation_closes_only_with_an_established_supersession(
    tracker, state
):
    await tracker.restore_workflow_state(issue_key=OLD, state_name=state)
    config = AppConfig(_env_file=None)
    arguments = inputs()
    assert await observe_barren_tick(tracker=tracker, config=config, **arguments)
    arguments["supersession_refs"] = {OLD: "recorded/successor"}
    assert (
        await observe_barren_tick(tracker=tracker, config=config, **arguments) is None
    )


@pytest.mark.parametrize("change", ["missing", "unlabelled", "reparented"])
async def test_disappearing_from_current_criteria_never_manufactures_closure(
    tracker, server, change
):
    if isinstance(tracker, FakeTrackerPort):
        if change == "missing":
            del tracker.issues[OLD]
        else:
            updates = (
                {"issue_labels": frozenset()}
                if change == "unlabelled"
                else {"parent_key": NEW}
            )
            tracker.issues[OLD] = tracker.issues[OLD].model_copy(update=updates)
    elif change == "missing":
        del server.issues[OLD]
    elif change == "unlabelled":
        server.issues[OLD].labels = []
    else:
        server.issues[OLD].parent_id = NEW
    assert await observe_barren_tick(
        tracker=tracker, config=AppConfig(_env_file=None), **inputs()
    )


async def test_failed_criterion_read_is_never_an_empty_or_closed_set(tracker, server):
    if isinstance(tracker, FakeTrackerPort):
        del tracker.issues[APPROVED_ISSUE]
    else:
        server._tool_errors["get_issue"] = "unreachable"
    with pytest.raises(CriterionReadError):
        await observe_barren_tick(
            tracker=tracker, config=AppConfig(_env_file=None), **inputs()
        )


def test_observer_has_one_tracker_read_and_no_version_control_dependency():
    wrapper = ast.parse(inspect.getsource(observe_barren_tick))
    assert {
        ast.unparse(node.func)
        for node in ast.walk(wrapper)
        if isinstance(node, ast.Call)
    } == {"read_barren_tick"}
    tree = ast.parse(inspect.getsource(read_barren_tick))
    tracker_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "tracker"
    }
    assert tracker_calls == {"read_criteria"}
    imports = {
        node.module
        for node in ast.walk(ast.parse(inspect.getsource(run_shape)))
        if isinstance(node, ast.ImportFrom)
    }
    assert imports == {
        "collections.abc",
        "kodezart.core.config",
        "kodezart.core.protocols",
        "kodezart.domain.gap",
        "kodezart.domain.run_shape",
        "kodezart.types.domain.escalation",
        "kodezart.types.domain.run_alarm",
        "kodezart.types.domain.tracker",
    }
    assert set(inspect.signature(observe_barren_tick).parameters) == {
        "tracker",
        "config",
        "scope_key",
        "lane_key",
        "issue_key",
        "previous_open",
        "files_changed",
        "commits_ahead",
        "supersession_refs",
        "raised_at_sha",
        "raised_by",
    }
    assert set(inspect.signature(read_barren_tick).parameters) == set(
        inspect.signature(observe_barren_tick).parameters
    )
