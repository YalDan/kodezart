"""Independent legal-state, schema and source contract checks for audit unions."""

import json

import pytest
from pydantic import ValidationError

from kodezart.types.domain.agent import AUDIT_MANDATE_SCHEMA
from kodezart.types.domain.audit import (
    AuditMandateJudgment,
    AuditMandateObservation,
    MandateFinding,
)
from kodezart.types.domain.organize import SpecFinding
from tests.tracker.test_audit_mandate import QUOTE, SURFACES, output


def observation(verdict="holds"):
    finding = output()["finding"] if verdict == "holds" else None
    return {
        "verdict": verdict,
        "covered": [
            {"surface": SURFACES[1], "native_ref": "source/actual", "content": QUOTE}
        ]
        if verdict != "unverifiable"
        else [],
        "unreadable": [{"surface": SURFACES[0], "reason": "Native source unavailable"}]
        if verdict == "unverifiable"
        else [],
        "finding": finding,
        "finding_surface": SURFACES[1] if finding else None,
        "evidence": "Fresh source observation",
    }


@pytest.mark.parametrize("verdict", ["holds", "refuted", "unverifiable"])
def test_judgment_and_observation_roundtrip_flat_json(verdict):
    raw = output(verdict=verdict)
    if verdict != "holds":
        raw["finding"] = None
    if verdict == "refuted":
        raw["source_index"] = None
    judgment = AuditMandateJudgment.model_validate(raw)
    observed = AuditMandateObservation.model_validate(observation(verdict))
    for value in (judgment, observed):
        wire = json.loads(value.model_dump_json(by_alias=True))
        assert "root" not in wire and wire["verdict"] == verdict
        assert type(value).model_validate(wire) == value
        assert (
            type(value).model_validate_json(value.model_dump_json(), strict=True)
            == value
        )
    if verdict == "holds":
        assert type(judgment.finding) is MandateFinding
        assert isinstance(judgment.finding, SpecFinding)


@pytest.mark.parametrize("field", ["verdict", "evidence", "finding", "source_index"])
@pytest.mark.parametrize("verdict", ["holds", "refuted", "unverifiable"])
def test_all_wire_fields_remain_required_in_every_judgment_arm(field, verdict):
    raw = output(
        verdict=verdict,
        finding=output()["finding"] if verdict == "holds" else None,
        source_index=None if verdict == "refuted" else 1,
    )
    del raw[field]
    with pytest.raises(ValidationError):
        AuditMandateJudgment.model_validate(raw)


@pytest.mark.parametrize(
    "change",
    [
        {"role": "instance", "mandate_text": None},
        {"mandate_text": " "},
        {"mandate_text": None},
        {"diagnosis": "invented"},
    ],
)
def test_mandate_specialization_refuses_wrong_payload_before_session_consumer(change):
    raw = output()
    raw["finding"].update(change)
    with pytest.raises(ValidationError):
        AuditMandateJudgment.model_validate(raw)


def test_agent_schema_is_actual_closed_union_and_mandate_specialization():
    schema = AUDIT_MANDATE_SCHEMA
    assert schema == AuditMandateJudgment.model_json_schema()
    shape = schema["$defs"][schema["$ref"].removeprefix("#/$defs/")]
    assert set(shape["discriminator"]["mapping"]) == {
        "holds",
        "refuted",
        "unverifiable",
    }
    finding = schema["$defs"]["MandateFinding"]
    assert finding["properties"]["role"]["const"] == "mandate"
    assert finding["properties"]["mandateText"]["type"] == "string"
    assert {"role", "mandateText"} <= set(finding["required"])


@pytest.mark.parametrize("index", [True, 1.0, "1"])
def test_source_index_requires_actual_integer(index):
    with pytest.raises(ValidationError):
        AuditMandateJudgment.model_validate(output(source_index=index))


@pytest.mark.parametrize("damage", ["empty-coverage", "foreign-source"])
def test_holds_observation_cannot_claim_an_uncovered_source(damage):
    raw = observation()
    if damage == "empty-coverage":
        raw["covered"] = []
    else:
        raw["finding_surface"] = SURFACES[0]
    with pytest.raises(ValidationError):
        AuditMandateObservation.model_validate(raw)


def test_holds_finding_identity_matches_covered_native_surface():
    raw = observation()
    raw["finding"]["issue_id"] = "different-native-issue"
    with pytest.raises(ValidationError):
        AuditMandateObservation.model_validate(raw)


def test_base_instance_cannot_substitute_for_required_mandate_specialization():
    raw = output()
    raw["finding"] = SpecFinding(
        issue_id="source", defect_class="class", evidence="instance", role="instance"
    )
    with pytest.raises(ValidationError):
        AuditMandateJudgment.model_validate(raw)
