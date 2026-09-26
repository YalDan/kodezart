"""A leased alarm record write names its holder before it reads anything."""

import pytest

from kodezart.domain.errors import SurfaceLeaseError
from kodezart.domain.run_alarm_record import (
    alarm_event_key,
    alarm_event_lane,
    require_alarm_holder,
)
from kodezart.types.domain.run_alarm import (
    AlarmReading,
    AlarmSignal,
    CountEvidence,
    EscalationSubject,
    LaneSubject,
    RunAlarm,
)
from kodezart.types.domain.scope import ScopeKind
from kodezart.types.domain.surface import SurfaceKind

ISSUE = "ISSUE-7"
MARKER = "[configured-alarm:lane%2Fone:tally_unmoved]"
READING = AlarmReading(source_ref="scope", value=CountEvidence(value=0))


@pytest.mark.parametrize("holder", [None, "", " ", "\t\n"])
def test_a_blank_or_absent_holder_refuses_with_the_addressed_surface(holder):
    with pytest.raises(SurfaceLeaseError) as refusal:
        require_alarm_holder(issue_key=ISSUE, marker=MARKER, holder=holder)
    assert refusal.value.surface_kind == SurfaceKind.MARKER_COMMENT.value
    assert refusal.value.scope_kind == ScopeKind.ISSUE.value
    assert refusal.value.scope_key == ISSUE
    assert refusal.value.marker == MARKER
    assert refusal.value.current_holder is None


@pytest.mark.parametrize("holder", ["job-1", " job-1 ", "0"])
def test_a_holder_with_any_content_at_all_is_admitted(holder):
    assert require_alarm_holder(issue_key=ISSUE, marker=MARKER, holder=holder) is None


def test_the_tally_event_key_is_unchanged_and_each_question_has_its_own():
    """A lane's tally keeps the key its stream already carries; each open
    question on the lane is keyed by its own occurrence, so two questions'
    transitions are two streams rather than one.
    """
    tally = RunAlarm(
        subject=LaneSubject(scope_key="scope", lane_key="LANE-B"),
        signal=AlarmSignal.TALLY_UNMOVED,
        readings=(READING,),
        bound=None,
        raised_at_sha="head",
        raised_by="holder",
    )
    questions = [
        RunAlarm(
            subject=EscalationSubject(
                scope_key="scope", member_id=key, lane_key="LANE-B", issue_id="LANE-B"
            ),
            signal=AlarmSignal.ESCALATION_AGEING,
            readings=(READING,),
            bound=None,
            raised_at_sha="head",
            raised_by="holder",
        )
        for key in ("LANE-B/check:lapse", "LANE-B/second:lapse")
    ]

    assert alarm_event_key(tally) == "tally_unmoved"
    assert [alarm_event_key(record) for record in questions] == [
        "escalation_ageing:LANE-B/check:lapse",
        "escalation_ageing:LANE-B/second:lapse",
    ]
    assert [alarm_event_lane(record) for record in (tally, *questions)] == [
        "LANE-B"
    ] * 3
