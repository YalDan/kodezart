"""Domain exceptions — no I/O, no infrastructure concerns."""

from collections.abc import Sequence

from kodezart.types.domain.gating import ScanFailureKind, ScanHit


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

    ``failure`` is the typed reason a scanner had NO answer, and it is a
    separate field rather than a category because "the scanner did not
    answer" and "the scanner found something" are different states an
    operator must be able to tell apart.  ``hits`` carries the per-span
    rationale, without which a human can neither confirm nor overrule the
    block — and a gate that cannot be confirmed gets worked around.
    """

    def __init__(
        self,
        message: str,
        *,
        writer: str,
        categories: Sequence[str],
        failure: ScanFailureKind | None = None,
        hits: Sequence[ScanHit] = (),
    ) -> None:
        detail = f"{message} (writer: {writer}; categories: {', '.join(categories)})"
        if failure is not None:
            detail = f"{detail} (scan failure: {failure.value})"
        super().__init__(detail)
        self.writer: str = writer
        self.categories: tuple[str, ...] = tuple(categories)
        self.failure: ScanFailureKind | None = failure
        self.hits: tuple[ScanHit, ...] = tuple(hits)


class QueueFullError(Exception):
    """Raised when a lane's queue is at capacity and cannot accept a submission."""


class DuplicateWorkRefError(Exception):
    """Raised when a second ref is recorded at a role that admits only one.

    At most one ``DELIVERABLE`` ref exists per issue.  A second is an error
    and never a silent replacement: silently replacing it would move every
    dependent lane's base without anything saying so.
    """

    def __init__(
        self,
        message: str,
        *,
        issue_id: str,
        role: str,
        existing_branch: str,
        offered_branch: str,
    ) -> None:
        super().__init__(message)
        self.issue_id: str = issue_id
        self.role: str = role
        self.existing_branch: str = existing_branch
        self.offered_branch: str = offered_branch


class AssetFetchError(Exception):
    """Raised when a fire's referenced asset cannot be brought into its context.

    Every asset a ticket references is required: kodezart does not decide
    which of an author's references matter.  A fetch that failed and a fetch
    that was skipped are indistinguishable to the session working the fire,
    so there is no skip — the fire does not build.

    ``reason`` is a short machine-readable token (``unreadable``,
    ``too_large``, ``too_many``, ``timeout``, ``private_content``) so a
    consumer can route on the failure without parsing the message.
    """

    def __init__(
        self,
        message: str,
        *,
        issue_key: str,
        reason: str,
        asset_key: str | None = None,
    ) -> None:
        super().__init__(f"{message} (issue: {issue_key}; reason: {reason})")
        self.issue_key: str = issue_key
        self.reason: str = reason
        self.asset_key: str | None = asset_key


class BaseResolutionError(Exception):
    """Raised when a lane's base cannot be resolved. The lane does not dispatch.

    Trunk is NEVER substituted on this path.  A lane whose premise cannot be
    located must not build without it, and a base that silently fell back to
    trunk is indistinguishable from a lane that had no premise at all.

    Carries primitives only, the metadata shape ``RateLimitError`` uses.
    """

    def __init__(
        self,
        message: str,
        *,
        issue_id: str,
        blocker_issue_ids: Sequence[str] = (),
        branches: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.issue_id: str = issue_id
        self.blocker_issue_ids: tuple[str, ...] = tuple(blocker_issue_ids)
        self.branches: tuple[str, ...] = tuple(branches)


class BaseIntegrationConflictError(BaseResolutionError):
    """Raised when two inputs to a constructed base conflict textually.

    Never resolved by judgment, never by dropping an input, and trunk is
    never substituted.  No partial integration ref is pushed or recorded.
    """

    def __init__(
        self,
        message: str,
        *,
        issue_id: str,
        branches: Sequence[str],
        paths: Sequence[str],
    ) -> None:
        detail = (
            f"{message} (refs: {', '.join(branches)}; "
            f"conflicting paths: {', '.join(paths)})"
        )
        super().__init__(detail, issue_id=issue_id, branches=branches)
        self.paths: tuple[str, ...] = tuple(paths)


class MergeConflictError(Exception):
    """Raised by the git port when a merge cannot be completed.

    ``paths`` carries the paths git named as conflicting; it is empty when
    git refused the merge without naming any (a non-fast-forwardable
    divergence, for instance).
    """

    def __init__(
        self, message: str, *, source_branch: str, paths: Sequence[str]
    ) -> None:
        super().__init__(message)
        self.source_branch: str = source_branch
        self.paths: tuple[str, ...] = tuple(paths)
