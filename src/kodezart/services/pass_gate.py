"""The deterministic pre-query a scheduled pass is gated on.

One port call per configured signal, no prompt, no session, no model — so
a tick over a quiet board costs zero tokens and cannot report a set
smaller than the tracker's own query returned.  That is the whole reason
the gate is not a cheap model call: a relayed answer is exactly the
failure the determinism ruling was written against.

Written against ``TrackerPort`` alone.  It holds no executor, no prompt
provider and no runner, and a test asserts that collaborator surface
rather than trusting the docstring.

A gate is the DISJUNCTION over its signals: any signal reporting work runs
the pass, and a gate configured with none is never built at all.  Which
signals a pass gates on is configuration, so one mechanism serves the
dispatch tick and the prompt passes alike — rather than one caller owning
a gate hardcoded to its own question while the others go ungated.

Marks are PER SIGNAL, never one per gate.  Issues and reviews have
independent timelines, so a shared stamp would let issue activity advance
the mark past review activity nobody has read.  Each mark is advanced only
by a tick that observed something on THAT signal, so a missed tick
re-reads its own window rather than skipping it.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import assert_never

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import TrackerPort
from kodezart.types.domain.dispatch import PassDelta, PassSignal
from kodezart.types.domain.operation import QueueState
from kodezart.types.domain.tracker import (
    IssueQuery,
    ReviewQuery,
    TrackerIssue,
    TrackerReview,
)


class PassGate:
    """Answers "does this pass have work?" over its signals, deterministically."""

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        signals: Sequence[PassSignal],
        page_size: int,
    ) -> None:
        self._tracker: TrackerPort = tracker
        self._signals: tuple[PassSignal, ...] = tuple(signals)
        self._page_size: int = page_size
        self._marks: dict[PassSignal, datetime | None] = dict.fromkeys(self._signals)
        self._log: BoundLogger = get_logger(__name__)

    @property
    def signals(self) -> tuple[PassSignal, ...]:
        """The questions this gate asks, in the order it asks them."""
        return self._signals

    def mark(self, signal: PassSignal) -> datetime | None:
        """The high-water stamp *signal*'s next query asks from."""
        return self._marks.get(signal)

    async def delta(self) -> PassDelta:
        """One port call per signal; everything that moved on any of them."""
        changed: list[str] = []
        for signal in self._signals:
            changed.extend(await self._observe(signal))
        # The value's mark is the newest stamp ACROSS the delta-bearing
        # signals, which is what a reader reconstructing this tick wants.
        # The per-signal marks are what the next queries actually use, and
        # they are not collapsed into this one.
        stamps = [mark for mark in self._marks.values() if mark is not None]
        newest = max(stamps) if stamps else None
        if not changed:
            await self._log.ainfo(
                "pass_gate_no_delta",
                signals=[signal.value for signal in self._signals],
                mark=None if newest is None else newest.isoformat(),
            )
            return PassDelta(mark=newest)
        delta = PassDelta(changed=tuple(changed), mark=newest)
        await self._log.ainfo(
            "pass_gate_delta",
            signals=[signal.value for signal in self._signals],
            changed=list(delta.changed),
            mark=None if newest is None else newest.isoformat(),
        )
        return delta

    async def _observe(self, signal: PassSignal) -> tuple[str, ...]:
        """What *signal* saw this tick, advancing its own mark if it saw anything.

        Total over the vocabulary by construction: a new member with no
        arm here fails type checking rather than falling through to a
        silent "nothing moved", which would gate its pass off forever.
        """
        match signal:
            case PassSignal.triage_backlog:
                return await self._backlog()
            case PassSignal.reviews_changed:
                return await self._review_delta(signal)
            case PassSignal.approved_changed:
                return await self._issue_delta(signal, QueueState.APPROVED)
            case PassSignal.issues_changed:
                return await self._issue_delta(signal, None)
            case _:
                assert_never(signal)

    async def _backlog(self) -> tuple[str, ...]:
        """The standing triage backlog — a size question, so no mark moves."""
        issues: Sequence[TrackerIssue] = await self._tracker.scan_issues(
            query=IssueQuery(
                queue_state=QueueState.TRIAGE,
                page_size=self._page_size,
            ),
        )
        return tuple(issue.issue_key for issue in issues)

    async def _issue_delta(
        self,
        signal: PassSignal,
        queue_state: QueueState | None,
    ) -> tuple[str, ...]:
        """Issues that moved on *signal* since its mark; an absent state is any."""
        issues: Sequence[TrackerIssue] = await self._tracker.scan_issues(
            query=IssueQuery(
                queue_state=queue_state,
                updated_since=self._marks[signal],
                page_size=self._page_size,
            ),
        )
        if not issues:
            return ()
        self._marks[signal] = max(issue.updated_at for issue in issues)
        return tuple(issue.issue_key for issue in issues)

    async def _review_delta(self, signal: PassSignal) -> tuple[str, ...]:
        """Reviews that moved since *signal*'s mark — unreachable by any issue scan."""
        reviews: Sequence[TrackerReview] = await self._tracker.scan_reviews(
            query=ReviewQuery(
                updated_since=self._marks[signal],
                page_size=self._page_size,
            ),
        )
        if not reviews:
            return ()
        self._marks[signal] = max(review.updated_at for review in reviews)
        return tuple(review.review_key for review in reviews)
