"""The running tick's budget, and the one wait that is not charged to it.

A scheduled pass runs under a budget (``pass_scheduler``), and a session
the provider stops on a rate limit is waited out inside that same pass
(``rate_limit_backoff``).  Charged to the budget, a wait longer than it
cancels the pass mid-sleep: its closing acts never run, the tick reports
``timed_out``, and the next interval meets the same limit (KOD-1303).

The wait is the provider's time, not the pass's work, so it lies outside
the tick's clock: the scheduler publishes its budget here for the length
of the tick, and the backoff moves that deadline out by exactly the wait
it is about to sleep.  Everything else the pass does stays bounded as
before, and a session outside any tick (a job) finds no budget and moves
nothing.  The published budget travels with the tick's context, so a
session the pass runs in a task of its own still reaches it.
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_RUNNING: ContextVar[asyncio.Timeout | None] = ContextVar(
    "kodezart_tick_budget", default=None
)


@contextmanager
def running_tick(budget: asyncio.Timeout) -> Iterator[None]:
    """Publish *budget* as the running tick's for the length of the block."""
    token = _RUNNING.set(budget)
    try:
        yield
    finally:
        _RUNNING.reset(token)


def leave_off_the_clock(seconds: float) -> None:
    """Move the running tick's deadline out by *seconds*, when there is one.

    A budget that has already fired is left alone: its cancellation is on
    its way and moving the deadline cannot recall it.  A budget whose tick
    has ended (a session that outlived its tick in a task of its own) is
    left alone too, because there is no tick left to bound.
    """
    budget = _RUNNING.get()
    if budget is None or budget.expired():
        return
    deadline = budget.when()
    if deadline is None:
        return
    try:
        budget.reschedule(deadline + seconds)
    except RuntimeError:
        return
