"""The fire-preparation pass: a frozen body, scanned, gated, then promoted.

The deterministic half of preparation.  The session composes bodies out of
a work set this service froze and handed it; this service decides what
happens to each one, and every step of that decision is arithmetic.

Three gates stand between a composed body and the tracker, in this order
and for three different reasons:

* **the frozen window** — a body naming an item the pass did not read is
  dropped.  A pass that acted outside the window it froze would be acting
  on a state it never verified, which is the atomicity guard the routines
  carry.
* **the hygiene scan** — can the implementer who receives this body act on
  it alone?  Orchestration vocabulary, tracker shorthand and pre-cooked
  evaluator material fail that question whatever their privacy.
* **the outbound gate** — may this leave the process at all?

Same body, three questions, and none of them substitutes for another.  A
body that fails any of them leaves its issue exactly where it found it:
nothing half-written, and the next pass sees the same item.
"""

from collections.abc import Sequence

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.protocols import OutboundContentGate, TrackerPort
from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.services.hygiene_scan import HygieneScan
from kodezart.services.pass_session import PassSession
from kodezart.types.domain.gating import (
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.operation import QueueState
from kodezart.types.domain.passes import (
    FIRE_PREP_SCHEMA,
    FirePrepOutput,
    PassSessionFailure,
    PreparedFire,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.tracker import IssueQuery, TrackerIssue

#: The preparation session composes from a work set already rendered into
#: its prompt, so it reaches nothing.  Not a hardened sandbox and not
#: claimed as one — simply the honest grant for work that needs no tool.
_NO_TOOLS: tuple[str, ...] = ()


class FirePrepPass:
    """One preparation tick: read the entry queue, promote what is shaped."""

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        session: PassSession,
        scan: HygieneScan,
        gate: OutboundContentGate,
        page_size: int,
        working_dir: str,
    ) -> None:
        self._tracker: TrackerPort = tracker
        self._session: PassSession = session
        self._scan: HygieneScan = scan
        self._gate: OutboundContentGate = gate
        self._page_size: int = page_size
        self._working_dir: str = working_dir
        self._log: BoundLogger = get_logger(__name__)

    async def run(self) -> None:
        """Freeze the entry-queue window, compose over it, promote what passes."""
        window: Sequence[TrackerIssue] = await self._tracker.scan_issues(
            query=IssueQuery(
                queue_state=QueueState.TRIAGE,
                page_size=self._page_size,
            ),
        )
        if not window:
            await self._log.ainfo("fire_prep_window_empty")
            return

        answer = await self._session.compose(
            key=PromptKey.FIRE_PREP_PASS,
            variables={"work_set": list(window)},
            schema=FIRE_PREP_SCHEMA,
            model=FirePrepOutput,
            cwd=self._working_dir,
            allowed_tools=_NO_TOOLS,
        )
        if isinstance(answer, PassSessionFailure):
            await self._log.awarning(
                "fire_prep_pass_unanswered",
                failure=answer.value,
                window=[issue.issue_key for issue in window],
            )
            return

        frozen = {issue.issue_key for issue in window}
        for preparation in answer.preparations:
            await self._promote(preparation, frozen=frozen)

    async def _promote(
        self,
        preparation: PreparedFire,
        *,
        frozen: set[str],
    ) -> None:
        """Scan, gate and write one prepared body, or say which gate stopped it."""
        if preparation.issue_key not in frozen:
            await self._log.awarning(
                "fire_prep_body_outside_window",
                issue_key=preparation.issue_key,
            )
            return

        report = await self._scan.inspect(
            body=preparation.body,
            destination=OutboundDestination.PREPARED_FIRE_BODY,
        )
        if not report.promotable:
            await self._log.awarning(
                "fire_prep_body_refused",
                issue_key=preparation.issue_key,
                categories=[category.value for category in report.categories],
                failure=None if report.failure is None else report.failure.value,
            )
            return

        try:
            body = await gated_write(
                gate=self._gate,
                log=self._log,
                content=preparation.body,
                visibility=RepoVisibility.PUBLIC,
                shape=WriterShape.PROSE,
                destination=OutboundDestination.PREPARED_FIRE_BODY,
            )
        except OutboundContentBlockedError as exc:
            # One blocked body is not a failed pass. The categories and the
            # per-hit rationales are already on the gate's own event, and
            # the issue stays exactly where it was, so the next pass sees
            # it again rather than the window silently shrinking.
            await self._log.awarning(
                "fire_prep_body_blocked",
                issue_key=preparation.issue_key,
                categories=list(exc.categories),
            )
            return

        await self._tracker.update_issue(issue_key=preparation.issue_key, body=body)
        # The entry queue is left behind only after the body is on the
        # issue: an item marked shaped whose body never landed is the one
        # ordering a reader cannot recover from.
        await self._tracker.set_queue_state(
            issue_key=preparation.issue_key,
            state=QueueState.PROPOSED,
        )
        await self._log.ainfo(
            "fire_prep_body_promoted",
            issue_key=preparation.issue_key,
            queue_state=QueueState.PROPOSED.value,
        )
