"""Compare a dispatched lane roster with a reported one by identity."""

from collections.abc import Sequence

from kodezart.core.errors import LaneRosterArityError


def assert_lane_roster(
    *, dispatched_lane_keys: Sequence[str], reported_lane_keys: Sequence[str]
) -> None:
    """Compare identities, including duplicate detection, rather than counts."""
    dispatched = tuple(dispatched_lane_keys)
    reported = tuple(reported_lane_keys)
    if (
        len(set(dispatched)) != len(dispatched)
        or len(set(reported)) != len(reported)
        or set(dispatched) != set(reported)
    ):
        raise LaneRosterArityError(
            dispatched_lane_keys=dispatched,
            reported_lane_keys=reported,
        )
