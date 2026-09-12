"""Full recorded authorship feeds the existing ruling and closure observation."""

import json
from unittest.mock import AsyncMock

import pytest

from kodezart.core.config import AppConfig
from kodezart.domain.errors import RulingRecordReadError, RunShapeReadError
from kodezart.domain.mandate_graph import rulings_outpace_closures
from kodezart.domain.rulings import render_ruling
from kodezart.services.mandate_graph import (
    observe_recorded_ruling_growth,
    read_lane_rulings,
)
from kodezart.types.domain.agent import Ruling
from kodezart.types.domain.mandate_graph import LaneRulingSnapshot
from kodezart.types.domain.run_alarm import AlarmReading, AlarmSubject, AlarmSubjectKind
from tests.domain.test_rulings import LANE, PREFIXES, ruling_data
from tests.fakes import FakeMcpIssue
from tests.tracker.conftest import APPROVED_ISSUE, CLAIMED_ISSUE, fixture_server
from tests.tracker.lease_fixtures import leased_comment
from tests.tracker.test_ruling_records import OPERATION, seed

KEYS = (APPROVED_ISSUE, CLAIMED_ISSUE)
SOURCE = "retained-window/native-key"


@pytest.fixture
def server():
    server = fixture_server()
    server.issues["criterion/open"] = FakeMcpIssue(
        id="criterion/open",
        parent_id=CLAIMED_ISSUE,
        labels=["acceptance-condition"],
        description="**Check:** A concrete condition.",
        status="Todo",
        status_type="unstarted",
    )
    return server


async def capture(tracker, **changes):
    return await read_lane_rulings(
        tracker=tracker,
        operation=OPERATION,
        **{"lane_key": LANE, "issue_keys": KEYS, "source_ref": SOURCE, **changes},
    )


async def observe(tracker, baseline, **changes):
    values = {
        "operation": OPERATION,
        "config": AppConfig(_env_file=None, run_alarm_max_rulings_without_closure=1),
        "subject": AlarmSubject(
            kind=AlarmSubjectKind.LANE, scope_key="scope", lane_key=LANE
        ),
        "issue_keys": KEYS,
        "baseline_rulings": baseline,
        "previous_open": AlarmReading(source_ref=SOURCE, value='["criterion/open"]'),
        "supersession_refs": {},
        "raised_at_sha": "supervisor-sha",
        "raised_by": "supervisor-holder",
    }
    values.update(changes)
    return await observe_recorded_ruling_growth(tracker=tracker, **values)


async def write_on_second_issue(tracker, *, author="machine"):
    ruling = Ruling.model_validate(
        ruling_data(issue_ref=CLAIMED_ISSUE, authored_by=author)
    )
    body = render_ruling(ruling=ruling, lane_key=LANE, marker_prefixes=PREFIXES)
    marker, content = body.split("\n", 1)
    await leased_comment(tracker, target=CLAIMED_ISSUE, marker=marker, body=content)
    return ruling


async def test_actual_records_on_all_lane_issues_raise_replayable_alarm_without_writes(
    tracker, tracker_writes
):
    baseline = await capture(tracker)
    _, first = await seed(tracker)
    second = await write_on_second_issue(tracker)
    before = tracker_writes()
    alarm = await observe(tracker, baseline)
    assert alarm is not None
    assert alarm.bound.observed_value == 2
    assert alarm.bound.configured_value == 1
    assert alarm.raised_at_sha == "supervisor-sha"
    assert alarm.raised_by == "supervisor-holder"
    current = LaneRulingSnapshot.model_validate_json(alarm.readings[1].value)
    assert current.issue_keys == KEYS
    assert tuple(row.ruling_id for row in current.rulings) == (
        first.ruling_id,
        second.ruling_id,
    )
    assert all(row.authored_by.value == "machine" for row in current.rulings)
    assert tracker_writes() == before
    assert (
        rulings_outpace_closures(
            subject=alarm.subject,
            readings=alarm.readings,
            raised_at_sha=alarm.raised_at_sha,
            raised_by=alarm.raised_by,
        )
        == alarm
    )


@pytest.mark.parametrize(
    "quiet", ["principal", "replay", "amendment", "closure", "higher-bound"]
)
async def test_native_record_quiet_controls_preserve_explicit_authorship_and_closure(
    tracker, quiet
):
    if quiet in {"replay", "amendment"}:
        await seed(tracker)
    baseline = await capture(tracker)
    await seed(
        tracker,
        resolution="Corrected answer"
        if quiet == "amendment"
        else "Use the observable reading.",
    )
    await write_on_second_issue(
        tracker, author="principal" if quiet == "principal" else "machine"
    )
    values = {}
    if quiet == "closure":
        await tracker.restore_workflow_state(
            issue_key="criterion/open", state_name="Done"
        )
    elif quiet == "higher-bound":
        values["config"] = AppConfig(
            _env_file=None, run_alarm_max_rulings_without_closure=2
        )
    assert await observe(tracker, baseline, **values) is None


async def test_missing_record_authorship_never_borrows_transport_account(tracker):
    baseline = await capture(tracker)
    first, _ = await seed(tracker)
    body = first.body.replace(',\n  "authoredBy": "machine"', "")
    marker, content = body.split("\n", 1)
    await leased_comment(tracker, target=APPROVED_ISSUE, marker=marker, body=content)
    with pytest.raises(RulingRecordReadError, match="malformed"):
        await observe(tracker, baseline)


@pytest.mark.parametrize(
    "changes",
    [
        {"issue_keys": (APPROVED_ISSUE, APPROVED_ISSUE)},
        {"issue_keys": ("",)},
        {"lane_key": ""},
        {"source_ref": ""},
    ],
)
async def test_invalid_membership_and_window_identity_refuse_before_reads(
    tracker, monkeypatch, changes
):
    listing = AsyncMock()
    monkeypatch.setattr(tracker, "list_comments", listing)
    with pytest.raises(RunShapeReadError):
        await capture(tracker, **changes)
    listing.assert_not_called()


async def test_nonlane_subject_refuses_before_record_read(tracker, monkeypatch):
    baseline = await capture(tracker)
    listing = AsyncMock()
    monkeypatch.setattr(tracker, "list_comments", listing)
    with pytest.raises(RunShapeReadError, match="lane subject"):
        await observe(
            tracker,
            baseline,
            subject=AlarmSubject(
                kind=AlarmSubjectKind.ISSUE, scope_key="scope", issue_id=APPROVED_ISSUE
            ),
        )
    listing.assert_not_called()


async def test_the_retained_baseline_is_not_rebuilt_from_current_records(tracker):
    baseline = await capture(tracker)
    await seed(tracker)
    await write_on_second_issue(tracker)
    assert json.loads(baseline.value)["rulings"] == []
    assert await observe(tracker, baseline) is not None
    later = await capture(tracker)
    assert await observe(tracker, later) is None
