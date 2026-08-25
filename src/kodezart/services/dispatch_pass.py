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
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.pass_gate import PassGate
from kodezart.types.domain.dispatch import DispatchOutcome


class GatedDispatchPass:
    """A dispatch pass behind its deterministic pre-query."""

    def __init__(
        self,
        *,
        gate: PassGate | None,
        dispatcher: FireDispatcher,
        lifecycle: LifecycleWatcher,
    ) -> None:
        self._gate: PassGate | None = gate
        self._dispatcher: FireDispatcher = dispatcher
        self._lifecycle: LifecycleWatcher = lifecycle
        self._log: BoundLogger = get_logger(__name__)

    async def run(self) -> None:
        """Consult the gate; run the pass only when something moved.

        An absent gate means this pass is ungated and runs every tick.
        The alternative — a gate holding no signals — would report an
        empty delta forever and silently pin the pass shut, which is why
        "no signals configured" resolves to no gate rather than to one.
        """
        changed: tuple[str, ...] = ()
        if self._gate is not None:
            delta = await self._gate.delta()
            if not delta.has_delta():
                await self._log.ainfo("dispatch_pass_skipped_no_delta")
                return
            changed = delta.changed
        report = await self._dispatcher.run_pass()
        await self._log.ainfo(
            "dispatch_pass_completed",
            outcome=report.outcome.value,
            changed=list(changed),
            claimed_issue_key=report.claimed_issue_key,
            job_id=report.job_id,
        )
        # Enqueueing is the moment the issue acquires a run to report on.
        # The other two outcomes claimed nothing, so there is nothing to
        # follow — never an empty watch started "just in case".
        if report.outcome is not DispatchOutcome.fire_enqueued:
            return
        if (
            report.claimed_issue_key is None
            or report.job_id is None
            or report.claimed_state_name is None
        ):
            return
        # The pre-claim state travels with the watch, because the watch is
        # what puts it back when the run reaches no terminal outcome.
        self._lifecycle.follow(
            issue_key=report.claimed_issue_key,
            job_id=report.job_id,
            pre_claim_state=report.claimed_state_name,
        )
