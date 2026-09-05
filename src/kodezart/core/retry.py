"""Shared retry predicate and delay floor for all LangGraph RetryPolicy nodes."""

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from langchain_core.runnables import RunnableConfig

from kodezart.domain.errors import TransientAPIError


def should_retry(exc: Exception) -> bool:
    """Return True for genuinely transient failures that warrant a retry.

    Reads the domain taxonomy alone.  An adapter that talks to a vendor
    classifies that vendor's failures at its own boundary and raises the
    domain error the classification produced, so a transport's exception
    types are never a second, competing statement of what is transient.

    - ``TransientAPIError`` (and subclass ``RateLimitError``) — retry-eligible
      by design.
    - ``ConnectionError`` — OS-level network failures.
    - Everything else (``ForgeAPIError``, ``AgentSDKError``, ``RuntimeError``,
      ``ValueError``, etc.) falls through to False.
    """
    if isinstance(exc, TransientAPIError):
        return True
    return isinstance(exc, ConnectionError)


#: Seconds a failing attempt must wait before the graph's own back-off
#: begins, or ``None`` when the failure carries no floor of its own.  Which
#: failures carry one is the CALLER's statement: this module decides
#: transience and nothing else, and a floor is a fact about a particular
#: provider rejection that composition names.
DelayFloor = Callable[[Exception], float | None]

#: Set on a rejection by the wrapper that slept its floor, so the wrappers
#: it climbs through on the way out do not sleep it again.  The mark rides
#: the exception because the exception IS the rejection: one provider
#: refusal, one wait (KOD-195).
_FLOOR_PAID = "_kodezart_retry_floor_paid"

_StateT = TypeVar("_StateT")
_ReturnT = TypeVar("_ReturnT")
_StateT_contra = TypeVar("_StateT_contra", contravariant=True)
_ReturnT_co = TypeVar("_ReturnT_co", covariant=True)


class GraphNode(Protocol[_StateT_contra, _ReturnT_co]):
    """One graph node as LangGraph calls it: state, config, awaited result."""

    def __call__(
        self,
        state: _StateT_contra,
        config: RunnableConfig,
    ) -> Awaitable[_ReturnT_co]:
        """Run the node over *state* under *config*."""
        ...


class RetryFloor:
    """The minimum a failing node attempt waits before the graph retries it.

    ``RetryPolicy`` holds ONE interval for every failure it matches, and a
    provider that answers a rejection with its own retry-after states a
    different one per rejection — so the floor rides with the exception
    and is applied where the exception leaves the node.  The policy's
    attempt budget and its ``retry_on`` predicate are untouched: the
    measured defect (KOD-174) was sixteen sessions spawned seconds apart
    under one standing provider limit, not the retrying itself.

    Paid by every failing attempt, the last one in the budget included:
    what crosses this boundary is an exception, and how many attempts the
    policy has left is the policy's own state.  A run whose budget ends
    under a standing limit therefore surfaces its terminal frame a floor
    later, which is the same wait the next attempt would have cost.

    The resolver is required.  A caller that means "no floor for any
    failure" says so in a resolver that answers ``None``, which is a
    statement someone made; an absent resolver was the same silence
    KOD-282 removed from the three loops one layer up.

    A floor is charged ONCE PER REJECTION, not once per node that
    re-raises it.  The graphs nest — the workflow engine wraps the node
    that runs the ralph loop, which wraps its own — so one rejection
    climbing out of a leaf passed through every wrapper above it and slept
    the floor at each: measured twelve floors, twelve minutes, for a
    single standing rate limit that still opened the same nine sessions.
    The exception carries the fact that it has already waited, and a
    wrapper that meets a rejection carrying it re-raises without sleeping
    again.
    """

    def __init__(self, delay_floor_for: DelayFloor) -> None:
        self._delay_floor_for: DelayFloor = delay_floor_for

    def __call__(
        self,
        node: GraphNode[_StateT, _ReturnT],
    ) -> GraphNode[_StateT, _ReturnT]:
        """Wrap *node* so a failure carrying a floor waits it out first."""
        resolve = self._delay_floor_for

        @functools.wraps(node)
        async def guarded(state: _StateT, config: RunnableConfig) -> _ReturnT:
            try:
                return await node(state, config)
            except Exception as exc:
                if not getattr(exc, _FLOOR_PAID, False):
                    floor = resolve(exc)
                    if floor is not None:
                        await asyncio.sleep(floor)
                        setattr(exc, _FLOOR_PAID, True)
                raise

        return guarded
