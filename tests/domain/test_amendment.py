"""Native semantic addresses and report arithmetic preserve exact identities."""

import pytest
from pydantic import TypeAdapter, ValidationError

from kodezart.domain.amendment import repeated_upheld, upheld_reason
from kodezart.handlers.agent_handler import _queued_event_payload
from kodezart.types.domain.agent import NativeAmendmentEvent
from kodezart.types.domain.agent import RulingId as ExistingRulingId
from kodezart.types.domain.amendment import (
    AmendedAmendment,
    AmendmentClaim,
    AmendmentGround,
    AmendmentJudgment,
    AmendmentReport,
    AmendmentSubject,
    CriterionSubject,
    NativeWriterOutput,
    RecordedRefusal,
    UpheldAmendment,
    UpheldReason,
)
from kodezart.types.domain.audit import TrackerArtifact
from kodezart.types.domain.operation import CheckPrerequisite
from kodezart.types.domain.ruling_id import RulingId
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeLaneEvent
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.write_back import WriteBackFinding, WriteBackResult


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
    return UpheldAmendment(
        claim=claim,
        reason=reason,
        judgment=judgment,
        publication=RecordedRefusal(
            record=WriteBackResult(
                verdict="holds",
                artifact=TrackerArtifact(
                    surface=WritableSurface(
                        kind=SurfaceKind.MARKER_COMMENT,
                        ref=ScopeRef(kind=ScopeKind.ISSUE, key=identity),
                        marker="[amendment:fixture]",
                    ),
                    native_ref="actual-record",
                    content="Fixture of an independently verified record.",
                ),
                rounds=(
                    WriteBackFinding(
                        verdict="holds", evidence="Fixture verified.", cited_refs=()
                    ),
                ),
            )
        ),
    )


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
        AmendmentReport(verdicts=items)
        for items in [
            (a, ruling),
            (other_reason,),
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
            report=AmendmentReport(verdicts=(record(),)),
        ),
    )
    payload = _queued_event_payload(event)
    assert ScopeLaneEvent.model_validate(payload) == event
    original = payload["event"]["report"]["verdicts"][0]
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


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_publication",
        "unverified",
        "wrong_issue",
        "duplicate",
        "uneconomic_without_escalation",
    ],
)
def test_completed_reports_refuse_missing_or_unrelated_canonical_evidence(mutation):
    value = record().model_dump()
    if mutation == "missing_publication":
        del value["publication"]
    elif mutation == "unverified":
        value["publication"]["record"]["verdict"] = "unverifiable"
    elif mutation == "wrong_issue":
        value["publication"]["record"]["artifact"]["surface"]["ref"]["key"] = (
            "another-issue"
        )
    elif mutation == "uneconomic_without_escalation":
        value["reason"] = "cost_measured_uneconomic"
    with pytest.raises(ValidationError):
        AmendmentReport.model_validate(
            {"verdicts": [value, value] if mutation == "duplicate" else [value]}
        )


def amended(identity="registry/ruling/7"):
    subject = TypeAdapter(AmendmentSubject).validate_python(
        {"kind": "ruling", "id": identity}
    )
    pinned = WritableSurface(
        kind=SurfaceKind.MARKER_COMMENT,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="KOD-97"),
        marker="[ruling:fixture-pinned]",
    )
    prior = TrackerArtifact(
        surface=pinned,
        native_ref="native-comment-1",
        content="The pinned ruling as it stood before this amendment.",
    )

    def result(artifact):
        return WriteBackResult(
            verdict="holds",
            artifact=artifact,
            rounds=(
                WriteBackFinding(
                    verdict="holds", evidence="Fixture verified.", cited_refs=()
                ),
            ),
        )

    return AmendedAmendment(
        claim=AmendmentClaim(
            subject=subject,
            stage="implementation",
            ground="premise_false_at_base",
            departure="A proposed change to the pinned ruling",
            claimed_capability=None,
        ),
        judgment=AmendmentJudgment(
            subject=subject,
            base_sha="b" * 40,
            ground="premise_false_at_base",
            reproduced=True,
            finding={
                "verdict": "infeasible",
                "smallest_repair": "criterion_text",
                "refutation": "The premise the ruling rests on is false at base.",
            },
            citations=({"path": "src/module.py", "quote": "actual base bytes"},),
            measured_by=None,
        ),
        prior=prior,
        archive=result(
            TrackerArtifact(
                surface=pinned,
                native_ref="native-comment-2",
                content="The archived prior text of the pinned ruling.",
            )
        ),
        applied=result(
            TrackerArtifact(
                surface=pinned,
                native_ref="native-comment-1",
                content="The amended ruling text.",
            )
        ),
    )


def test_ground_vocabulary_gained_no_member_when_the_subject_widened():
    assert [(member.name, member.value) for member in AmendmentGround] == [
        ("UNSATISFIABLE_AT_BASE", "unsatisfiable_at_base"),
        ("MUTUALLY_UNSATISFIABLE", "mutually_unsatisfiable"),
        ("PREMISE_FALSE_AT_BASE", "premise_false_at_base"),
        ("REQUIRES_BREAKING_HOUSE_RULE", "requires_breaking_house_rule"),
    ]
    with pytest.raises(ValidationError):
        AmendmentClaim.model_validate(
            amended().claim.model_dump() | {"ground": "a_fifth_ground"}
        )


def test_an_amended_ruling_keeps_its_identity_on_the_surface_it_was_pinned_to():
    value = amended()
    assert value.subject.kind == "ruling"
    assert value.subject.id == "registry/ruling/7"
    assert value.judgment.subject == value.subject
    assert value.applied.artifact.surface == value.prior.surface
    assert value.applied.artifact.native_ref == value.prior.native_ref
    assert value.archive.artifact.surface.ref == value.prior.surface.ref
    dumped = value.model_dump()
    for changes in [
        {"claim": dumped["claim"] | {"subject": {"kind": "ruling", "id": "x"}}},
        {
            "applied": dumped["applied"]
            | {
                "artifact": dumped["applied"]["artifact"]
                | {"native_ref": "another-comment"}
            }
        },
        {
            "prior": dumped["prior"]
            | {"surface": dumped["prior"]["surface"] | {"kind": "criterion_sub_issue"}}
        },
    ]:
        with pytest.raises(ValidationError):
            AmendedAmendment.model_validate(dumped | changes)


def test_the_upheld_reason_members_are_exactly_these():
    assert [(member.name, member.value) for member in UpheldReason] == [
        ("GROUND_NOT_REPRODUCED", "ground_not_reproduced"),
        ("ENVIRONMENT_LACKS_CAPABILITY", "environment_lacks_capability"),
        ("COST_MEASURED_AFFORDABLE", "cost_measured_affordable"),
        ("COST_MEASURED_UNECONOMIC", "cost_measured_uneconomic"),
    ]


def test_a_verdict_and_its_reason_cannot_be_constructed_apart():
    upheld = record().model_dump()
    assert UpheldAmendment.model_validate(upheld).reason is (
        UpheldReason.GROUND_NOT_REPRODUCED
    )
    without_reason = {key: value for key, value in upheld.items() if key != "reason"}
    with pytest.raises(ValidationError):
        UpheldAmendment.model_validate(without_reason)
    with pytest.raises(ValidationError):
        UpheldAmendment.model_validate(upheld | {"reason": None})
    amended_value = amended().model_dump()
    assert AmendedAmendment.model_validate(amended_value).verdict == "amended"
    for reason in UpheldReason:
        with pytest.raises(ValidationError):
            AmendedAmendment.model_validate(amended_value | {"reason": reason.value})
    with pytest.raises(ValidationError):
        AmendedAmendment.model_validate(amended_value | {"reason": None})
