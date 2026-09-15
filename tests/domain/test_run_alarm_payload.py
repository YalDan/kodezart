"""Alarm payloads carry observations and provenance, with no diagnosis field."""

import json

import pytest
from pydantic import ValidationError

from kodezart.types.domain.run_alarm import RunAlarm
from tests.domain.test_run_alarm import alarm_data


@pytest.mark.parametrize("bounded", [False, True])
def test_alarm_payload_contains_exactly_the_declared_six_fields(bounded):
    data = alarm_data()
    if not bounded:
        data["bound"] = None
    alarm = RunAlarm.model_validate(data)
    payload = json.loads(alarm.model_dump_json(by_alias=True))
    assert set(payload) == {
        "subject",
        "signal",
        "readings",
        "bound",
        "raisedAtSha",
        "raisedBy",
    }
    assert payload["readings"] == [
        {
            "sourceRef": "escalation/one",
            "value": {"kind": "text", "value": "UNRESOLVED\n"},
            "atSha": "0000000",
        },
        {
            "sourceRef": "count/one",
            "value": {"kind": "text", "value": " 004 "},
            "atSha": None,
        },
    ]
    assert payload["bound"] == (
        {
            "configField": "run_alarm_escalation_age_max_commits",
            "configuredValue": 3,
            "observedValue": 4,
        }
        if bounded
        else None
    )
    assert RunAlarm.model_validate(payload) == alarm


@pytest.mark.parametrize(
    "field", ["diagnosis", "remediation", "cause", "explanation", "severity", "message"]
)
@pytest.mark.parametrize("placement", ["alarm", "subject", "reading", "bound"])
def test_explanatory_fields_cannot_be_smuggled_into_an_alarm(field, placement):
    data = alarm_data()
    target = {
        "alarm": data,
        "subject": data["subject"],
        "reading": data["readings"][0],
        "bound": data["bound"],
    }[placement]
    target[field] = "Invented causal explanation or instruction"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RunAlarm.model_validate(data)
