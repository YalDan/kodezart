"""Actual Organize description attempts retain source and surface authority."""

import pytest

from kodezart.core.errors import (
    McpCallUnansweredError,
    McpTransportError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.domain.errors import (
    OrganizeWriteRefusalError,
    StaleWriteError,
    SurfaceLeaseError,
)
from tests.chains import test_organize_owner as fixtures
from tests.tracker.conftest import CLAIMED_ISSUE


@pytest.mark.parametrize("change", ["held", "expired", "source", "approved", "unknown"])
async def test_configured_body_writer_rechecks_each_known_unsent_attempt(
    monkeypatch, change
):
    original_tracker = fixtures.tracker_over

    def tracker_with_retry(*args, **kwargs):
        return original_tracker(*args, **kwargs, max_retries=1)

    monkeypatch.setattr(fixtures, "tracker_over", tracker_with_retry)
    owner, board, _ = fixtures.factory()
    original_call = board.call_tool
    attempts = 0
    first_body = board.server.issues[CLAIMED_ISSUE].description
    changed_body = "The independently revised specification must survive."

    async def call_tool(*, name, arguments):
        nonlocal attempts
        if (
            name == "save_issue"
            and arguments.get("id") == CLAIMED_ISSUE
            and "description" in arguments
        ):
            attempts += 1
            if attempts == 1:
                if change == "expired":
                    board.advance(10000)
                elif change == "source":
                    board.server.issues[CLAIMED_ISSUE].description = changed_body
                elif change == "approved":
                    board.server.issues[CLAIMED_ISSUE].labels.append("approved scope")
                failure = (
                    McpCallUnansweredError if change == "unknown" else McpTransportError
                )
                raise failure(
                    "External transport interruption",
                    server_name="fixture",
                    tool_name=name,
                )
        return await original_call(name=name, arguments=arguments)

    monkeypatch.setattr(board, "call_tool", call_tool)
    error = None
    try:
        report = await fixtures.run_owner(owner)
    except (
        OrganizeWriteRefusalError,
        StaleWriteError,
        SurfaceLeaseError,
        TrackerProtocolError,
        TrackerUnavailableError,
    ) as exc:
        error = exc
    writes = [
        args
        for name, args in board.calls
        if name == "save_issue"
        and args.get("id") == CLAIMED_ISSUE
        and "description" in args
    ]
    if change == "held":
        assert error is None
        assert report.halt is None
        assert attempts == 2
        assert len(writes) == 1
        assert writes[0]["description"] == "Prepared body grounded in the source."
    else:
        assert error is not None
        assert attempts == 1, "a refused description attempt was sent again"
        assert writes == [], "an unauthorized description reached the native writer"
        assert board.server.issues[CLAIMED_ISSUE].description == (
            changed_body if change == "source" else first_body
        )
    assert board.grants() == []
