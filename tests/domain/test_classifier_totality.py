"""Both outcome classifiers are total: every arm returns, or it raises.

KOD-77-AC-11: neither the fire-level nor the lane-level classifier carries
a default arm, so an input matching no predicate raises instead of being
silently absorbed into some neighbouring terminal.
"""

import itertools

import pytest

from kodezart.domain.outcome import classify_outcome
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.check_observation import AbsentChecks, ObservedChecks
from kodezart.types.domain.delivery import CheckRedClass, classify_lane_delivery
from kodezart.types.domain.outcome import WorkflowOutcome
from tests.chains.test_fire_extraction import DELIVERY_FIELDS
from tests.domain.test_outcome import _state, _trajectory

#: The fire classifier reads only these state fields; the rest are delivery.
_VERDICTS = (AcceptVerdict.accepted, AcceptVerdict.rejected)
_TRAJECTORIES = (None, _trajectory(plateaued=True), _trajectory(plateaued=False))

_ABSENT = AbsentChecks(summary="No check run appeared within the grace window.")
_GREEN = ObservedChecks(
    commit_sha="c" * 40,
    checks_passed=True,
    check_names=frozenset({"ci/test"}),
    failed_check_names=frozenset(),
    summary="All checks passed.",
)
_RED = ObservedChecks(
    commit_sha="c" * 40,
    checks_passed=False,
    check_names=frozenset({"ci/test"}),
    failed_check_names=frozenset({"ci/test"}),
    summary="ci/test failed.",
)
_OBSERVATIONS = (_ABSENT, _GREEN, _RED)
_RED_CLASSES = (None, *CheckRedClass)


def _fire_state(**overrides):
    """A fire state: the authored fixture without its delivery fields."""
    authored = _state(**overrides)
    return {key: value for key, value in authored.items() if key not in DELIVERY_FIELDS}


def test_unclassifiable_fire_state_raises_there_is_no_default_arm():
    """An accepted run that neither merged nor failed to merge matches nothing."""
    state = _fire_state(verdict=AcceptVerdict.accepted, merged=False, merge_error=None)
    with pytest.raises(ValueError, match="Unclassifiable terminal state"):
        classify_outcome(state)


def test_every_classifiable_fire_state_maps_to_a_member():
    """Swept over the classifier's whole finite predicate space.

    Each state either names a ``WorkflowOutcome`` member or raises; no arm
    returns anything else, and both halves of the partition are populated,
    so the sweep cannot pass by raising everywhere.
    """
    classified = 0
    refused = 0
    for (
        verdict,
        merged,
        merge_error,
        remediation_rounds_used,
        best_iteration_sha,
        review_passed,
        trajectory,
        criteria_infeasible,
    ) in itertools.product(
        _VERDICTS,
        (True, False),
        (None, "ralph diverged from feature"),
        (0, 1),
        (None, "c" * 40),
        (True, False),
        _TRAJECTORIES,
        (True, False),
    ):
        state = _fire_state(
            verdict=verdict,
            merged=merged,
            merge_error=merge_error,
            remediation_rounds_used=remediation_rounds_used,
            best_iteration_sha=best_iteration_sha,
            review_passed=review_passed,
            trajectory=trajectory,
            criteria_infeasible=criteria_infeasible,
        )
        try:
            outcome = classify_outcome(state)
        except ValueError as error:
            assert "Unclassifiable terminal state" in str(error)
            refused += 1
        else:
            assert outcome in set(WorkflowOutcome)
            classified += 1
    assert classified > 0
    assert refused > 0


def test_unclassifiable_lane_delivery_raises_there_is_no_default_arm():
    """Red checks with no persisting class name no lane terminal at all."""
    with pytest.raises(ValueError, match="Unclassifiable lane delivery"):
        classify_lane_delivery(
            observation=_RED,
            red_class=None,
            no_run_at_ref=False,
            stalled=False,
            remediation_pending=False,
        )


def test_unclassifiable_lane_delivery_is_not_rescued_by_a_transient_class():
    """A runner flake is not a persisting class, so it classifies nothing."""
    with pytest.raises(ValueError, match="Unclassifiable lane delivery"):
        classify_lane_delivery(
            observation=_RED,
            red_class=CheckRedClass.RUNNER_FLAKE,
            no_run_at_ref=False,
            stalled=False,
            remediation_pending=False,
        )


def test_every_classifiable_lane_delivery_maps_to_a_member():
    """Swept over the lane classifier's whole finite routing-fact space."""
    classified = 0
    refused = 0
    for (
        observation,
        red_class,
        no_run_at_ref,
        stalled,
        remediation_pending,
    ) in itertools.product(
        _OBSERVATIONS, _RED_CLASSES, (True, False), (True, False), (True, False)
    ):
        try:
            outcome = classify_lane_delivery(
                observation=observation,
                red_class=red_class,
                no_run_at_ref=no_run_at_ref,
                stalled=stalled,
                remediation_pending=remediation_pending,
            )
        except ValueError as error:
            assert "Unclassifiable lane delivery" in str(error)
            refused += 1
        else:
            assert outcome in set(WorkflowOutcome)
            classified += 1
    assert classified > 0
    assert refused > 0
