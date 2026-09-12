"""Translate MCP record-sink failures at the adapter boundary."""

from collections.abc import Iterator
from contextlib import contextmanager

from kodezart.core.errors import (
    McpCallUnansweredError,
    McpCredentialRefusedError,
    McpSessionClosedError,
    McpTransportError,
    RunRecordWriteError,
)
from kodezart.types.domain.operation import RecordDestination
from kodezart.types.domain.run_records import RunRecord, RunRecordFailure


@contextmanager
def record_failure_boundary(
    *, destination: RecordDestination, record: RunRecord
) -> Iterator[None]:
    """Preserve the record remedy and concrete cause without leaking MCP types.

    This boundary never retries an unanswered write or suppresses cancellation
    and programming errors. Both verification and publication carry the same
    destination and run identity when they fail.
    """
    try:
        yield
    except (McpTransportError, McpCredentialRefusedError) as exc:
        if isinstance(exc, McpSessionClosedError):
            failure = RunRecordFailure.SESSION_CLOSED
        elif isinstance(exc, McpCallUnansweredError):
            failure = RunRecordFailure.UNANSWERED
        else:
            failure = RunRecordFailure.VENDOR_REFUSED
        raise RunRecordWriteError(
            f"the run's declared destination did not take its record: {exc}",
            kind=record.kind.value,
            destination=destination.id,
            system=destination.system.value,
            failure=failure.value,
        ) from exc
