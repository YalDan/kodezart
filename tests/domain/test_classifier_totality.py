"""Both outcome classifiers are total: every arm returns, or it raises.

Neither the fire-level nor the lane-level classifier carries a default arm, so
an input matching no predicate raises instead of being absorbed into some
neighbouring terminal. Beyond the two refusals, the lane classifier's input
vocabulary is held closed against the arms it answers with, so a member added
to that vocabulary without an arm reddens here by name (KOD-323).
"""

import inspect
from typing import get_args

import pytest
from pydantic import ValidationError

from kodezart.domain.outcome import classify_outcome
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.check_observation import AbsentChecks, ObservedChecks
from kodezart.types.domain.delivery import (
    CheckRedClass,
    LaneDelivery,
    classify_lane_delivery,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_state import LanePR
from tests.chains.test_fire_extraction import DELIVERY_FIELDS
from tests.domain.test_outcome import _state

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

#: The arm a red observation takes, by persisting class; a class with no
#: persisting defect names no lane terminal and is refused.  Keyed over the
#: WHOLE input vocabulary so a member that arrives without a row fails by name.
RED_ARMS: dict[CheckRedClass | None, WorkflowOutcome | type[ValueError]] = {
    None: ValueError,
    CheckRedClass.RUNNER_FLAKE: ValueError,
    CheckRedClass.ENVIRONMENT_PREREQUISITE_UNMET: (
        WorkflowOutcome.ci_failed_environment_prerequisite
    ),
    CheckRedClass.UNCLASSIFIED: WorkflowOutcome.ci_failed_unclassified,
    CheckRedClass.WORK_DEFECT: WorkflowOutcome.ci_failed_fix_budget_exhausted,
}

_RED_ARM_ROWS = sorted(RED_ARMS.items(), key=lambda row: str(row[0]))
_RED_ARM_IDS = ["none" if row[0] is None else row[0].value for row in _RED_ARM_ROWS]


def _fire_state(**overrides):
    """A fire state: the authored fixture without its delivery fields.

    Stripping the delivery fields reaches the fire classifier itself rather
    than the authored wrapper the neighbouring module exercises.
    """
    authored = _state(**overrides)
    return {key: value for key, value in authored.items() if key not in DELIVERY_FIELDS}


def _classify_red(red_class: CheckRedClass | None) -> WorkflowOutcome:
    """The lane arm a red observation of *red_class* takes, nothing else set."""
    return classify_lane_delivery(
        observation=_RED,
        red_class=red_class,
        no_run_at_ref=False,
        stalled=False,
        remediation_pending=False,
    )


def test_unclassifiable_fire_state_raises_there_is_no_default_arm():
    """An accepted run that neither merged nor failed to merge matches nothing."""
    state = _fire_state(verdict=AcceptVerdict.accepted, merged=False, merge_error=None)
    with pytest.raises(ValueError, match="Unclassifiable terminal state"):
        classify_outcome(state)


@pytest.mark.parametrize(
    "red_class",
    [None, CheckRedClass.RUNNER_FLAKE],
    ids=["none", "runner_flake"],
)
def test_unclassifiable_lane_delivery_raises_there_is_no_default_arm(red_class):
    """Red checks with no persisting class name no lane terminal at all.

    Neither an absent class nor a transient one is a persisting defect, so the
    classifier refuses both, and the delivery model refuses to be built from
    the same facts on its own clause — matched on that clause's own words,
    because the model re-runs the classifier too and would raise either way.
    """
    with pytest.raises(ValueError, match="Unclassifiable lane delivery"):
        _classify_red(red_class)
    with pytest.raises(ValidationError, match="established persisting class"):
        LaneDelivery(
            lane_key="L",
            issue_id="L",
            head_branch="h",
            base_branch="main",
            final_commit_sha=_RED.commit_sha,
            pr=LanePR(url="https://github.com/o/r/pull/1", number=1, state="open"),
            observation=_RED,
            red_class=red_class,
            no_run_at_ref=False,
            stalled=False,
            remediation_pending=False,
            checks_passed=False,
            checks_summary=_RED.summary,
            outcome=WorkflowOutcome.ci_failed_unclassified,
        )


@pytest.mark.parametrize(("red_class", "expected"), _RED_ARM_ROWS, ids=_RED_ARM_IDS)
def test_each_red_class_takes_exactly_the_arm_the_table_names(red_class, expected):
    """Every class in the vocabulary either names one terminal, or is refused."""
    if expected is ValueError:
        with pytest.raises(ValueError, match="Unclassifiable lane delivery"):
            _classify_red(red_class)
    else:
        assert _classify_red(red_class) is expected


def test_the_lane_classifier_input_vocabulary_is_closed_against_its_arms():
    """A member added to either input vocabulary has no row and reddens here."""
    assert set(RED_ARMS) == {None, *CheckRedClass}
    observation = inspect.signature(classify_lane_delivery).parameters["observation"]
    assert set(get_args(observation.annotation)) == {ObservedChecks, AbsentChecks}
    assert (
        classify_lane_delivery(
            observation=_ABSENT,
            red_class=None,
            no_run_at_ref=False,
            stalled=False,
            remediation_pending=False,
        )
        is WorkflowOutcome.ci_not_configured
    )
    assert (
        classify_lane_delivery(
            observation=_GREEN,
            red_class=None,
            no_run_at_ref=False,
            stalled=False,
            remediation_pending=False,
        )
        is WorkflowOutcome.ci_passed
    )
