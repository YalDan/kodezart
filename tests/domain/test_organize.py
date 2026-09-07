"""Admission refuses boolean collapse and unverifiable refusal shapes."""

import pytest

from kodezart.types.domain.organize import AdmissionVerdict


def test_admission_verdict_is_exactly_three_distinct_states():
    assert {member.name: member.value for member in AdmissionVerdict} == {
        "BUILDABLE": "buildable",
        "NOT_BUILDABLE": "not_buildable",
        "UNVERIFIABLE": "unverifiable",
    }


@pytest.mark.parametrize("verdict", list(AdmissionVerdict))
def test_admission_verdict_cannot_be_coerced_to_boolean(verdict):
    with pytest.raises(TypeError, match="explicit three-state"):
        bool(verdict)


def test_no_source_call_booleanizes_admission_verdict():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "kodezart"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "bool"
            ):
                assert not any(
                    isinstance(child, ast.Name) and child.id == "AdmissionVerdict"
                    for child in ast.walk(node)
                ), path


@pytest.mark.parametrize(
    "verdict,fields",
    [
        ("not_buildable", {"invented_decision": "choose a storage model"}),
        (
            "unverifiable",
            {"missing_artifact": "schema", "pending_blocker_id": "ISSUE-9"},
        ),
        ("buildable", {}),
    ],
)
def test_admission_result_round_trips_without_changing_verdict(verdict, fields):
    from kodezart.types.domain.organize import AdmissionResult

    result = AdmissionResult(
        issue_id="ISSUE-1",
        verdict=AdmissionVerdict(verdict),
        evidence="observed",
        **fields,
    )
    assert AdmissionResult.model_validate_json(result.model_dump_json()) == result
    assert result.verdict.value == verdict


@pytest.mark.parametrize(
    "verdict,fields",
    [
        ("not_buildable", {}),
        ("not_buildable", {"invented_decision": ""}),
        ("not_buildable", {"invented_decision": "  "}),
        ("unverifiable", {}),
        ("unverifiable", {"missing_artifact": "schema"}),
        ("unverifiable", {"pending_blocker_id": "ISSUE-9"}),
        ("unverifiable", {"missing_artifact": " ", "pending_blocker_id": "ISSUE-9"}),
        ("unverifiable", {"missing_artifact": "schema", "pending_blocker_id": ""}),
    ],
)
def test_admission_refusal_requires_actionable_fields(verdict, fields):
    from pydantic import ValidationError

    from kodezart.types.domain.organize import AdmissionResult

    with pytest.raises(ValidationError):
        AdmissionResult(
            issue_id="ISSUE-1",
            verdict=AdmissionVerdict(verdict),
            evidence="observed",
            **fields,
        )
