"""Aggregates belong on event surfaces, never on descriptions read as current."""

import pytest

from kodezart.types.domain.gating import (
    DESTINATION_DURABILITY,
    OutboundDestination,
    SurfaceDurability,
    durability_of,
)


def test_every_real_writer_has_a_durability_classification() -> None:
    """Adding a destination without deciding how it is read fails this gate."""
    assert set(DESTINATION_DURABILITY) == set(OutboundDestination)


@pytest.mark.parametrize(
    "destination",
    [
        OutboundDestination.BRANCH_NAME,
        OutboundDestination.PR_TITLE,
        OutboundDestination.PR_BODY,
        OutboundDestination.ARTIFACT_TICKET_JSON,
        OutboundDestination.ARTIFACT_CRITERIA_JSON,
    ],
)
def test_current_descriptions_and_artifacts_are_durable(
    destination: OutboundDestination,
) -> None:
    assert durability_of(destination) is SurfaceDurability.DURABLE


@pytest.mark.parametrize(
    "destination",
    [
        OutboundDestination.PR_COMMENT,
        OutboundDestination.TRACKER_COMMENT,
        OutboundDestination.COMMIT_MESSAGE,
        OutboundDestination.COMMIT_MESSAGE_DIVERGENCE_REPLAY,
    ],
)
def test_appended_events_are_point_in_time(
    destination: OutboundDestination,
) -> None:
    assert durability_of(destination) is SurfaceDurability.POINT_IN_TIME


def test_a_write_without_a_destination_member_is_durable() -> None:
    assert durability_of(None) is SurfaceDurability.DURABLE
