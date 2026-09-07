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


def boolean_coercion_lines(source):
    """Track the two admission types through parameters and local bindings."""
    import ast

    tree = ast.parse(source)
    constructors = {"AdmissionVerdict": "verdict", "AdmissionResult": "result"}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "kodezart.types.domain.organize"
        ):
            for alias in node.names:
                if alias.name in constructors:
                    constructors[alias.asname or alias.name] = constructors[alias.name]

    def value_kind(node, bindings):
        if isinstance(node, ast.Name):
            return bindings.get(node.id)
        if isinstance(node, ast.Attribute):
            if node.attr == "verdict" and value_kind(node.value, bindings) == "result":
                return "verdict"
            if (
                isinstance(node.value, ast.Name)
                and constructors.get(node.value.id) == "verdict"
            ):
                return "verdict"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return constructors.get(node.func.id)
        return None

    failures = set()
    scopes = [
        tree,
        *(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ),
    ]
    for scope in scopes:
        nodes = list(ast.walk(scope))
        bindings = {}
        for node in nodes:
            if isinstance(node, ast.arg) and isinstance(node.annotation, ast.Name):
                bindings[node.arg] = constructors.get(node.annotation.id)
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and isinstance(node.annotation, ast.Name)
            ):
                bindings[node.target.id] = constructors.get(node.annotation.id)
        for _ in range(len(nodes)):
            before = dict(bindings)
            for node in nodes:
                if isinstance(node, ast.Assign):
                    kind = value_kind(node.value, bindings)
                    if kind is not None:
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                bindings[target.id] = kind
            if before == bindings:
                break
        for node in nodes:
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "bool"
            ):
                if any(value_kind(arg, bindings) == "verdict" for arg in node.args):
                    failures.add(node.lineno)
    return failures


def test_no_source_call_booleanizes_admission_verdict():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "kodezart"
    for path in root.rglob("*.py"):
        assert not boolean_coercion_lines(path.read_text()), path


@pytest.mark.parametrize(
    "source",
    [
        "bool(AdmissionVerdict.BUILDABLE)",
        "def f(verdict: AdmissionVerdict): return bool(verdict)",
        "def f(result: AdmissionResult): return bool(result.verdict)",
        (
            "def f(result: AdmissionResult):\n"
            "    verdict = result.verdict\n    return bool(verdict)"
        ),
        (
            "from kodezart.types.domain.organize import AdmissionVerdict as V\n"
            "def f(verdict: V): return bool(verdict)"
        ),
    ],
)
def test_static_detector_catches_typed_consumer_mutations(source):
    assert boolean_coercion_lines(source)


def test_static_detector_accepts_explicit_comparison_and_unrelated_boolean():
    assert not boolean_coercion_lines(
        "def f(result: AdmissionResult, enabled: bool):\n"
        "    return result.verdict is AdmissionVerdict.BUILDABLE and bool(enabled)"
    )


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


def test_defect_role_has_exact_instance_and_mandate_members():
    from kodezart.types.domain.organize import DefectRole

    assert {member.name: member.value for member in DefectRole} == {
        "INSTANCE": "instance",
        "MANDATE": "mandate",
    }


@pytest.mark.parametrize("mandate_text", [None, "", " ", "\n\t"])
def test_mandate_finding_requires_a_quoted_sentence(mandate_text):
    from pydantic import ValidationError

    from kodezart.types.domain.organize import DefectRole, SpecFinding

    with pytest.raises(ValidationError, match="MANDATE requires"):
        SpecFinding(
            issue_id="ISSUE-1",
            defect_class="self-sufficiency",
            evidence="The instruction asks writers to leave the choice open.",
            role=DefectRole.MANDATE,
            mandate_text=mandate_text,
        )


@pytest.mark.parametrize("mandate_text", ["", " ", "Repeat this defect."])
def test_instance_finding_refuses_any_mandate_text(mandate_text):
    from pydantic import ValidationError

    from kodezart.types.domain.organize import DefectRole, SpecFinding

    with pytest.raises(ValidationError, match="INSTANCE requires"):
        SpecFinding(
            issue_id="ISSUE-1",
            defect_class="self-sufficiency",
            evidence="The body leaves an implementation choice open.",
            role=DefectRole.INSTANCE,
            mandate_text=mandate_text,
        )


@pytest.mark.parametrize(
    "role,mandate_text",
    [("instance", None), ("mandate", "  Repeat this defect verbatim.\n")],
)
def test_finding_round_trip_keeps_role_and_verbatim_instruction(role, mandate_text):
    from kodezart.types.domain.organize import DefectRole, SpecFinding

    finding = SpecFinding(
        issue_id="ISSUE-1",
        defect_class="self-sufficiency",
        evidence="Observed on the issue body.",
        role=DefectRole(role),
        mandate_text=mandate_text,
    )
    restored = SpecFinding.model_validate_json(finding.model_dump_json(by_alias=True))
    assert restored == finding
    assert restored.mandate_text == mandate_text


def test_instance_finding_needs_no_mandate_text():
    from kodezart.types.domain.organize import DefectRole, SpecFinding

    finding = SpecFinding(
        issue_id="ISSUE-1",
        defect_class="self-sufficiency",
        evidence="Observed on the issue body.",
        role=DefectRole.INSTANCE,
    )
    assert finding.mandate_text is None


def test_finding_is_frozen_and_rejects_unknown_fields():
    from pydantic import ValidationError

    from kodezart.types.domain.organize import DefectRole, SpecFinding

    fields = {
        "issue_id": "ISSUE-1",
        "defect_class": "self-sufficiency",
        "evidence": "Observed on the issue body.",
        "role": DefectRole.INSTANCE,
    }
    finding = SpecFinding.model_validate(fields)
    with pytest.raises(ValidationError, match="frozen"):
        finding.role = DefectRole.MANDATE
    with pytest.raises(ValidationError, match="Extra inputs"):
        SpecFinding.model_validate({**fields, "instruction": "Not a defined field"})
