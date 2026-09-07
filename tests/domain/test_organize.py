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
