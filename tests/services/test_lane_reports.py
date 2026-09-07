"""Lost lanes remain visible and foreign verdicts cannot replace them."""

from itertools import permutations

import pytest
from pydantic import ValidationError

from kodezart.core.errors import LaneRosterArityError
from kodezart.services.lane_reports import LaneReportCollection, assert_lane_roster
from kodezart.types.domain.scope_terminal import LaneReport, LaneReportState


@pytest.mark.parametrize(
    ("name", "wire"),
    [
        ("CONVERGED", "converged"),
        ("IN_GAP", "in_gap"),
        ("HALTED", "halted"),
        ("UNREPORTED", "unreported"),
    ],
)
def test_lane_report_wire_and_frozen_identity(name: str, wire: str) -> None:
    assert LaneReportState[name].value == wire
    report = LaneReport(
        lane_key="left", issue_id="ISS-1", state=LaneReportState[name], detail="why"
    )
    assert report.model_dump(by_alias=True) == {
        "laneKey": "left",
        "issueId": "ISS-1",
        "state": wire,
        "detail": "why",
    }
    assert LaneReport.model_validate_json(report.model_dump_json()) == report
    with pytest.raises(ValidationError):
        report.issue_id = "ISS-other"


def test_silence_and_empty_gaps_have_distinct_complete_payloads() -> None:
    collection = LaneReportCollection(dispatched={"left": "ISS-1", "right": "ISS-2"})
    collection.record(
        lane_key="left",
        report=LaneReport(
            lane_key="left", issue_id="ISS-1", state=LaneReportState.CONVERGED
        ),
    )
    collection.record(lane_key="right", report=None)
    silent = collection.snapshot()
    assert [(r.lane_key, r.state) for r in silent] == [
        ("left", LaneReportState.CONVERGED),
        ("right", LaneReportState.UNREPORTED),
    ]
    collection.record(
        lane_key="right",
        report=LaneReport(
            lane_key="right", issue_id="ISS-2", state=LaneReportState.CONVERGED
        ),
    )
    clean = collection.snapshot()
    assert len(clean) == len(silent) == 2
    assert all(r.state is LaneReportState.CONVERGED for r in clean)
    assert silent[-1].state is LaneReportState.UNREPORTED
    assert [r.model_dump_json() for r in silent] != [r.model_dump_json() for r in clean]


@pytest.mark.parametrize("order", list(permutations(("a", "b", "c"))))
def test_result_arrival_order_never_changes_dispatch_order(
    order: tuple[str, ...],
) -> None:
    dispatched = {key: f"ISS-{key}" for key in ("a", "b", "c")}
    collection = LaneReportCollection(dispatched=dispatched)
    dispatched.clear()
    before = collection.snapshot()
    assert all(r.state is LaneReportState.UNREPORTED for r in before)
    for key in order:
        collection.record(
            lane_key=key,
            report=LaneReport(
                lane_key=key, issue_id=f"ISS-{key}", state=LaneReportState.IN_GAP
            ),
        )
    assert tuple(r.lane_key for r in collection.snapshot()) == ("a", "b", "c")
    collection.record(lane_key="b", report=None)
    after = collection.snapshot()
    assert len(after) == 3
    assert after[1].state is LaneReportState.UNREPORTED
    assert all(r.state is LaneReportState.UNREPORTED for r in before)


@pytest.mark.parametrize(
    "report",
    [
        LaneReport(lane_key="other", issue_id="ISS-1", state=LaneReportState.CONVERGED),
        LaneReport(lane_key="a", issue_id="ISS-2", state=LaneReportState.CONVERGED),
    ],
)
def test_wrong_identity_leaves_existing_collection_unchanged(
    report: LaneReport,
) -> None:
    collection = LaneReportCollection(dispatched={"a": "ISS-1"})
    before = collection.snapshot()
    with pytest.raises(ValueError, match="identity"):
        collection.record(lane_key="a", report=report)
    assert collection.snapshot() == before


def test_unknown_lane_cannot_extend_or_overwrite_the_roster() -> None:
    collection = LaneReportCollection(dispatched={"a": "ISS-1"})
    with pytest.raises(LaneRosterArityError) as raised:
        collection.record(lane_key="other", report=None)
    assert raised.value.dispatched_lane_keys == ("a",)
    assert raised.value.reported_lane_keys == ("other",)
    assert tuple(r.lane_key for r in collection.snapshot()) == ("a",)


@pytest.mark.parametrize(
    ("dispatched", "reported"),
    [
        (("a", "b"), ("a",)),
        (("a", "b"), ("a", "c")),
        (("a", "b"), ("a", "a")),
        (("a", "a"), ("a",)),
        (("a", "a"), ("a", "a")),
        ((), ("a",)),
    ],
)
def test_arity_is_exact_identity_coverage_not_count(
    dispatched: tuple[str, ...], reported: tuple[str, ...]
) -> None:
    with pytest.raises(LaneRosterArityError) as raised:
        assert_lane_roster(dispatched_lane_keys=dispatched, reported_lane_keys=reported)
    assert raised.value.dispatched_lane_keys == dispatched
    assert raised.value.reported_lane_keys == reported


def test_empty_and_reordered_complete_rosters_are_valid() -> None:
    assert LaneReportCollection(dispatched={}).snapshot() == ()
    assert_lane_roster(dispatched_lane_keys=(), reported_lane_keys=())
    assert_lane_roster(dispatched_lane_keys=("a", "b"), reported_lane_keys=("b", "a"))


@pytest.mark.parametrize("lane, issue", [("", "ISS-1"), (" ", "ISS-1"), ("a", "")])
def test_unaddressable_dispatch_refuses(lane: str, issue: str) -> None:
    with pytest.raises(ValidationError):
        LaneReportCollection(dispatched={lane: issue})
