"""Native semantic addresses and report arithmetic preserve exact identities."""

import pytest
from pydantic import TypeAdapter, ValidationError

from kodezart.domain.amendment import (
    NativeWriteRefusalError,
    repeated_upheld,
    upheld_reason,
)
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


RULING = "pinned/ruling/1"
PINNED_ISSUE = "native/issue"


def holds(artifact):
    return WriteBackResult(
        verdict="holds",
        artifact=artifact,
        rounds=(
            WriteBackFinding(
                verdict="holds", evidence="Fixture verified.", cited_refs=()
            ),
        ),
    )


def amended(
    ground="premise_false_at_base",
    identity=RULING,
    citations=(("policy.py", "The base text the pinned ruling assumed."),),
):
    subject = {"kind": "ruling", "id": identity}
    ref = ScopeRef(kind=ScopeKind.ISSUE, key=PINNED_ISSUE)
    pinned = WritableSurface(
        kind=SurfaceKind.MARKER_COMMENT, ref=ref, marker="[ruling:fixture-pinned]"
    )
    prior = TrackerArtifact(
        surface=pinned,
        native_ref="pinned-comment",
        content="The prior pinned ruling body.",
    )
    judgment = AmendmentJudgment(
        subject=subject,
        base_sha="b" * 40,
        ground=ground,
        reproduced=True,
        finding={
            "verdict": "infeasible",
            "smallest_repair": "criterion_text",
            "refutation": "Independently refuted at the exact base commit.",
        },
        citations=[{"path": path, "quote": quote} for path, quote in citations],
        measured_by=None,
    )
    claim = AmendmentClaim(
        subject=subject,
        stage="implementation",
        ground=ground,
        departure="The corrected pinned answer.",
        claimed_capability=None,
    )
    return AmendedAmendment(
        claim=claim,
        judgment=judgment,
        prior=prior,
        archive=holds(
            TrackerArtifact(
                surface=WritableSurface(
                    kind=SurfaceKind.MARKER_COMMENT,
                    ref=ref,
                    marker="[amendment:fixture]",
                ),
                native_ref="archive-comment",
                content="Fixture of the archived prior pinned ruling.",
            )
        ),
        applied=holds(
            TrackerArtifact(
                surface=pinned,
                native_ref="pinned-comment",
                content="The amended pinned ruling body.",
            )
        ),
    )


def upheld_ruling(ground, identity=RULING):
    value = record(kind="ruling", identity=identity).model_dump()
    value["claim"]["ground"] = ground
    value["judgment"]["ground"] = ground
    return UpheldAmendment.model_validate(value)


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


def test_the_ground_vocabulary_has_exactly_the_four_named_grounds():
    assert [(ground.name, ground.value) for ground in AmendmentGround] == [
        ("UNSATISFIABLE_AT_BASE", "unsatisfiable_at_base"),
        ("MUTUALLY_UNSATISFIABLE", "mutually_unsatisfiable"),
        ("PREMISE_FALSE_AT_BASE", "premise_false_at_base"),
        ("REQUIRES_BREAKING_HOUSE_RULE", "requires_breaking_house_rule"),
    ]
    for absent in ["a_quote_exists", "cost_measured_uneconomic", "subject_widened"]:
        with pytest.raises(ValueError):
            AmendmentGround(absent)
        with pytest.raises(ValidationError):
            AmendmentClaim.model_validate(
                amended().claim.model_dump() | {"ground": absent}
            )


@pytest.mark.parametrize("ground", list(AmendmentGround))
def test_a_ruling_amends_on_every_ground_only_on_reproduced_cited_refutation(ground):
    applied = amended(ground=ground)
    assert applied.verdict == "amended"
    assert (applied.subject.kind, applied.subject.id) == ("ruling", RULING)
    assert applied.claim.ground is ground
    assert applied.judgment.ground is ground
    assert upheld_reason(applied.claim, applied.judgment, environment={}) is None
    value = applied.model_dump()
    assert AmendedAmendment.model_validate(value) == applied
    for change in [
        {"reproduced": False},
        {"citations": []},
        {
            "finding": {
                "verdict": "feasible",
                "smallest_repair": "none",
            }
        },
    ]:
        with pytest.raises(ValidationError):
            AmendedAmendment.model_validate(
                value | {"judgment": value["judgment"] | change}
            )
    with pytest.raises(NativeWriteRefusalError):
        upheld_reason(
            applied.claim,
            AmendmentJudgment.model_validate(value["judgment"] | {"citations": []}),
            environment={},
        )


@pytest.mark.parametrize("ground", list(AmendmentGround))
def test_a_ruling_upholds_on_every_ground_when_the_ground_is_not_reproduced(ground):
    refusal = upheld_ruling(ground)
    assert refusal.verdict == "upheld"
    assert (refusal.subject.kind, refusal.subject.id) == ("ruling", RULING)
    assert refusal.claim.ground is ground
    assert refusal.judgment.ground is ground
    assert not refusal.judgment.reproduced
    assert (
        upheld_reason(refusal.claim, refusal.judgment, environment={})
        is UpheldReason.GROUND_NOT_REPRODUCED
    )
    value = amended(ground=ground).model_dump()
    with pytest.raises(ValidationError):
        AmendedAmendment.model_validate(
            value
            | {
                "judgment": value["judgment"]
                | {
                    "reproduced": False,
                    "finding": refusal.judgment.finding.model_dump(),
                    "citations": [],
                }
            }
        )
    other = next(item for item in AmendmentGround if item is not ground)
    with pytest.raises(ValidationError):
        UpheldAmendment.model_validate(
            refusal.model_dump()
            | {"judgment": refusal.judgment.model_dump() | {"ground": other}}
        )


def test_a_mutually_unsatisfiable_ruling_amendment_cites_its_conflicting_subset():
    subset = (
        ("checks/first.md", "The first pinned answer's exact demand."),
        ("checks/second.md", "The second pinned answer's contradicting demand."),
    )
    applied = amended(ground=AmendmentGround.MUTUALLY_UNSATISFIABLE, citations=subset)
    assert (
        tuple(
            (citation.path, citation.quote) for citation in applied.judgment.citations
        )
        == subset
    )
    assert upheld_reason(applied.claim, applied.judgment, environment={}) is None
    without = AmendmentJudgment.model_validate(
        applied.judgment.model_dump() | {"citations": []}
    )
    with pytest.raises(NativeWriteRefusalError):
        upheld_reason(applied.claim, without, environment={})


@pytest.mark.parametrize(
    "mutation",
    [
        "ruling_id",
        "applied_native_ref",
        "applied_issue",
        "archive_issue",
        "unpinned_surface",
    ],
)
def test_an_amended_ruling_keeps_its_identity_on_the_surface_it_was_pinned_to(mutation):
    value = amended().model_dump()
    assert AmendedAmendment.model_validate(value).prior.native_ref == "pinned-comment"
    if mutation == "ruling_id":
        value["claim"]["subject"]["id"] = "another/ruling"
    elif mutation == "applied_native_ref":
        value["applied"]["artifact"]["native_ref"] = "another-comment"
    elif mutation == "applied_issue":
        value["applied"]["artifact"]["surface"]["ref"]["key"] = "another/issue"
    elif mutation == "archive_issue":
        value["archive"]["artifact"]["surface"]["ref"]["key"] = "another/issue"
    else:
        for artifact in [value["prior"], value["applied"]["artifact"]]:
            artifact["surface"] = {
                "kind": "criterion_sub_issue",
                "ref": artifact["surface"]["ref"],
                "marker": None,
            }
    with pytest.raises(ValidationError):
        AmendedAmendment.model_validate(value)
