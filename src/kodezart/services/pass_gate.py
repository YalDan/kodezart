"""The deterministic pre-query a scheduled pass is gated on.

One port call per configured signal and container, no prompt, no session,
no model — so a tick over a quiet board costs zero tokens and cannot
report a set smaller than the tracker's own query returned.  That is the
whole reason the gate is not a cheap model call: a relayed answer is
exactly the failure the determinism ruling was written against.

Written against ``TrackerPort`` alone.  It holds no executor, no prompt
provider and no runner, and a test asserts that collaborator surface
rather than trusting the docstring.

A gate is the DISJUNCTION over its signals: any signal reporting work runs
the pass, and a gate configured with none is never built at all.  Which
signals a pass gates on is configuration, so one mechanism serves the
dispatch tick and the prompt passes alike — rather than one caller owning
a gate hardcoded to its own question while the others go ungated.

Every question is asked WITHIN a container: a team key for the issue
signals, a repository url for the review signal.  A gate is constructed
with the containers its pass is scoped to and asks each signal once per
container, because a workspace holds more than one operation's board and
an unscoped scan answers about somebody else's work — and answers about
it FIRST, since a full page of another board's activity crowds the page a
scoped query would have returned whole.  A gate carrying a signal whose
container class it was given nothing for refuses at construction rather
than scanning the workspace, and so does one given a container that
cannot serve the signal at all — a repository with no forge behind it,
which the review scan has no owner or name to ask about.  Both refusals
happen where the containers meet the signal, because the alternative is a
tick that raises identically forever.

Marks are PER SIGNAL AND CONTAINER, never one per gate and never one per
signal.  Issues and reviews have independent timelines, so a shared stamp
would let issue activity advance the mark past review activity nobody has
read — and N containers are N independent timelines for the same reason,
so a per-signal stamp shared across them would let one busy board advance
the mark past another board's unseen work.  Each mark is advanced only by
a tick that observed something on THAT signal in THAT container, so a
missed tick re-reads its own window rather than skipping it.  And a mark
this gate advanced for a pass that then RAISED is put back — ``rearm`` —
because asking is not reading: the window was opened for work that never
happened, and leaving the mark past it spends a wake-up on nothing.

Asking is itself three-state, never two.  A signal resolves to
saw-something, saw-nothing, or COULD-NOT-ASK: a transport that refused to
answer contributes nothing to the disjunction, leaves its own mark exactly
where it was, and is named in its own event.  Reading it as "nothing
moved" would be a lie the next tick cannot detect, because the window it
would have covered is one no tick ever reads again.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import assert_never

from kodezart.core.errors import McpTransportError, PassGateScopeError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import TrackerPort
from kodezart.domain.git_url import is_forge_less_origin
from kodezart.types.domain.dispatch import PassDelta, PassSignal
from kodezart.types.domain.operation import OperationMemberAbsentError, QueueState
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
        team_keys: Sequence[str],
        repo_urls: Sequence[str],
        page_size: int,
    ) -> None:
        self._tracker: TrackerPort = tracker
        self._signals: tuple[PassSignal, ...] = tuple(signals)
        self._team_keys: tuple[str, ...] = tuple(team_keys)
        self._repo_urls: tuple[str, ...] = tuple(repo_urls)
        self._page_size: int = page_size
        self._marks: dict[tuple[PassSignal, str], datetime | None] = {
            (signal, container): None
            for signal in self._signals
            for container in self._containers(signal)
        }
        #: What each mark the LAST :meth:`delta` advanced held before it did.
        #: Empty until a delta advances something, and emptied again by
        #: :meth:`rearm`, so "nothing to put back" and "already put back"
        #: are the same state and re-arming twice cannot rewind a window
        #: twice.
        self._advanced: dict[tuple[PassSignal, str], datetime | None] = {}
        self._log: BoundLogger = get_logger(__name__)

    @property
    def signals(self) -> tuple[PassSignal, ...]:
        """The questions this gate asks, in the order it asks them."""
        return self._signals

    def mark(self, signal: PassSignal, *, container: str) -> datetime | None:
        """The high-water stamp *signal*'s next query over *container* asks from."""
        return self._marks.get((signal, container))

    async def delta(self) -> PassDelta:
        """One port call per signal and container; everything that moved.

        A container whose ask could not be answered is named and skipped:
        it contributes nothing here and keeps its own mark, and the other
        containers of the same signal are asked regardless.
        """
        changed: list[str] = []
        unanswerable: list[str] = []
        self._advanced.clear()
        for signal in self._signals:
            for container in self._containers(signal):
                try:
                    changed.extend(await self._observe(signal, container))
                except McpTransportError as exc:
                    unanswerable.append(f"{signal.value}@{container}")
                    await self._log.awarning(
                        "pass_gate_signal_unanswerable",
                        signal=signal.value,
                        container=container,
                        error=str(exc),
                    )
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
                unanswerable=unanswerable,
                mark=None if newest is None else newest.isoformat(),
            )
            return PassDelta(mark=newest)
        delta = PassDelta(changed=tuple(changed), mark=newest)
        await self._log.ainfo(
            "pass_gate_delta",
            signals=[signal.value for signal in self._signals],
            changed=list(delta.changed),
            unanswerable=unanswerable,
            mark=None if newest is None else newest.isoformat(),
        )
        return delta

    def rearm(self) -> None:
        """Put every mark the last :meth:`delta` advanced back where it was.

        Asking advances a mark, which is what stops the next tick reporting
        the same window again.  But the pass BENEATH the gate is what reads
        that window, and a pass that raised read nothing: leaving the mark
        forward there consumes a wake-up nobody acted on, and at the shipped
        dispatch signal that means an approved issue sits undispatched until
        something else happens to touch it.  The consumer calls this on the
        way out of a failure, so the next tick asks the same question again.

        Idempotent by construction: the snapshot is emptied as it is
        applied, so a second call and a call after a delta that advanced
        nothing are both no-ops rather than a second rewind.
        """
        for key, previous in self._advanced.items():
            self._marks[key] = previous
        self._advanced.clear()

    def _containers(self, signal: PassSignal) -> tuple[str, ...]:
        """The containers *signal* is asked once per, refusing when it has none.

        Two refusals, because a container can fail a signal in two ways: it
        can be missing, and it can be present and unable to serve the
        question.  Both land here, at construction, rather than on the tick
        that reaches one.

        Total over the vocabulary by construction: a new member with no arm
        here fails type checking rather than defaulting to a container class
        it does not belong to.
        """
        match signal:
            case PassSignal.reviews_changed:
                containers = self._repo_urls
                missing = "repository this gate's review signal can be scoped to"
                for repo_url in containers:
                    if is_forge_less_origin(repo_url):
                        raise PassGateScopeError(
                            "a review scan resolves an owner and a repository "
                            "out of a forge URL and this origin has no forge "
                            "behind it, so the review signal cannot be asked "
                            "about this repository at all",
                            signal=signal.value,
                            container=repo_url,
                        )
            case (
                PassSignal.triage_backlog
                | PassSignal.approved_changed
                | PassSignal.issues_changed
            ):
                containers = self._team_keys
                missing = "team this gate's issue signals can be scoped to"
            case _:
                assert_never(signal)
        if not containers:
            raise OperationMemberAbsentError(
                missing=missing,
                stops=(
                    f"the {signal.value} signal has no container to be bounded "
                    "by, so nothing distinguishes this operation's work from "
                    "any other in the workspace and the gate would answer "
                    "about a board it does not own"
                ),
            )
        return containers

    async def _observe(self, signal: PassSignal, container: str) -> tuple[str, ...]:
        """What *signal* saw in *container*, advancing that mark if it saw anything.

        Total over the vocabulary by construction: a new member with no
        arm here fails type checking rather than falling through to a
        silent "nothing moved", which would gate its pass off forever.
        """
        match signal:
            case PassSignal.triage_backlog:
                return await self._backlog(container)
            case PassSignal.reviews_changed:
                return await self._review_delta(signal, container)
            case PassSignal.approved_changed:
                return await self._issue_delta(signal, container, QueueState.APPROVED)
            case PassSignal.issues_changed:
                return await self._issue_delta(signal, container, None)
            case _:
                assert_never(signal)

    async def _backlog(self, team_key: str) -> tuple[str, ...]:
        """The standing triage backlog — a size question, so no mark moves."""
        issues: Sequence[TrackerIssue] = await self._tracker.scan_issues(
            query=IssueQuery(
                queue_state=QueueState.TRIAGE,
                team_key=team_key,
                page_size=self._page_size,
            ),
        )
        return tuple(issue.issue_key for issue in issues)

    async def _issue_delta(
        self,
        signal: PassSignal,
        team_key: str,
        queue_state: QueueState | None,
    ) -> tuple[str, ...]:
        """Issues that moved on *signal* since its mark; an absent state is any."""
        issues: Sequence[TrackerIssue] = await self._tracker.scan_issues(
            query=IssueQuery(
                queue_state=queue_state,
                team_key=team_key,
                updated_since=self._marks[signal, team_key],
                page_size=self._page_size,
            ),
        )
        if not issues:
            return ()
        self._advanced[signal, team_key] = self._marks[signal, team_key]
        self._marks[signal, team_key] = max(issue.updated_at for issue in issues)
        return tuple(issue.issue_key for issue in issues)

    async def _review_delta(
        self,
        signal: PassSignal,
        repo_url: str,
    ) -> tuple[str, ...]:
        """Reviews that moved since *signal*'s mark — unreachable by any issue scan."""
        reviews: Sequence[TrackerReview] = await self._tracker.scan_reviews(
            query=ReviewQuery(
                repo_url=repo_url,
                updated_since=self._marks[signal, repo_url],
                page_size=self._page_size,
            ),
        )
        if not reviews:
            return ()
        self._advanced[signal, repo_url] = self._marks[signal, repo_url]
        self._marks[signal, repo_url] = max(review.updated_at for review in reviews)
        return tuple(review.review_key for review in reviews)
