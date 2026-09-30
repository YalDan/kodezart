"""One ageing record per open question: what the next tick should leave there.

Every expected outcome is written out here rather than derived from the rule
under test, so the table and the code are compared and not merely both read
off one expression.
"""

import pytest

from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.escalation_age_record import (
    ScopePosition,
    anchor_reading,
    commits_since,
    is_ageing_raised,
    next_ageing_record,
)
from kodezart.domain.run_shape import (
    ESCALATION_COMMITS_BOUND,
    ESCALATION_TICKS_BOUND,
    escalation_ageing,
)
from kodezart.types.domain.escalation import (
    EscalationResolution,
    EscalationResolutionState,
)
from kodezart.types.domain.run_alarm import (
    AlarmBound,
    AlarmReading,
    AlarmSignal,
    CountEvidence,
    EscalationEvidence,
    EscalationSubject,
    ReferencesEvidence,
    ResolutionEvidence,
    RunAlarm,
)
from kodezart.types.domain.run_state import LaneEscalation

SCOPE = "scoped-project"
LANE = "LANE-B"
KEY = "LANE-B/check:lapse"
HOLDER = "fixture/supervisor"
HEAD = "head-sha"
QUESTION_REF = "question-comment"
SUBJECT = EscalationSubject(
    scope_key=SCOPE, member_id=KEY, lane_key=LANE, issue_id=LANE
)
UNRESOLVED = EscalationResolution(
    state=EscalationResolutionState.UNRESOLVED, decision_ref=None
)
RESOLVED = EscalationResolution(
    state=EscalationResolutionState.RESOLVED, decision_ref="decision-comment"
)
ANCHOR = AlarmReading(source_ref=SCOPE, value=ReferencesEvidence(value=("b1", "c1")))


def observation(*, subject=SUBJECT, commits=("raised", "between", HEAD)):
    """The collector's reading of the question, past its commit bound of one."""
    question = LaneEscalation(
        issue_id=LANE,
        escalation_key=subject.member_id,
        raised_by="fire",
        question="Re-derive or re-state the observation.",
        interim_reading="The criterion is returned to unstarted.",
        interim_basis="src/",
        raised_at_sha="raised",
    )
    escalation = AlarmReading(
        source_ref=QUESTION_REF,
        value=EscalationEvidence(value=question),
        at_sha="raised",
    )
    observed = escalation_ageing(
        subject=subject,
        readings=(
            escalation,
            AlarmReading(
                source_ref=QUESTION_REF, value=ResolutionEvidence(value=UNRESOLVED)
            ),
            AlarmReading(
                source_ref="lane-record",
                value=ReferencesEvidence(value=commits),
                at_sha=HEAD,
            ),
            AlarmReading(source_ref=SCOPE, value=CountEvidence(value=0)),
            AlarmReading(
                source_ref=ESCALATION_COMMITS_BOUND, value=CountEvidence(value=1)
            ),
            AlarmReading(
                source_ref=ESCALATION_TICKS_BOUND, value=CountEvidence(value=10)
            ),
        ),
        raised_at_sha=HEAD,
        raised_by=HOLDER,
    )
    assert observed is not None
    return observed


OBSERVED = observation()
QUIET = RunAlarm(
    subject=SUBJECT,
    signal=AlarmSignal.ESCALATION_AGEING,
    readings=(ANCHOR,),
    bound=None,
    raised_at_sha=HEAD,
    raised_by=HOLDER,
)
RAISED = RunAlarm(
    subject=SUBJECT,
    signal=AlarmSignal.ESCALATION_AGEING,
    readings=(ANCHOR, *OBSERVED.readings),
    bound=OBSERVED.bound,
    raised_at_sha=HEAD,
    raised_by=HOLDER,
)


@pytest.mark.parametrize(
    ("orders", "expected"),
    [
        pytest.param({"LANE-B": ("b0", "b1")}, 0, id="unchanged"),
        pytest.param({"LANE-C": ("c0", "c1", "c2", "c3")}, 2, id="plus-two"),
        pytest.param({"LANE-D": ("d0", "d1", "d2")}, 3, id="new-since-anchor"),
        pytest.param({"LANE-B": ("x0", "x1")}, 2, id="anchored-head-gone"),
        pytest.param(
            {"LANE-B": ("b0", "b1"), "LANE-C": ("c1", "c2"), "LANE-D": ("d0",)},
            2,
            id="summed-across-lanes",
        ),
    ],
)
def test_commits_since_counts_each_lane_after_its_anchored_head(orders, expected):
    assert commits_since(anchor=ANCHOR, position=ScopePosition(orders=orders)) == (
        expected
    )


def test_the_anchor_is_every_recorded_lane_head_sorted():
    position = ScopePosition(orders={"LANE-C": ("c0", "c1"), "LANE-B": ("b1",)})

    assert anchor_reading(scope_key=SCOPE, position=position) == ANCHOR
    assert position.head_of("LANE-C") == "c1"
    assert position.head_of("LANE-E") is None


@pytest.mark.parametrize(
    ("stored", "observed", "resolution", "expected"),
    [
        (None, None, RESOLVED, None),
        (None, None, UNRESOLVED, "anchor"),
        (None, OBSERVED, RESOLVED, None),
        (None, OBSERVED, UNRESOLVED, "raise"),
        (QUIET, None, RESOLVED, None),
        (QUIET, None, UNRESOLVED, None),
        (QUIET, OBSERVED, RESOLVED, "raise"),
        (QUIET, OBSERVED, UNRESOLVED, "raise"),
        (RAISED, None, RESOLVED, "anchor"),
        (RAISED, None, UNRESOLVED, "anchor"),
        (RAISED, OBSERVED, RESOLVED, None),
        (RAISED, OBSERVED, UNRESOLVED, None),
    ],
)
def test_each_row_of_the_next_record_table(stored, observed, resolution, expected):
    written = next_ageing_record(
        subject=SUBJECT,
        stored=stored,
        anchor=ANCHOR,
        observed=observed,
        resolution=resolution,
        raised_at_sha=HEAD,
        raised_by=HOLDER,
    )

    if expected is None:
        assert written is None
    elif expected == "anchor":
        assert written == QUIET
        assert not is_ageing_raised(written)
    else:
        assert written == RAISED
        assert is_ageing_raised(written)
        assert written.bound == AlarmBound(
            config_field=ESCALATION_COMMITS_BOUND, configured_value=1, observed_value=2
        )


def test_a_stored_record_whose_replay_differs_from_its_bound_refuses():
    carried = RAISED.model_copy(update={"bound": None})

    with pytest.raises(RunShapeReadError, match="replays to a bound"):
        is_ageing_raised(carried)


def test_an_answered_question_is_never_anchored():
    assert (
        next_ageing_record(
            subject=SUBJECT,
            stored=None,
            anchor=ANCHOR,
            observed=None,
            resolution=RESOLVED,
            raised_at_sha=HEAD,
            raised_by=HOLDER,
        )
        is None
    )


def test_an_observation_about_another_subject_refuses():
    another = observation(
        subject=SUBJECT.model_copy(update={"member_id": "LANE-B/second:lapse"})
    )

    with pytest.raises(RunShapeReadError, match="another question"):
        next_ageing_record(
            subject=SUBJECT,
            stored=None,
            anchor=ANCHOR,
            observed=another,
            resolution=UNRESOLVED,
            raised_at_sha=HEAD,
            raised_by=HOLDER,
        )
