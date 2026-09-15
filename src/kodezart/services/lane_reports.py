"""Preserve every dispatched lane before any result is collected."""

from collections.abc import Mapping, Sequence

from kodezart.core.errors import LaneRosterArityError
from kodezart.types.domain.scope_terminal import LaneReport, LaneReportState


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


class LaneReportCollection:
    """A fixed roster whose entries can be replaced but never dropped.

    Each snapshot is detached from later observations. Recording no result
    explicitly writes UNREPORTED, including after a previous attempt reported.
    The evaluator and terminal must independently assert their own incoming
    rosters; preserving this collection cannot validate another producer.
    """

    def __init__(self, *, dispatched: Mapping[str, str]) -> None:
        self._reports = {
            lane_key: LaneReport(
                lane_key=lane_key,
                issue_id=issue_id,
                state=LaneReportState.UNREPORTED,
            )
            for lane_key, issue_id in dispatched.items()
        }

    def record(self, *, lane_key: str, report: LaneReport | None) -> None:
        if lane_key not in self._reports:
            raise LaneRosterArityError(
                dispatched_lane_keys=tuple(self._reports),
                reported_lane_keys=(lane_key,),
            )
        previous = self._reports[lane_key]
        if report is None:
            report = LaneReport(
                lane_key=lane_key,
                issue_id=previous.issue_id,
                state=LaneReportState.UNREPORTED,
            )
        if report.lane_key != lane_key or report.issue_id != previous.issue_id:
            raise ValueError("lane report identity differs from its dispatch")
        self._reports[lane_key] = report

    def snapshot(self) -> tuple[LaneReport, ...]:
        """Return every entry in dispatch order, retaining explicit silence."""
        return tuple(self._reports.values())
