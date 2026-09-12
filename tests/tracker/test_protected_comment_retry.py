"""Protected writes keep the transport budget and original failure partition."""

import asyncio

import pytest
import structlog

from kodezart.core.errors import (
    McpCallUnansweredError,
    McpCredentialRefusedError,
    McpTransportError,
    TrackerAccessDeniedError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.domain.errors import StaleCommentWriteError
from tests.tracker.conftest import APPROVED_ISSUE, fixture_server
from tests.tracker.lease_fixtures import lease_for_comment, leased_comment
from tests.tracker.test_linear_mcp_tracker import tracker_over

MARKER = "[fixture:protected-retry:record]"


@pytest.mark.parametrize("create", [False, True])
@pytest.mark.parametrize("failure", ["unsent", "unknown", "credential", "malformed"])
async def test_write_attempt_budget_and_receipt_failures(monkeypatch, create, failure):
    server = fixture_server()
    tracker = tracker_over(server, max_retries=2)
    original = None
    if not create:
        original = await leased_comment(
            tracker, target=APPROVED_ISSUE, marker=MARKER, body="original"
        )
    async with lease_for_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER
    ) as holder:
        actual = server.call_tool
        attempted = 0
        failure_object = None

        async def call(*, name, arguments):
            nonlocal attempted, failure_object
            if name != "save_comment" or arguments.get("body") != f"{MARKER}\nnew":
                return await actual(name=name, arguments=arguments)
            attempted += 1
            if failure == "unsent":
                failure_object = McpTransportError("not sent", server_name="fixture")
                raise failure_object
            if failure == "credential":
                failure_object = McpCredentialRefusedError(
                    "denied", server_name="fixture"
                )
                raise failure_object
            result = await actual(name=name, arguments=arguments)
            if failure == "unknown":
                failure_object = McpCallUnansweredError(
                    "sent, unanswered", server_name="fixture"
                )
                raise failure_object
            assert result is not None
            return {"id": "invalid receipt"}

        monkeypatch.setattr(server, "call_tool", call)
        error = {
            "unsent": TrackerUnavailableError,
            "unknown": TrackerUnavailableError,
            "credential": TrackerAccessDeniedError,
            "malformed": TrackerProtocolError,
        }[failure]
        with structlog.testing.capture_logs() as logs, pytest.raises(error) as caught:
            await tracker.upsert_comment(
                target=APPROVED_ISSUE,
                marker=MARKER,
                body="new",
                holder=holder,
                expected=original,
            )
        assert attempted == (3 if failure == "unsent" else 1)
        if failure_object is not None:
            assert caught.value.__cause__ is failure_object
        retries = [row for row in logs if row["event"] == "tracker_mcp_retry"]
        assert len(retries) == (2 if failure == "unsent" else 0)
        rows = [row for row in server.comments if row.body == f"{MARKER}\nnew"]
        assert len(rows) == (1 if failure in {"unknown", "malformed"} else 0)


@pytest.mark.parametrize("cancel", [False, True])
async def test_backoff_rechecks_drift_and_preserves_task_cancellation(
    monkeypatch, cancel
):
    server = fixture_server()
    tracker = tracker_over(server, max_retries=2, retry_backoff_factor=0.25)
    original = await leased_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER, body="original"
    )
    async with lease_for_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER
    ) as holder:
        actual_call = server.call_tool
        actual_sleep = asyncio.sleep
        backoff = asyncio.Event()
        release = asyncio.Event()
        writes = 0

        async def call(*, name, arguments):
            nonlocal writes
            if name == "save_comment" and arguments.get("id") == original.comment_key:
                writes += 1
                if writes == 1:
                    raise McpTransportError("not sent", server_name="fixture")
            return await actual_call(name=name, arguments=arguments)

        async def sleep(delay):
            if delay == 0.25:
                backoff.set()
                await release.wait()
            else:
                await actual_sleep(delay)

        monkeypatch.setattr(server, "call_tool", call)
        monkeypatch.setattr(asyncio, "sleep", sleep)
        task = asyncio.create_task(
            tracker.upsert_comment(
                target=APPROVED_ISSUE,
                marker=MARKER,
                body="new",
                holder=holder,
                expected=original,
            )
        )
        await asyncio.wait_for(backoff.wait(), timeout=2)
        if cancel:
            task.cancel("caller cancellation")
            with pytest.raises(asyncio.CancelledError, match="caller cancellation"):
                await task
        else:
            row = next(row for row in server.comments if row.id == original.comment_key)
            row.body = f"{MARKER}\nnewer answer during backoff"
            release.set()
            with pytest.raises(StaleCommentWriteError):
                await task
        assert writes == 1
        assert not any(row.body == f"{MARKER}\nnew" for row in server.comments)
