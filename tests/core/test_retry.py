"""Tests for shared retry predicate."""

import inspect
import time
from typing import Final, cast

import pytest
from langchain_core.runnables import RunnableConfig

from kodezart.composition.engine import rate_limit_delay_floor
from kodezart.core import retry as retry_module
from kodezart.core.config import AppConfig
from kodezart.core.errors import NoStructuredOutputError, soft_failure
from kodezart.core.retry import DelayFloor, GraphNode, RetryFloor, should_retry
from kodezart.domain.errors import ForgeAPIError, RateLimitError, TransientAPIError
from kodezart.types.domain.agent import RaiseSite


def test_transient_api_error_is_retryable() -> None:
    """TransientAPIError triggers retry."""
    assert should_retry(TransientAPIError("transient")) is True


def test_rate_limit_error_is_retryable() -> None:
    """RateLimitError (subclass of TransientAPIError) triggers retry."""
    assert should_retry(RateLimitError("rate limited")) is True


def test_connection_error_is_retryable() -> None:
    """OS-level ConnectionError triggers retry."""
    assert should_retry(ConnectionError("reset")) is True


@pytest.mark.parametrize("status_code", [422, 429, 502, None])
def test_a_forge_api_error_is_never_retryable(status_code: int | None) -> None:
    """``ForgeAPIError`` is the non-retryable arm — whatever status it carries.

    The adapter classifies first: a 429 or a 5xx that is still worth
    retrying never becomes a ``ForgeAPIError`` at all, it becomes a
    ``RateLimitError`` or a ``TransientAPIError`` once the adapter's own
    budget is spent.  Re-deciding that here on the status code would put
    a second classifier behind the first, and the two would drift.

    ``None`` is the statusless arm — a decode failure, a redirect loop,
    a URL that would not build — and a predicate reading the status to
    decide would have no answer for it at all.
    """
    exc = ForgeAPIError(
        "refused",
        status_code=status_code,
        detail="POST /repos/owner/repo/pulls",
    )

    assert should_retry(exc) is False


def test_the_predicate_names_no_vendor_exception_type() -> None:
    """No transport's exception types reach this decision — domain shapes only.

    The escape this closes: a vendor error classified HERE is classified
    a second time, after the adapter that owns the vendor already
    classified it, and nothing keeps the two statements in agreement.
    The module is swept for the transport it used to read, because a
    behavioural test cannot see an arm that only a re-introduced import
    would make reachable.
    """
    source = inspect.getsource(retry_module)

    assert "httpx" not in source


def test_runtime_error_not_retryable() -> None:
    """RuntimeError falls through to False."""
    assert should_retry(RuntimeError("unexpected")) is False


def test_value_error_not_retryable() -> None:
    """ValueError falls through to False."""
    assert should_retry(ValueError("bad input")) is False


def test_no_structured_output_error_does_not_satisfy_should_retry() -> None:
    """NoStructuredOutputError is a peer of AgentSDKError — NOT retry-eligible.

    Locks in the peer-not-subclass relationship with the transient
    exception hierarchy.  If a future refactor accidentally re-parents
    ``NoStructuredOutputError`` under ``TransientAPIError``, this test fails
    loudly and prevents silent retry storms on deterministic failures.
    """
    exc = NoStructuredOutputError(
        "no structured output",
        raise_site="ticket_creator",
        result_event=None,
    )
    assert should_retry(exc) is False


# ---------------------------------------------------------------------------
# KOD-43/AC-5 — the out-of-scope list, held as a test
# ---------------------------------------------------------------------------

#: Every site that drains a stream and raises on no usable output.
RAISE_SITES: Final[list[RaiseSite]] = [
    "ticket_creator",
    "ticket_reviewer",
    "branch_name",
    "acceptance_criteria",
    "criteria_validation",
    "ralph_evaluator",
    "post_merge_review",
    "pr_description",
    "commit_message",
    "remediation_ticket",
]


@pytest.mark.parametrize("raise_site", RAISE_SITES)
def test_only_the_rejection_changes_retryability_at_every_raise_site(
    raise_site: RaiseSite,
) -> None:
    """The partition is by CAUSE, not by site: no site is special-cased.

    The frozen text's out-of-scope list binds the implementation — only
    the rate-limit-rejection case changes.  Asserted at every site
    rather than at the one the report was written about, because a fix
    that only reaches the reported site is the defect's next hiding
    place.  The reported downstream branch-name symptom is included and
    is NOT treated differently: it has no established cause and gets no
    special handling here.
    """
    rejected = soft_failure(
        "no structured output",
        raise_site=raise_site,
        result_event=None,
        rate_limit_rejected=True,
    )
    deterministic = soft_failure(
        "no structured output",
        raise_site=raise_site,
        result_event=None,
        rate_limit_rejected=False,
    )

    assert should_retry(rejected) is True
    assert should_retry(deterministic) is False


def test_the_predicate_itself_names_no_soft_failure_class() -> None:
    """``should_retry`` was not widened — it still decides on transience alone.

    The ruling's mechanism is that the rejection stops being routed
    through the class the predicate excludes.  A future shortcut that
    instead teaches the predicate about soft failures would satisfy the
    behavioural tests above and violate the ruling, so the source is
    swept for the class names it must not mention.
    """
    source = inspect.getsource(retry_module)

    assert "NoStructuredOutputError" not in source
    assert "RateLimitedSoftFailureError" not in source
    assert "rate_limit_rejected" not in source


# ---------------------------------------------------------------------------
# KOD-195 — the floor a rate-limited attempt waits before the next one
# ---------------------------------------------------------------------------

#: Long enough that a scheduler hiccup cannot account for it, short enough
#: that the suite pays it several times without noticing.
FLOOR_SECONDS: Final[float] = 0.05

#: The floor's own negative: a failure the resolver says nothing about must
#: reach the policy's back-off at once, so the elapsed time of such an
#: attempt has to stay well under the floor.
UNDELAYED_CEILING_SECONDS: Final[float] = FLOOR_SECONDS / 2


def _floor_for_rate_limits(exc: Exception) -> float | None:
    """A resolver of the shape composition supplies, over the domain class."""
    if isinstance(exc, RateLimitError):
        return FLOOR_SECONDS
    return None


async def _elapsed_of(node: object, exc_type: type[Exception] | None) -> float:
    """Drive *node* once and return how long the attempt took."""
    guarded = cast("GraphNode[str, str]", node)
    started = time.perf_counter()
    if exc_type is None:
        await guarded("state", RunnableConfig())
    else:
        with pytest.raises(exc_type):
            await guarded("state", RunnableConfig())
    return time.perf_counter() - started


async def test_a_failure_carrying_a_floor_waits_it_out_before_the_policy_sees_it() -> (
    None
):
    """The measured defect: sixteen sessions in thirty seconds (KOD-174).

    ``RetryPolicy`` holds ONE interval for every failure it matches, so a
    provider limit that wants a minute and a dropped connection that wants
    a second cannot both be expressed there.  The floor rides with the
    exception instead, and is paid where the exception leaves the node —
    before the policy's own back-off begins.
    """

    async def node(state: str, config: RunnableConfig) -> str:
        raise RateLimitError(state)

    elapsed = await _elapsed_of(
        RetryFloor(_floor_for_rate_limits)(node), RateLimitError
    )

    assert elapsed >= FLOOR_SECONDS


async def test_a_failure_the_resolver_names_no_floor_for_is_not_delayed() -> None:
    """The paired negative: the floor is one class's, never every failure's.

    A blanket delay would pace every retry in the graph off a rate limit's
    clock — a dropped connection would wait a minute to be tried again.
    """

    async def node(state: str, config: RunnableConfig) -> str:
        raise ConnectionError(state)

    elapsed = await _elapsed_of(
        RetryFloor(_floor_for_rate_limits)(node),
        ConnectionError,
    )

    assert elapsed < UNDELAYED_CEILING_SECONDS


async def test_a_node_that_answers_pays_no_floor_at_all() -> None:
    """The successful path is untouched — the floor is a failure's price."""

    async def node(state: str, config: RunnableConfig) -> str:
        return state

    elapsed = await _elapsed_of(RetryFloor(_floor_for_rate_limits)(node), None)

    assert elapsed < UNDELAYED_CEILING_SECONDS


def test_every_wrapper_is_built_over_a_resolver() -> None:
    """The floor's resolver is a required dependency (KOD-301).

    Measured at ``b5d1297``: the constructor took ``DelayFloor | None``
    and skipped the resolver entirely for ``None`` — an arm no production
    caller could reach once KOD-282 made the resolver required on all
    three loops, kept alive by the one test that passed ``None``.  A
    caller meaning "no floor" states it in a resolver that answers
    ``None``, which is a decision on the record rather than an omission.
    """
    parameter = inspect.signature(RetryFloor.__init__).parameters["delay_floor_for"]

    assert parameter.default is inspect.Parameter.empty
    assert parameter.annotation == DelayFloor


async def test_a_resolver_that_names_no_floor_for_anything_delays_nothing() -> None:
    """The graph's own back-off, asked for rather than fallen into.

    The behaviour the deleted ``None`` arm provided, now reached the only
    way it can be: a resolver a caller wrote that answers ``None`` to
    every failure it is shown.
    """

    def no_floor_for_anything(exc: Exception) -> float | None:
        _ = exc
        return None

    async def node(state: str, config: RunnableConfig) -> str:
        raise RateLimitError(state)

    elapsed = await _elapsed_of(RetryFloor(no_floor_for_anything)(node), RateLimitError)

    assert elapsed < UNDELAYED_CEILING_SECONDS


def test_the_floor_resolver_is_compositions_statement_not_this_modules() -> None:
    """Which classes are a rate limit is a COMPOSITION decision (KOD-195).

    ``core.retry`` decides transience over the domain taxonomy and nothing
    else — the guard above sweeps it for the soft-failure class names it
    must not mention — so the mapping from a class to a number of seconds
    an operator configured is built where the config is read.

    A rejection that states its own retry-after is honoured verbatim: the
    provider knows when it will answer again, and a configured default
    would either overrule it or be overruled by it.
    """
    config = AppConfig()
    resolve = rate_limit_delay_floor(config)
    rejected = soft_failure(
        "no structured output",
        raise_site="acceptance_criteria",
        result_event=None,
        rate_limit_rejected=True,
    )

    assert resolve(rejected) == config.retry_rate_limit_floor_seconds
    assert resolve(RateLimitError("limited", retry_after=12.0)) == 12.0
    assert resolve(RateLimitError("limited")) == config.retry_rate_limit_floor_seconds
    assert resolve(ConnectionError("reset")) is None
    assert resolve(TransientAPIError("blip")) is None
