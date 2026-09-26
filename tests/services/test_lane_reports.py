"""A dispatched roster and a reported one match by identity, not by count."""

import pytest

from kodezart.core.errors import LaneRosterArityError
from kodezart.services.lane_reports import assert_lane_roster


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
    assert_lane_roster(dispatched_lane_keys=(), reported_lane_keys=())
    assert_lane_roster(dispatched_lane_keys=("a", "b"), reported_lane_keys=("b", "a"))
