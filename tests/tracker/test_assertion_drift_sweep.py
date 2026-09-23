"""The sweep's assertion-drift arm: configured or refused, and held to one head."""

from dataclasses import replace

import pytest

from kodezart.domain.errors import AuditClaimReadError
from kodezart.types.domain.assertion_drift import (
    AssertionDeviationClaim,
    AssertionSource,
    ProtectedTestRef,
)
from tests.tracker.test_audit_sweep import CHILD, HEAD, PRIOR, ROOT
from tests.tracker.test_audit_sweep import server as server
from tests.tracker.test_audit_sweep import setup as setup


def claim(*, head_sha: str) -> AssertionDeviationClaim:
    """One deviation of the protected assertion, observed at *head_sha*."""
    return AssertionDeviationClaim(
        protected_test=ProtectedTestRef(
            source_ref="protection-record-comment",
            path="tests/test_contract.py",
            qualified_name="test_contract",
        ),
        graded_sha=PRIOR,
        head_sha=head_sha,
        graded_blob_sha="graded-blob",
        head_blob_sha="head-blob",
        before=(
            AssertionSource(
                line=5, expression="implementation() == 1", structural_form="Compare"
            ),
        ),
        after=(
            AssertionSource(
                line=5, expression="implementation() == 2", structural_form="Compare"
            ),
        ),
    )


async def test_a_sweep_with_no_comparison_refuses_the_criterion_by_name(setup):
    """An unconfigured arm is a named unavailability, never a quiet default."""
    build, *_ = setup
    child, _parent = (await build().run()).observations
    assert child.target.issue.issue_key == CHILD
    assert child.assertion_drift is None
    assert child.drift_unavailable_reason == (
        "AuditClaimReadError: the assertion-drift comparison is not configured"
    )


async def test_a_target_that_is_not_a_criterion_is_not_compared(setup):
    build, *_ = setup
    _child, parent = (await build().run()).observations
    assert parent.target.issue.issue_key == ROOT
    assert parent.assertion_drift is None
    assert parent.drift_unavailable_reason is None


async def test_a_claim_about_another_head_refuses_the_read_as_current(setup):
    """A claim carries the head it read, and it must be the other arms' head."""
    build, *_ = setup
    sweep = build()
    result = await sweep.run()
    child, parent = result.observations
    assert child.claim is not None and child.claim.claim.head_sha == HEAD

    # Not vacuous: a claim at the arms' own head is current.
    await sweep.require_current(
        result.sources,
        (replace(child, assertion_drift=(claim(head_sha=HEAD),)), parent),
    )
    with pytest.raises(AuditClaimReadError, match="different branch heads"):
        await sweep.require_current(
            result.sources,
            (replace(child, assertion_drift=(claim(head_sha="f" * 40),)), parent),
        )
