"""The fire-preparation pass: a frozen body, scanned, gated, then promoted.

The deterministic half of preparation.  The session composes bodies out of
a work set this service froze and handed it; this service decides what
happens to each one, and every step of that decision is arithmetic.

Nothing that costs tokens runs before the pre-query.  The window is
whatever the deterministic gate saw move on the entry queue since this
pass's own high-water mark, so a tick over an unmoved queue reaches no
session at all and a stalled queue is not re-composed every interval
forever.  A tick whose session did not answer rewinds the mark, because a
consumed-but-unprocessed window is worse than a repeated one.

Four gates stand between a composed body and the tracker, in this order
and for four different reasons:

* **the frozen window** — a body naming an item the pass did not read is
  dropped.  A pass that acted outside the window it froze would be acting
  on a state it never verified.
* **the hygiene scan** — can the implementer who receives this body act on
  it alone?  Orchestration vocabulary, tracker shorthand and pre-cooked
  evaluator material fail that question whatever their privacy.
* **the outbound gate** — may this leave the process at all?
* **the re-read** — is the item still the one the window recorded?  A
  session runs for minutes, and an item another actor moved in the
  meantime is dropped rather than overwritten.  Deliberately the LAST
  question asked, immediately before the write, because the whole value of
  a re-read is how little room it leaves after itself.

Same body, four questions, and none of them substitutes for another.  A
body that fails any of them leaves its issue exactly where it found it:
nothing half-written, and the next pass sees the same item.
"""

from collections.abc import Mapping

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.protocols import OutboundContentGate, TrackerPort
from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.services.hygiene_scan import HygieneScan
from kodezart.services.pass_gate import PassGate
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
from kodezart.types.domain.tracker import TrackerIssue

#: The preparation session composes from a work set already rendered into
#: its prompt, so it reaches nothing.  Not a hardened sandbox and not
#: claimed as one — simply the honest grant for work that needs no tool.
_NO_TOOLS: tuple[str, ...] = ()


class FirePrepPass:
    """One preparation tick: read the entry queue, promote what is shaped."""

    def __init__(
        self,
        *,
        pre_query: PassGate,
        tracker: TrackerPort,
        session: PassSession,
        scan: HygieneScan,
        gate: OutboundContentGate,
        working_dir: str,
    ) -> None:
        self._pre_query: PassGate = pre_query
        self._tracker: TrackerPort = tracker
        self._session: PassSession = session
        self._scan: HygieneScan = scan
        self._gate: OutboundContentGate = gate
        self._working_dir: str = working_dir
        self._log: BoundLogger = get_logger(__name__)

    async def run(self) -> None:
        """Take this tick's window from the gate, compose, promote what passes."""
        delta = await self._pre_query.delta()
        if not delta.has_delta():
            await self._log.ainfo("fire_prep_pass_skipped_no_delta")
            return
        window = delta.issues

        answer = await self._session.compose(
            key=PromptKey.FIRE_PREP_PASS,
            variables={"work_set": list(window)},
            schema=FIRE_PREP_SCHEMA,
            model=FirePrepOutput,
            cwd=self._working_dir,
            allowed_tools=_NO_TOOLS,
        )
        if isinstance(answer, PassSessionFailure):
            # The mark goes back. A window consumed by a pass that never
            # processed it would leave its items unprepared with nothing
            # ever asking about them again.
            self._pre_query.rewind()
            await self._log.awarning(
                "fire_prep_pass_unanswered",
                failure=answer.value,
                window=[issue.issue_key for issue in window],
            )
            return

        frozen = {issue.issue_key: issue for issue in window}
        for preparation in answer.preparations:
            await self._promote(preparation, frozen=frozen)

    async def _promote(
        self,
        preparation: PreparedFire,
        *,
        frozen: Mapping[str, TrackerIssue],
    ) -> None:
        """Scan, gate and write one prepared body, or say which gate stopped it."""
        recorded = frozen.get(preparation.issue_key)
        if recorded is None:
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

        if not await self._still_as_recorded(recorded):
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

    async def _still_as_recorded(self, recorded: TrackerIssue) -> bool:
        """Whether the item is where the frozen window last saw it.

        Two facts, because they fail differently: an item that left the
        entry queue was disposed of by someone else, and an item whose
        stamp moved was edited under the session.  Either way this pass's
        composed body answers a state that no longer exists, and writing it
        would overwrite the other actor's change with a stale one.
        """
        current = await self._tracker.read_issue(issue_key=recorded.issue_key)
        if (
            QueueState.TRIAGE in current.queue_states
            and current.updated_at == recorded.updated_at
        ):
            return True
        await self._log.awarning(
            "fire_prep_body_stale",
            issue_key=recorded.issue_key,
            recorded_at=recorded.updated_at.isoformat(),
            current_at=current.updated_at.isoformat(),
            queue_states=sorted(state.value for state in current.queue_states),
        )
        return False
