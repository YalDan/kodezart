"""Tests for shared retry predicate."""

import inspect
from typing import Final

import pytest

from kodezart.core import retry as retry_module
from kodezart.core.errors import NoStructuredOutputError, soft_failure
from kodezart.core.retry import should_retry
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
