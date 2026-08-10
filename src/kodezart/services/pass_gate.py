"""The deterministic pre-query every scheduled pass is gated on.

One port call, no prompt, no session, no model — so a tick over a quiet
board costs zero tokens and cannot report a set smaller than the tracker's
own query returned.  That is the whole reason the gate is not a cheap
model call: a relayed answer is exactly the failure the determinism ruling
was written against.

Written against ``TrackerPort`` alone.  It holds no executor, no prompt
provider and no runner, and a test asserts that collaborator surface
rather than trusting the docstring.

The mark is the pass's own high-water stamp, advanced only by a tick that
observed something.  A tick that saw nothing leaves it where it was, so a
missed tick re-reads the same window rather than skipping it.
"""

from collections.abc import Sequence
from datetime import datetime

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.dispatch import PassDelta
from kodezart.types.domain.operation import QueueState
from kodezart.types.domain.tracker import IssueQuery, TrackerIssue


class PassGate:
    """Answers "did anything move?" for one queue state, deterministically."""

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        queue_state: QueueState,
        page_size: int,
    ) -> None:
        self._tracker: TrackerPort = tracker
        self._queue_state: QueueState = queue_state
        self._page_size: int = page_size
        self._mark: datetime | None = None
        self._log: BoundLogger = get_logger(__name__)

    @property
    def mark(self) -> datetime | None:
        """The high-water stamp the next query asks from."""
        return self._mark

    async def delta(self) -> PassDelta:
        """One port call; the issues that moved since the last observed mark."""
        issues: Sequence[TrackerIssue] = await self._tracker.scan_issues(
            query=IssueQuery(
                queue_state=self._queue_state,
                updated_since=self._mark,
                page_size=self._page_size,
            ),
        )
        if not issues:
            await self._log.ainfo(
                "pass_gate_no_delta",
                queue_state=self._queue_state.value,
                mark=None if self._mark is None else self._mark.isoformat(),
            )
            return PassDelta(mark=self._mark)
        self._mark = max(issue.updated_at for issue in issues)
        delta = PassDelta(
            changed=tuple(issue.issue_key for issue in issues),
            mark=self._mark,
        )
        await self._log.ainfo(
            "pass_gate_delta",
            queue_state=self._queue_state.value,
            changed=list(delta.changed),
            mark=self._mark.isoformat(),
        )
        return delta
