"""A completed audit report exposes only valid claim/mandate combinations."""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.audit import (
    AuditClaimObservation,
    AuditClaimReport,
    AuditMandateObservation,
    AuditVerdict,
)


def claim(verdict):
    return AuditClaimObservation.model_validate(
        {
            "judgment": {
                "criterion_key": "criterion-one",
                "verdict": verdict,
                "evidence": "The independently observed result.",
            },
            "head_sha": "observed-head",
            "record_ref": "native-record",
            "check": "The exact current Check.",
        }
    )


def mandate():
    return AuditMandateObservation.model_validate(
        {
            "verdict": "refuted",
            "covered": [],
            "unreadable": [],
            "finding": None,
            "finding_surface": None,
            "evidence": "The covered set supplies no mandate.",
        }
    )


@pytest.mark.parametrize("verdict", list(AuditVerdict))
@pytest.mark.parametrize("has_mandate", [True, False])
def test_native_model_inputs_obey_the_complete_report_pair(verdict, has_mandate):
    observed = claim(verdict)
    completed = mandate() if has_mandate else None
    if has_mandate != (verdict is AuditVerdict.REFUTED):
        with pytest.raises(ValidationError):
            AuditClaimReport(claim=observed, mandate=completed)
        return
    report = AuditClaimReport(claim=observed, mandate=completed)
    assert report.claim == observed
    assert report.mandate == completed
    assert AuditClaimReport.model_validate_json(report.model_dump_json()) == report
    assert set(report.model_dump()) == {"claim", "mandate"}


@pytest.mark.parametrize("refuted", [True, False])
def test_json_schema_expresses_required_mandate_and_nested_verdict(refuted):
    schema = AuditClaimReport.model_json_schema()

    def resolve(node):
        return schema["$defs"][node["$ref"].rsplit("/", 1)[1]]

    arms = [resolve(node) for node in schema["anyOf"]]
    assert len(arms) == 2
    selected = [
        node
        for node in arms
        if (node["properties"]["mandate"].get("type") != "null") == refuted
    ]
    assert len(selected) == 1
    arm = selected[0]
    assert set(arm["required"]) == {"claim", "mandate"}
    observation = resolve(arm["properties"]["claim"])
    judgment = resolve(observation["properties"]["judgment"])
    actual = judgment["properties"]["verdict"]
    if refuted:
        assert actual["const"] == "refuted"
        assert "$ref" in arm["properties"]["mandate"]
    else:
        assert set(actual["enum"]) == {"holds", "unverifiable"}
        assert arm["properties"]["mandate"]["type"] == "null"
