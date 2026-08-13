"""Bounded re-dispatch until a session returns a conforming answer.

Inside the node, and deliberately NOT the graph's ``RetryPolicy``: that
policy re-runs the node and PROPAGATES once its attempts are spent, which
ends the run, while an exhausted guard has to fall through to the arm its
channel owns.  A guard whose exhaustion kills the run is a guard nobody
can afford to arm.

The bound is a caller-supplied configuration value, never a literal here:
one attempt is a whole judgment session, so the number is a cost decision
an operator owns.

Two refusals reach this helper and they carry different information.  A
non-permutation is a slip in a set the prompt already names, so the same
prompt asked again is what clears it.  A contract violation — a response
the response model refuses, or a verdict its own evidence does not derive
— repeats verbatim under the identical prompt, so the refusal's own text
is the only new information a re-dispatch can carry, and this helper
hands it to the next dispatch.

*check* raises the guard's error; this helper catches it and dispatches
again, and RETURNS the one still standing when the bound is spent.  The
caller decides what an unresolved breach means, because the channels
answer differently — one grades fail-closed, one strikes the statement
and keeps the derivation, one re-raises and halts.  The single case this
helper decides itself is arithmetic rather than policy: when the spent
attempt failed inside *dispatch* there is no output to hand back at all,
so the breach propagates.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from kodezart.core.logging import BoundLogger
from kodezart.domain.errors import CriteriaFanInError
from kodezart.types.domain.agent import RaiseSite
from kodezart.types.domain.criteria import (
    ContractBreach,
    ContractCorrection,
    CorrectionOutcome,
)

CORRECTION_HEADER = (
    "The previous response to this exact prompt was REFUSED before it "
    "reached the run. Return the whole output again, corrected. Change "
    "only what the refusal names."
)


def correction_notice(breach: Exception) -> str | None:
    """The refusal restated for the next dispatch, or nothing to restate.

    A non-permutation names ids the prompt already carries, so restating
    it adds nothing a re-dispatch can act on.  Every other refusal is a
    contract violation whose text names the field and the rule, and a
    dispatch that is not told repeats it.
    """
    if isinstance(breach, CriteriaFanInError):
        return None
    return f"{CORRECTION_HEADER}\n\nREFUSAL: {breach}"


@dataclass(frozen=True, slots=True)
class Redispatched[T, E: Exception]:
    """What a bounded re-dispatch returned, and what it spent getting there.

    ``unresolved`` is the breach still standing when the bound was spent,
    and ``None`` when the returned output conformed.  ``breaches`` is
    every refusal the guard caught in order, so a caller can name on the
    wire what it corrected even when the correction WORKED and nothing is
    left standing.
    """

    output: T
    unresolved: E | None
    breaches: tuple[E, ...]
    attempts: int


async def until_conforming[T, E: Exception](
    *,
    dispatch: Callable[[E | None], Awaitable[T]],
    check: Callable[[T], object],
    correctable: tuple[type[E], ...],
    max_attempts: int,
    site: RaiseSite,
    log: BoundLogger,
) -> Redispatched[T, E]:
    """Dispatch until *check* passes, or until *max_attempts* are spent.

    *dispatch* is handed the previous attempt's breach — ``None`` on the
    first — so a channel whose refusal carries new information can restate
    it.  Both regions are checked: a *correctable* raised while producing
    the output earns a re-dispatch exactly as one raised while checking
    it, which is what puts a response the model rejected inside the guard
    at all.

    Every attempt that failed is named in the log with the refusal's own
    text, so an operator sees a run paying for re-dispatch before the
    exhausted case ever reaches the wire.
    """
    breaches: list[E] = []
    attempt = 0
    while True:
        attempt += 1
        previous = breaches[-1] if breaches else None
        try:
            output = await dispatch(previous)
        except correctable as caught:
            breaches.append(caught)
            await _name_breach(
                log=log,
                site=site,
                attempt=attempt,
                max_attempts=max_attempts,
                breach=caught,
            )
            # Nothing was produced, so there is no output to hand back and
            # no derivation to fall through to: the spent bound propagates.
            if attempt >= max_attempts:
                raise
            continue

        try:
            check(output)
        except correctable as caught:
            breaches.append(caught)
        else:
            return Redispatched(
                output=output,
                unresolved=None,
                breaches=tuple(breaches),
                attempts=attempt,
            )

        await _name_breach(
            log=log,
            site=site,
            attempt=attempt,
            max_attempts=max_attempts,
            breach=breaches[-1],
        )
        if attempt >= max_attempts:
            return Redispatched(
                output=output,
                unresolved=breaches[-1],
                breaches=tuple(breaches),
                attempts=attempt,
            )


def correction_report[T, E: Exception](
    redispatched: Redispatched[T, E],
    *,
    outcome: CorrectionOutcome,
) -> ContractCorrection | None:
    """The wire record of what the guard refused, or nothing to record.

    ``None`` when no attempt was refused, which is the fact "the first
    answer conformed" — the outcome enum carries the other two.
    """
    if not redispatched.breaches:
        return None
    return ContractCorrection(
        breaches=[
            ContractBreach(breach_class=type(breach).__name__, detail=str(breach))
            for breach in redispatched.breaches
        ],
        attempts=redispatched.attempts,
        outcome=outcome,
    )


async def until_permutation[T](
    *,
    dispatch: Callable[[], Awaitable[T]],
    check: Callable[[T], object],
    max_attempts: int,
    site: RaiseSite,
    log: BoundLogger,
) -> tuple[T, CriteriaFanInError | None, int]:
    """The fan-in channel's binding of the guard: the id set is a permutation.

    Returns the last output, the breach still standing (``None`` when the
    returned set was a permutation), and the attempts the guard spent.
    """

    async def restating_nothing(_breach: CriteriaFanInError | None) -> T:
        return await dispatch()

    redispatched = await until_conforming(
        dispatch=restating_nothing,
        check=check,
        correctable=(CriteriaFanInError,),
        max_attempts=max_attempts,
        site=site,
        log=log,
    )
    return (
        redispatched.output,
        redispatched.unresolved,
        redispatched.attempts,
    )


async def _name_breach(
    *,
    log: BoundLogger,
    site: RaiseSite,
    attempt: int,
    max_attempts: int,
    breach: Exception,
) -> None:
    """One refused attempt on the wire, named by the class that refused it."""
    await log.awarning(
        "redispatch_breach",
        site=site,
        attempt=attempt,
        max_attempts=max_attempts,
        breach_class=type(breach).__name__,
        breach=str(breach),
    )
