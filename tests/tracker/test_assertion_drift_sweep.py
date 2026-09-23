"""The sweep's assertion-drift arm: configured or refused, and held to one head."""

from dataclasses import replace

import pytest

from kodezart.domain.errors import (
    AssertionComparisonError,
    AuditClaimReadError,
    CriterionReadError,
    RulingRecordReadError,
)
from kodezart.services.assertion_drift import AssertionDriftDetector
from kodezart.services.audit_failures import (
    AUDIT_PUBLICATION_FAILURES,
    AUDIT_READ_FAILURES,
    DRIFT_READ_FAILURES,
)
from kodezart.services.recorded_assertion_drift import RecordedAssertionDriftDetector
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.types.domain.assertion_drift import (
    AssertionDeviationClaim,
    AssertionSource,
    ProtectedTestRef,
)
from tests.tracker.test_audit_evidence import Source
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


class UnreadableFamily:
    """A tracker whose criterion membership read cannot establish an answer."""

    async def read_criteria(self, *, issue_key):
        raise CriterionReadError(issue_key=issue_key, reason="the backend timed out")


class ReadSources:
    """A baseline read that answers, so the family read is the one that fails."""

    async def read(self, request):
        return request


async def test_an_unreadable_criterion_family_refuses_the_comparison_not_the_audit(
    setup, tracker
):
    """A family read that fails is the drift arm's unavailability, not the run's."""
    build, *_rest, op = setup
    source = Source()
    drift = RecordedAssertionDriftDetector(
        tracker=UnreadableFamily(),
        sources=ReadSources(),
        rulings=RulingRecordReader(tracker=tracker, operation=op),
        detector=AssertionDriftDetector(git=source),
    )
    child, _parent = (await build(selected_drift=drift).run()).observations
    assert child.target.issue.issue_key == CHILD
    assert child.assertion_drift is None
    assert child.drift_unavailable_reason is not None
    assert child.drift_unavailable_reason.startswith("CriterionReadError: ")


class Comparing:
    """A configured comparison that would report one claim if it were asked."""

    def __init__(self):
        self.requests = []

    async def compare(self, request):
        self.requests.append(request)
        return (claim(head_sha=HEAD),)


class Refusing:
    """A configured comparison that cannot establish a readable pair."""

    async def compare(self, request):
        raise AssertionComparisonError(
            source_ref=request.criterion_key, reason="the graded test holds none"
        )


async def test_a_criterion_target_with_no_native_request_is_refused_by_name(setup):
    """A criterion with no native request is never compared as an empty result."""
    build, *_ = setup
    drift = Comparing()
    sweep = build(selected_drift=drift)
    snapshot = await sweep.prepare()
    child, parent = snapshot.targets
    assert child.issue.issue_key == CHILD
    unaddressed = replace(child, request=None)

    observed = await sweep.observe_target(
        snapshot=replace(snapshot, targets=(unaddressed, parent)), target=unaddressed
    )

    assert observed.assertion_drift is None
    assert observed.drift_unavailable_reason == (
        "AuditClaimReadError: assertion-drift comparison requires a native "
        "criterion request"
    )
    assert drift.requests == []


async def test_a_comparison_that_refuses_is_the_criterions_unavailability(setup):
    build, *_ = setup
    child, _parent = (await build(selected_drift=Refusing()).run()).observations
    assert child.target.issue.issue_key == CHILD
    assert child.assertion_drift is None
    assert child.drift_unavailable_reason is not None
    assert child.drift_unavailable_reason.startswith("AssertionComparisonError: ")


def test_only_the_drift_arm_treats_its_own_refusals_as_unavailability():
    """The drift arm's tuple is the one widened; the shared taxonomy is not."""
    own = {AssertionComparisonError, RulingRecordReadError, CriterionReadError}
    assert set(DRIFT_READ_FAILURES) - set(AUDIT_READ_FAILURES) == own
    assert not own & set(AUDIT_READ_FAILURES)
    assert not own & set(AUDIT_PUBLICATION_FAILURES)
