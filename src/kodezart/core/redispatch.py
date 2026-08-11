"""Bounded re-dispatch until a session returns a conforming id set.

Inside the node, and deliberately NOT the graph's ``RetryPolicy``: that
policy re-runs the node and PROPAGATES once its attempts are spent, which
ends the run, while an exhausted permutation guard has to fall through to
the fail-closed grading with the dispatched denominator intact.  A guard
whose exhaustion kills the run is a guard nobody can afford to arm.

The bound is a caller-supplied configuration value, never a literal here:
one attempt is a whole judgment session, so the number is a cost decision
an operator owns.

*check* raises the guard's error; this helper catches it and dispatches
again, and RETURNS the one still standing when the bound is spent.  The
caller decides what an unresolved breach means, because the two channels
answer differently — one grades fail-closed, one re-raises and halts.
"""

from collections.abc import Awaitable, Callable

from kodezart.core.logging import BoundLogger
from kodezart.domain.errors import CriteriaFanInError
from kodezart.types.domain.agent import RaiseSite


async def until_permutation[T](
    *,
    dispatch: Callable[[], Awaitable[T]],
    check: Callable[[T], object],
    max_attempts: int,
    site: RaiseSite,
    log: BoundLogger,
) -> tuple[T, CriteriaFanInError | None, int]:
    """Dispatch until *check* passes, or until *max_attempts* are spent.

    Returns the last output, the breach still standing (``None`` when the
    returned set was a permutation), and the attempts the guard spent.
    Every attempt that failed the check is named in the log with its ids,
    so an operator sees a run paying for re-dispatch before the exhausted
    case ever reaches the wire.
    """
    attempt = 0
    while True:
        attempt += 1
        output = await dispatch()
        try:
            check(output)
        except CriteriaFanInError as breach:
            unresolved = breach
        else:
            return output, None, attempt
        await log.awarning(
            "fan_in_permutation_breach",
            site=site,
            attempt=attempt,
            max_attempts=max_attempts,
            missing_ids=list(unresolved.missing_ids),
            unknown_ids=list(unresolved.unknown_ids),
            duplicate_ids=list(unresolved.duplicate_ids),
        )
        if attempt >= max_attempts:
            return output, unresolved, attempt
