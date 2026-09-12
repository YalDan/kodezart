"""Mandate output schemas expose the same legal verdicts as their consumers."""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.audit import AuditMandateJudgment, AuditMandateObservation


@pytest.mark.parametrize("model", [AuditMandateJudgment, AuditMandateObservation])
def test_mandate_schema_discriminates_payload_by_verdict(model):
    schema = model.model_json_schema()
    shape = schema
    if "$ref" in shape:
        shape = schema["$defs"][shape["$ref"].removeprefix("#/$defs/")]
    discriminator = shape.get("discriminator")
    assert discriminator is not None
    assert discriminator["propertyName"] == "verdict"
    mapping = discriminator["mapping"]
    assert set(mapping) == {"holds", "refuted", "unverifiable"}
    branches = {
        verdict: schema["$defs"][reference.removeprefix("#/$defs/")]
        for verdict, reference in mapping.items()
    }
    assert branches["refuted"]["properties"]["finding"]["type"] == "null"
    assert branches["unverifiable"]["properties"]["finding"]["type"] == "null"
    assert "finding" in branches["holds"]["required"]
    finding = branches["holds"]["properties"]["finding"]
    assert "$ref" in finding and "anyOf" not in finding
    if model is AuditMandateJudgment:
        assert branches["refuted"]["properties"]["sourceIndex"]["type"] == "null"
        for verdict in ["holds", "unverifiable"]:
            assert branches[verdict]["properties"]["sourceIndex"]["type"] == "integer"
            assert "sourceIndex" in branches[verdict]["required"]
    else:
        assert branches["unverifiable"]["properties"]["unreadable"]["minItems"] == 1
        for verdict in ["holds", "refuted"]:
            assert branches[verdict]["properties"]["unreadable"]["maxItems"] == 0


@pytest.mark.parametrize(
    ("verdict", "finding", "source_index"),
    [("holds", None, 0), ("refuted", None, 0), ("unverifiable", None, None)],
)
def test_invalid_agent_payload_never_crosses_validation(verdict, finding, source_index):
    with pytest.raises(ValidationError):
        AuditMandateJudgment.model_validate(
            {
                "verdict": verdict,
                "finding": finding,
                "source_index": source_index,
                "evidence": "Actual source evidence.",
            }
        )
