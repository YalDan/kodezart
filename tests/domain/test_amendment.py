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


def _holds(artifact):
    return WriteBackResult(
        verdict="holds",
        artifact=artifact,
        rounds=(
            WriteBackFinding(
                verdict="holds", evidence="Fixture verified.", cited_refs=()
            ),
        ),
    )


def amended(identity="opaque/criterion"):
    """An applied amendment on a criterion subject, the arm that carries no reason."""
    subject = TypeAdapter(AmendmentSubject).validate_python(
        {"kind": "criterion", "id": identity}
    )
    surface = WritableSurface(
        kind=SurfaceKind.CRITERION_SUB_ISSUE,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=identity),
    )
    prior = TrackerArtifact(
        surface=surface, native_ref=identity, content="[prior criterion row]"
    )
    return AmendedAmendment(
        claim=AmendmentClaim(
            subject=subject,
            stage="implementation",
            ground="unsatisfiable_at_base",
            departure="A proposed change",
            claimed_capability=None,
        ),
        judgment=AmendmentJudgment(
            subject=subject,
            base_sha="a" * 40,
            ground="unsatisfiable_at_base",
            reproduced=True,
            finding={
                "verdict": "infeasible",
                "smallest_repair": "criterion_text",
                "refutation": "No implementation at base satisfies the text.",
            },
            citations=({"path": "policy.py", "quote": "def answer(): return 42"},),
            measured_by=None,
        ),
        prior=prior,
        archive=_holds(
            TrackerArtifact(
                surface=WritableSurface(
                    kind=SurfaceKind.MARKER_COMMENT,
                    ref=surface.ref,
                    marker="[amendment:fixture]",
                ),
                native_ref="actual-archive",
                content="[archived prior criterion row]",
            )
        ),
        applied=_holds(
            TrackerArtifact(
                surface=surface,
                native_ref=identity,
                content="[amended criterion row]",
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
    # The same id string under a different kind: a count that drops the kind
    # merges this with `a`.
    ruling = record(kind="ruling")
    # The same subject as `a` under a second reason, repeated so that one
    # subject carries two distinct counted rows.
    other_reason = record(reason="cost_measured_affordable")
    # A second criterion identity upheld once for the same reason as `a`: a
    # count that drops the id merges it into `a` and reaches four.
    second = record(identity="opaque/criterion-2")
    reports = [
        AmendmentReport(verdicts=items)
        for items in [
            (a, ruling),
            (other_reason,),
            (a, ruling),
            (a, second),
            (other_reason,),
        ]
    ]
    before = [report.model_dump_json() for report in reports]
    counted = repeated_upheld(reports)
    assert [
        (item.subject.kind, item.subject.id, item.reason, item.count)
        for item in counted
    ] == [
        ("criterion", "opaque/criterion", UpheldReason.COST_MEASURED_AFFORDABLE, 2),
        ("criterion", "opaque/criterion", UpheldReason.GROUND_NOT_REPRODUCED, 3),
        ("ruling", "opaque/criterion", UpheldReason.GROUND_NOT_REPRODUCED, 2),
    ]
    event = NativeAmendmentEvent(report=reports[-1], repeated=counted)
    assert (
        NativeAmendmentEvent.model_validate_json(event.model_dump_json()).repeated
        == counted
    )
    assert repeated_upheld(reports) == counted
    assert [report.model_dump_json() for report in reports] == before


def test_the_ground_vocabulary_gained_no_member_when_the_subject_widened():
    """The subject is a discriminated union carrying a typed id, not a fifth ground.

    Widening the subject to the pinned-answer kind added no member here: the same
    four grounds are read against whichever kind the claim names.
    """
    assert [(member.name, member.value) for member in AmendmentGround] == [
        ("UNSATISFIABLE_AT_BASE", "unsatisfiable_at_base"),
        ("MUTUALLY_UNSATISFIABLE", "mutually_unsatisfiable"),
        ("PREMISE_FALSE_AT_BASE", "premise_false_at_base"),
        ("REQUIRES_BREAKING_HOUSE_RULE", "requires_breaking_house_rule"),
    ]
    value = amended()
    with pytest.raises(ValidationError):
        AmendmentClaim.model_validate(
            value.claim.model_dump() | {"ground": "a_fifth_ground"}
        )
    with pytest.raises(ValidationError):
        AmendmentJudgment.model_validate(
            value.judgment.model_dump() | {"ground": "a_fifth_ground"}
        )


def test_the_reason_vocabulary_is_exactly_these_four():
    assert [(member.name, member.value) for member in UpheldReason] == [
        ("GROUND_NOT_REPRODUCED", "ground_not_reproduced"),
        ("ENVIRONMENT_LACKS_CAPABILITY", "environment_lacks_capability"),
        ("COST_MEASURED_AFFORDABLE", "cost_measured_affordable"),
        ("COST_MEASURED_UNECONOMIC", "cost_measured_uneconomic"),
    ]


def test_a_verdict_and_its_reason_cannot_be_constructed_apart():
    """The pairing is the shape: upheld requires a reason, amended has no such field.

    No validator states it — an upheld record carries `reason` as a required,
    non-nullable field and an amended record declares none under `extra="forbid"`,
    so the bad pairing is unconstructible rather than rejected.
    """
    upheld = record().model_dump()
    assert (
        UpheldAmendment.model_validate(upheld).reason
        is UpheldReason.GROUND_NOT_REPRODUCED
    )
    for broken in (
        {key: value for key, value in upheld.items() if key != "reason"},
        upheld | {"reason": None},
    ):
        with pytest.raises(ValidationError):
            UpheldAmendment.model_validate(broken)
        with pytest.raises(ValidationError):
            AmendmentReport.model_validate({"verdicts": [broken]})
    applied = amended().model_dump()
    assert (
        AmendmentReport.model_validate({"verdicts": [applied]}).verdicts[0].verdict
        == "amended"
    )
    for reason in (*[member.value for member in UpheldReason], None):
        with pytest.raises(ValidationError):
            AmendedAmendment.model_validate(applied | {"reason": reason})
        with pytest.raises(ValidationError):
            AmendmentReport.model_validate({"verdicts": [applied | {"reason": reason}]})


#: The fault lies outside the criterion: some implementation at base would
#: satisfy it, and only the demonstration is unavailable in this environment.
_FAULT_OUTSIDE = {
    "verdict": "unverifiable",
    "smallest_repair": "environment_supply",
    "missing_resource": "network access for the demonstration",
}


@pytest.mark.parametrize(
    "judgment_changes,claimed,environment,expected",
    [
        pytest.param({}, None, {}, None, id="fault_in_criterion"),
        pytest.param(
            {},
            "network",
            {CheckPrerequisite.NETWORK: False},
            None,
            id="fault_in_criterion_with_capability_claimed",
        ),
        pytest.param(
            {"finding": _FAULT_OUTSIDE},
            None,
            {},
            UpheldReason.GROUND_NOT_REPRODUCED,
            id="fault_outside_criterion",
        ),
        pytest.param(
            {"finding": _FAULT_OUTSIDE},
            "network",
            {CheckPrerequisite.NETWORK: False},
            UpheldReason.ENVIRONMENT_LACKS_CAPABILITY,
            id="fault_outside_criterion_declared_absent",
        ),
        pytest.param(
            {
                "finding": _FAULT_OUTSIDE
                | {
                    "cost_claim": {
                        "assertion": "The demonstration costs what it costs.",
                        "measurement": {
                            "observed": "Executed once at base; 2 seconds observed",
                            "affordable": True,
                        },
                    }
                },
                "measured_by": "Reproduced recorded command at base",
            },
            None,
            {},
            UpheldReason.COST_MEASURED_AFFORDABLE,
            id="cost_decides_before_the_fault_line",
        ),
    ],
)
def test_the_fault_line_is_asked_after_cost_and_before_reproduction(
    judgment_changes,
    claimed,
    environment,
    expected,
):
    """Would some implementation at base satisfy this criterion, asked first.

    The rows differ in the finding alone where the fault line is what decides:
    a fault in the criterion's own text authorizes an amendment, a fault outside
    it never does. Cost is not one of the four grounds, so deciding it above the
    fault line still asks the fault line before any ground, and that landed
    order is pinned here.
    """
    value = amended()
    claim = AmendmentClaim.model_validate(
        value.claim.model_dump() | {"claimed_capability": claimed}
    )
    judgment = AmendmentJudgment.model_validate(
        value.judgment.model_dump() | judgment_changes
    )
    assert judgment.reproduced
    assert upheld_reason(claim, judgment, environment=environment) is expected


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
