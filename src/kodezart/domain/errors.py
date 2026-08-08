"""Domain exceptions — no I/O, no infrastructure concerns."""

from collections.abc import Sequence


class WorkspaceError(Exception):
    """Raised when workspace acquisition or release fails."""


class TransientAPIError(Exception):
    """Raised for transient, retry-eligible API failures (e.g. 5xx, network)."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after: float | None = retry_after


class RateLimitError(TransientAPIError):
    """Raised when an API rate limit is hit; carries timing and utilization metadata."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        resets_at: int | None = None,
        utilization: float | None = None,
    ) -> None:
        super().__init__(message, retry_after=retry_after)
        self.resets_at: int | None = resets_at
        self.utilization: float | None = utilization


class AgentSDKError(Exception):
    """Raised when the Claude Agent SDK reports a non-transient failure.

    Carries the structured ``ProcessError`` metadata (``exit_code``,
    ``stderr_tail``) when re-raised from ``ClaudeClientExecutor``.
    Both are nullable; the non-``ProcessError`` branches of the SDK
    exception handler (``CLIConnectionError``, ``ClaudeSDKError``)
    leave them ``None``.  Storing primitive scalars only — no SDK
    exception object reference is retained, mirroring the
    ``RateLimitError`` primitive-only shape.
    """

    def __init__(
        self,
        message: str,
        *,
        error_kind: str,
        exit_code: int | None = None,
        stderr_tail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_kind: str = error_kind
        self.exit_code: int | None = exit_code
        self.stderr_tail: str | None = stderr_tail


class OutboundContentBlockedError(Exception):
    """Raised when the outbound gate blocks a write. Nothing is posted.

    Carries the categories that triggered the block and the writer that was
    about to run, so the workflow can surface both in its event stream.
    """

    def __init__(
        self,
        message: str,
        *,
        writer: str,
        categories: Sequence[str],
    ) -> None:
        detail = f"{message} (writer: {writer}; categories: {', '.join(categories)})"
        super().__init__(detail)
        self.writer: str = writer
        self.categories: tuple[str, ...] = tuple(categories)


class QueueFullError(Exception):
    """Raised when a lane's queue is at capacity and cannot accept a submission."""
