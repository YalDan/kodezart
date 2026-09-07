"""Native-key findings share the established evidence and conjunction rules."""

import hashlib
import json

import pytest
from pydantic import ValidationError

from kodezart.domain.criteria_feasibility import (
    classify_finding,
    grounded_finding,
    minimal_conflicting_subsets,
    reconcile_findings,
)
from kodezart.domain.errors import CriteriaFanInError, UngroundedVerdictError
from kodezart.types.domain.criteria import (
    CriteriaValidationOutput,
    CriterionFinding,
    CriterionFlag,
    CriterionVerdict,
    TrackerContradiction,
    TrackerCriterionFinding,
)


@pytest.mark.parametrize(
    "model,digest",
    [
        (
            CriterionFinding,
            "5afe903e14f6c46793fb5b5a5e2ce796f1c69ba4df859ed4d580316dd75069d4",
        ),
        (
            CriteriaValidationOutput,
            "0da41e65788f8ed11a5a101bc32938299a0ce7c5322394300381ea54883fd8b6",
        ),
    ],
)
def test_authored_wire_schemas_equal_the_captured_dispatch_base(model, digest):
    # Captured before extracting shared evidence at fbc4daa; never re-baselined.
    value = json.dumps(
        model.model_json_schema(),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    assert hashlib.sha256(value.encode()).hexdigest() == digest


def native(key, **changes):
    return TrackerCriterionFinding.model_validate(
        {"criterionId": key, "verdict": "feasible", "smallestRepair": "none", **changes}
    )


@pytest.mark.parametrize(
    "changes",
    [
        {
            "verdict": "unverifiable",
            "smallestRepair": "environment_supply",
            "missingResource": "fixture",
        },
        {
            "verdict": "infeasible",
            "smallestRepair": "criterion_text",
            "refutation": "undeclared arm",
        },
        {"pinnedLiterals": ["exactly one site"]},
        {"baseDemonstration": {"command": "pytest concrete", "satisfiedAtBase": True}},
        {"costClaim": {"assertion": "unmeasured"}},
    ],
)
def test_the_same_classifier_handles_authored_and_native_evidence(changes):
    tracker = native("EXT/café", **changes)
    authored = CriterionFinding.model_validate(
        {**tracker.model_dump(), "criterion_id": "AC-1"}
    )
    assert classify_finding(tracker) == classify_finding(authored)
    assert grounded_finding(tracker) == grounded_finding(authored)


def test_native_flags_do_not_add_a_class_or_rewrite_the_criterion():
    found = native("EXT/café", pinnedLiterals=["exact site"])
    derived = grounded_finding(found)
    assert derived.flags == (CriterionFlag.literal_pinning,)
    assert derived.verdict is CriterionVerdict.feasible
    assert "criterion_class" not in type(found).model_fields


@pytest.mark.parametrize(
    "changes",
    [
        {"verdict": "infeasible", "smallestRepair": "none", "refutation": "x"},
        {
            "verdict": "infeasible",
            "smallestRepair": "criterion_text",
            "refutation": " ",
        },
        {
            "verdict": "unverifiable",
            "smallestRepair": "environment_supply",
            "missingResource": " ",
        },
        {"verdict": True},
    ],
)
def test_native_stated_verdict_requires_the_same_completed_grounds(changes):
    with pytest.raises(ValidationError):
        native("EXT/42", **changes)


def test_vacuous_native_repair_is_refused_with_its_own_key():
    finding = native(
        "EXT/42",
        verdict="infeasible",
        smallestRepair="criterion_text",
        refutation="x",
        baseDemonstration={"command": "test", "satisfiedAtBase": True},
    )
    with pytest.raises(UngroundedVerdictError) as raised:
        grounded_finding(finding)
    assert raised.value.criterion_id == "EXT/42"


def test_conjunction_and_permutation_keep_native_keys_and_nonlexical_order():
    keys = ("z/2", "a/1", "m/3", "b/4")
    rows = [native(key) for key in reversed(keys)]
    conflicts = [
        TrackerContradiction(criterion_ids=list(group), explanation=str(group))
        for group in [(keys[0], keys[1]), keys[:3], (keys[2], keys[3])]
    ]
    reconciled = reconcile_findings(
        dispatched=keys, findings=rows, contradictions=conflicts
    )
    assert tuple(row.criterion_id for row in reconciled) == keys
    assert minimal_conflicting_subsets(conflicts) == (conflicts[0], conflicts[2])
    with pytest.raises(CriteriaFanInError):
        reconcile_findings(
            dispatched=keys,
            findings=rows,
            contradictions=[
                TrackerContradiction(
                    criterion_ids=[keys[0], "foreign"], explanation="foreign"
                )
            ],
        )


@pytest.mark.parametrize(
    "model,key", [(CriterionFinding, "AC-1"), (TrackerCriterionFinding, "EXT/42")]
)
def test_shared_evidence_models_remain_frozen(model, key):
    value = model.model_validate(
        {"criterion_id": key, "verdict": "feasible", "smallest_repair": "none"}
    )
    with pytest.raises(ValidationError, match="frozen_instance"):
        value.verdict = CriterionVerdict.infeasible
