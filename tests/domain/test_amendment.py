"""Native semantic addresses and report arithmetic preserve exact identities."""

import pytest
from pydantic import TypeAdapter, ValidationError

from kodezart.domain.amendment import repeated_upheld, upheld_reason
from kodezart.handlers.agent_handler import _queued_event_payload
from kodezart.types.domain.agent import NativeAmendmentEvent
from kodezart.types.domain.agent import RulingId as ExistingRulingId
from kodezart.types.domain.amendment import (
    AmendmentClaim,
    AmendmentJudgment,
    AmendmentReport,
    AmendmentSubject,
    CriterionSubject,
    NativeWriterOutput,
    UpheldAmendment,
    UpheldReason,
)
from kodezart.types.domain.operation import CheckPrerequisite
from kodezart.types.domain.ruling_id import RulingId
from kodezart.types.domain.scope_runtime import ScopeLaneEvent


def record(
    kind="criterion", identity="opaque/criterion", reason="ground_not_reproduced"
):
    subject = TypeAdapter(AmendmentSubject).validate_python(
        {"kind": kind, "id": identity}
    )
    finding = {"verdict": "feasible", "smallest_repair": "none"}
    if reason.startswith("cost_measured"):
        finding["cost_claim"] = {
            "assertion": "A measured cost",
            "measurement": {
                "observed": "Executed once at base; 2 seconds observed",
                "affordable": True,
            },
        }
    judgment = AmendmentJudgment(
        subject=subject,
        base_sha="a" * 40,
        ground="unsatisfiable_at_base",
        reproduced=False,
        finding=finding,
        citations=(),
        measured_by="Recorded base experiment"
        if reason.startswith("cost_measured")
        else None,
    )
    claim = AmendmentClaim(
        subject=subject,
        stage="implementation",
        ground="unsatisfiable_at_base",
        departure="A proposed change",
        claimed_capability=None,
    )
    return UpheldAmendment(claim=claim, reason=reason, judgment=judgment)


def test_same_ruling_newtype_object_reexported_and_native_ids_remain_opaque():
    assert ExistingRulingId is RulingId
    for identity in ["vendor/二", "KOD-97-AC-3", "some key"]:
        assert CriterionSubject(id=identity).id == identity
    for identity in ["", "  ", "\n"]:
        with pytest.raises(ValidationError):
            CriterionSubject(id=identity)


def test_pure_counts_separate_subject_kind_identity_and_reason():
    a = record()
    ruling = record(kind="ruling")
    other_reason = record(reason="cost_measured_affordable")
    reports = [
        AmendmentReport(upheld=items)
        for items in [
            (a, ruling, other_reason),
            (a, ruling),
            (a,),
        ]
    ]
    before = [report.model_dump_json() for report in reports]
    counted = repeated_upheld(reports)
    assert [(item.subject.kind, item.reason, item.count) for item in counted] == [
        ("criterion", UpheldReason.GROUND_NOT_REPRODUCED, 3),
        ("ruling", UpheldReason.GROUND_NOT_REPRODUCED, 2),
    ]
    assert repeated_upheld(reports) == counted
    assert [report.model_dump_json() for report in reports] == before


def test_claim_cannot_carry_writer_reasoning_unknown_stage_or_unknown_ground():
    valid = {
        "subject": {"kind": "criterion", "id": "native/1"},
        "stage": "implementation",
        "ground": "premise_false_at_base",
        "departure": "Different behavior",
        "claimed_capability": None,
    }
    assert NativeWriterOutput(claims=[AmendmentClaim(**valid)])
    for changes in [
        {"reasoning": "trust me"},
        {"stage": "guessed_fix"},
        {"ground": "a_quote_exists"},
        {"claimed_capability": "unknown"},
    ]:
        with pytest.raises(ValidationError):
            AmendmentClaim.model_validate(valid | changes)
    with pytest.raises(ValidationError):
        NativeWriterOutput(claims=[valid, valid])


def test_upheld_record_cannot_misattribute_a_judgment_or_claim_amended():
    value = record().model_dump()
    with pytest.raises(ValidationError):
        UpheldAmendment.model_validate(value | {"verdict": "amended"})
    with pytest.raises(ValidationError):
        UpheldAmendment.model_validate(
            value
            | {"claim": value["claim"] | {"subject": {"kind": "ruling", "id": "other"}}}
        )


def test_actual_scope_egress_roundtrips_required_nulls_and_rejects_bad_native_reports():
    event = ScopeLaneEvent(
        lane_key="lane",
        event=NativeAmendmentEvent(
            report=AmendmentReport(upheld=(record(),)),
        ),
    )
    payload = _queued_event_payload(event)
    assert ScopeLaneEvent.model_validate(payload) == event
    original = payload["event"]["report"]["upheld"][0]
    assert "claimedCapability" in original["claim"]
    assert original["claim"]["claimedCapability"] is None
    original["reason"] = "guessed_reason"
    with pytest.raises(ValidationError):
        ScopeLaneEvent.model_validate(payload)


@pytest.mark.parametrize(
    "claimed,environment,expected",
    [
        (None, {}, "ground_not_reproduced"),
        ("network", None, "ground_not_reproduced"),
        ("network", {CheckPrerequisite.NETWORK: True}, "ground_not_reproduced"),
        ("network", {CheckPrerequisite.NETWORK: False}, "environment_lacks_capability"),
    ],
)
def test_missing_capability_requires_a_typed_claim_absent_from_declared_capabilities(
    claimed,
    environment,
    expected,
):
    value = record()
    claim = AmendmentClaim.model_validate(
        value.claim.model_dump() | {"claimed_capability": claimed}
    )
    judgment = AmendmentJudgment.model_validate(
        value.judgment.model_dump()
        | {
            "finding": {
                "verdict": "unverifiable",
                "smallest_repair": "environment_supply",
                "missing_resource": "Network",
            }
        }
    )
    assert upheld_reason(claim, judgment, environment=environment).value == expected


@pytest.mark.parametrize(
    "affordable,measured,cited,expected",
    [
        (True, True, True, "cost_measured_affordable"),
        (False, True, True, "cost_measured_uneconomic"),
        (False, False, True, "ground_not_reproduced"),
        (False, True, False, "ground_not_reproduced"),
    ],
)
def test_cost_never_authorizes_amendment_and_requires_recorded_base_measurement(
    affordable,
    measured,
    cited,
    expected,
):
    value = record()
    judgment = AmendmentJudgment.model_validate(
        value.judgment.model_dump()
        | {
            "finding": {
                "verdict": "feasible",
                "smallest_repair": "none",
                "cost_claim": {
                    "assertion": "Measured cost at base",
                    "measurement": {
                        "observed": "Actual measurement output",
                        "affordable": affordable,
                    }
                    if measured
                    else None,
                },
            },
            "measured_by": "Reproduced recorded command at base" if measured else None,
            "citations": [
                {"path": "measurements.txt", "quote": "Actual measurement output"}
            ]
            if cited
            else [],
        }
    )
    assert upheld_reason(value.claim, judgment, environment={}).value == expected
