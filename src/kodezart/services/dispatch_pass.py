"""One scheduled dispatch tick: the gate first, the pass only on a delta.

The composition of the two halves the cutover needs and nothing else.  The
gate is deterministic and free; the pass beneath it reads several issues
and claims one.  Running the pass unconditionally on a quiet board would
spend that work to discover nothing moved, every tick, forever.

The tick reports through the log rather than returning a value: it is
driven by the scheduler, which has no caller to hand a report to.
"""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.services.fire_dispatcher import FireDispatcher
from kodezart.services.pass_gate import PassGate


class GatedDispatchPass:
    """A dispatch pass behind its deterministic pre-query."""

    def __init__(self, *, gate: PassGate, dispatcher: FireDispatcher) -> None:
        self._gate: PassGate = gate
        self._dispatcher: FireDispatcher = dispatcher
        self._log: BoundLogger = get_logger(__name__)

    async def run(self) -> None:
        """Consult the gate; run the pass only when something moved."""
        delta = await self._gate.delta()
        if not delta.has_delta():
            await self._log.ainfo("dispatch_pass_skipped_no_delta")
            return
        report = await self._dispatcher.run_pass()
        await self._log.ainfo(
            "dispatch_pass_completed",
            outcome=report.outcome.value,
            changed=list(delta.changed),
            claimed_issue_key=report.claimed_issue_key,
            job_id=report.job_id,
        )
