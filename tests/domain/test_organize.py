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
    """Track admission types through parameters and local bindings."""
    import ast

    tree = ast.parse(source)
    constructors = {
        "AdmissionVerdict": "verdict",
        "AdmissionResult": "result",
        "AdmissionJudgment": "result",
    }
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
        "def f(judgment: AdmissionJudgment): return bool(judgment.verdict)",
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
        (
            "not_buildable",
            {
                "invented_decision": "choose a storage model",
                "refusal_kind": "spec_gap",
            },
        ),
        (
            "unverifiable",
            {"missing_artifact": "schema", "pending_blocker_id": "ISSUE-9"},
        ),
        ("buildable", {}),
    ],
)
def test_admission_result_round_trips_without_changing_verdict(verdict, fields):
    from pydantic import ValidationError

    from kodezart.types.domain.organize import AdmissionResult

    result = AdmissionResult(
        admitted_body_digest="revision:one",
        issue_id="ISSUE-1",
        verdict=AdmissionVerdict(verdict),
        evidence="observed",
        **fields,
    )
    assert AdmissionResult.model_validate_json(result.model_dump_json()) == result
    assert result.verdict.value == verdict
    assert result.admitted_body_digest == "revision:one"
    with pytest.raises(ValidationError, match="frozen"):
        result.admitted_body_digest = "revision:two"


@pytest.mark.parametrize("verdict", list(AdmissionVerdict))
@pytest.mark.parametrize("digest", [None, "", " \n\t"])
def test_every_verdict_requires_a_nonempty_admitted_body_digest(verdict, digest):
    from pydantic import ValidationError

    from kodezart.types.domain.organize import AdmissionResult, RefusalKind

    fields = {
        "issue_id": "surface/one",
        "verdict": verdict,
        "evidence": "Observed.",
        "invented_decision": "Choose the model.",
        "missing_artifact": "Schema.",
        "pending_blocker_id": "blocker/one",
        "refusal_kind": (
            RefusalKind.SPEC_GAP if verdict is AdmissionVerdict.NOT_BUILDABLE else None
        ),
    }
    with pytest.raises(ValidationError, match="admitted_body_digest"):
        AdmissionResult.model_validate({**fields, "admitted_body_digest": digest})
    with pytest.raises(ValidationError, match="admittedBodyDigest"):
        AdmissionResult.model_validate(fields)


@pytest.mark.parametrize(
    "admitted,current,expected",
    [
        ("revision:one", "revision:one", True),
        ("revision:one", "revision:two", False),
        ("revision:one ", "revision:one", False),
        ("REVISION", "revision", False),
        ("opaque-not-a-sha", "opaque-not-a-sha", True),
    ],
)
def test_admission_liveness_is_exact_digest_arithmetic(admitted, current, expected):
    from kodezart.domain.organize import is_admission_live

    assert (
        is_admission_live(admitted_body_digest=admitted, current_body_digest=current)
        is expected
    )


@pytest.mark.parametrize("field", ["admitted_body_digest", "current_body_digest"])
@pytest.mark.parametrize("missing", ["", " \n"])
def test_liveness_cannot_treat_unavailable_digests_as_live(field, missing):
    from kodezart.domain.organize import is_admission_live

    fields = {"admitted_body_digest": "revision", "current_body_digest": "revision"}
    fields[field] = missing
    with pytest.raises(ValueError, match="nonempty"):
        is_admission_live(**fields)


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

    if verdict == "not_buildable":
        fields = {**fields, "refusal_kind": "spec_gap"}
    with pytest.raises(ValidationError):
        AdmissionResult(
            admitted_body_digest="revision:one",
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


def test_refusal_kind_has_exact_spec_gap_and_human_decision_members():
    from kodezart.types.domain.organize import RefusalKind

    assert {member.name: member.value for member in RefusalKind} == {
        "SPEC_GAP": "spec_gap",
        "HUMAN_DECISION": "human_decision",
    }


@pytest.mark.parametrize("extra_fields", [{}, {"refusal_kind": None}])
def test_not_buildable_requires_an_explicit_refusal_kind(extra_fields):
    from pydantic import ValidationError

    from kodezart.types.domain.organize import AdmissionResult

    with pytest.raises(ValidationError, match="NOT_BUILDABLE requires refusal_kind"):
        AdmissionResult.model_validate(
            {
                "issue_id": "ISSUE-1",
                "verdict": "not_buildable",
                "admitted_body_digest": "revision:one",
                "invented_decision": "Choose the storage model.",
                "evidence": "The specification leaves that choice open.",
                **extra_fields,
            }
        )


@pytest.mark.parametrize("verdict", ["buildable", "unverifiable"])
@pytest.mark.parametrize("refusal_kind", ["spec_gap", "human_decision"])
def test_only_not_buildable_can_carry_a_refusal_kind(verdict, refusal_kind):
    from pydantic import ValidationError

    from kodezart.types.domain.organize import AdmissionResult

    with pytest.raises(ValidationError, match="refusal_kind must be None"):
        AdmissionResult.model_validate(
            {
                "issue_id": "ISSUE-1",
                "verdict": verdict,
                "admitted_body_digest": "revision:one",
                "missing_artifact": "schema",
                "pending_blocker_id": "ISSUE-9",
                "evidence": "Observed on the issue body.",
                "refusal_kind": refusal_kind,
            }
        )


@pytest.mark.parametrize("verdict", ["buildable", "unverifiable"])
def test_non_refusals_round_trip_with_no_refusal_kind(verdict):
    from kodezart.types.domain.organize import AdmissionResult

    result = AdmissionResult.model_validate(
        {
            "issue_id": "ISSUE-1",
            "verdict": verdict,
            "admitted_body_digest": "revision:one",
            "missing_artifact": "schema",
            "pending_blocker_id": "ISSUE-9",
            "evidence": "Observed on the issue body.",
        }
    )
    restored = AdmissionResult.model_validate_json(
        result.model_dump_json(by_alias=True)
    )
    assert restored == result
    assert restored.refusal_kind is None


@pytest.mark.parametrize(
    "refusal_kind,expected",
    [("spec_gap", "reauthor"), ("human_decision", "escalate")],
)
@pytest.mark.parametrize(
    "invented_decision",
    ["URGENT HUMAN APPROVAL REQUIRED", "The author can easily repair this gap."],
)
def test_admission_refusal_route_uses_kind_without_reading_tone(
    refusal_kind, expected, invented_decision
):
    from kodezart.domain.organize import admission_route
    from kodezart.types.domain.organize import AdmissionResult
    from tests.fakes import make_tracker_issue

    result = AdmissionResult.model_validate(
        {
            "issue_id": "ISSUE-1",
            "verdict": "not_buildable",
            "admitted_body_digest": "revision:one",
            "invented_decision": invented_decision,
            "evidence": "The same evidence is used for either classification.",
            "refusal_kind": refusal_kind,
        }
    )
    before = result.model_dump_json()
    assert (
        admission_route(
            result,
            issue=make_tracker_issue("ISSUE-1"),
            scope_issue_keys=frozenset({"ISSUE-1"}),
        ).value
        == expected
    )
    assert result.model_dump_json() == before
    assert AdmissionResult.model_validate_json(before) == result


def mandate_fields(**overrides):
    return {
        "kind": "groom",
        "gate_label_key": "scope_labels.triage",
        "rubric_prompt_key": "grooming_pass",
        "admission_prompt_key": "ticket_review",
        "terminal_marker_key": "issue_labels.groomed",
        **overrides,
    }


def test_mandate_kind_names_and_values_are_the_three_organize_phases():
    from kodezart.types.domain.organize import MandateKind

    assert {member.name: member.value for member in MandateKind} == {
        "GROOM": "groom",
        "TICKET": "ticket",
        "CRITERIA": "criteria",
    }


def test_mandate_has_only_its_five_configured_differences():
    from kodezart.types.domain.organize import MandateSpec

    assert set(MandateSpec.model_fields) == {
        "kind",
        "gate_label_key",
        "rubric_prompt_key",
        "admission_prompt_key",
        "terminal_marker_key",
    }


@pytest.mark.parametrize("missing", list(mandate_fields()))
def test_every_mandate_field_is_required(missing):
    from pydantic import ValidationError

    from kodezart.types.domain.organize import MandateSpec

    fields = mandate_fields()
    del fields[missing]
    with pytest.raises(ValidationError, match="Field required"):
        MandateSpec.model_validate(fields)


def test_mandate_is_frozen_closed_and_round_trips_qualified_keys():
    from pydantic import ValidationError

    from kodezart.types.domain.organize import MandateSpec

    spec = MandateSpec.model_validate(mandate_fields())
    assert MandateSpec.model_validate_json(spec.model_dump_json(by_alias=True)) == spec
    with pytest.raises(ValidationError, match="frozen"):
        spec.gate_label_key = "scope_labels.proposed"
    with pytest.raises(ValidationError, match="Extra inputs"):
        MandateSpec.model_validate(mandate_fields(mode="ticket"))


@pytest.mark.parametrize("field", ["gate_label_key", "terminal_marker_key"])
@pytest.mark.parametrize(
    "reference",
    [
        "triage",
        "scope:triage",
        "queue_states.triage",
        "issue_labels.",
        "issue_labels. ",
    ],
)
def test_mandate_refs_name_an_explicit_supported_mapping(field, reference):
    from pydantic import ValidationError

    from kodezart.types.domain.organize import MandateSpec

    with pytest.raises(ValidationError):
        MandateSpec.model_validate(mandate_fields(**{field: reference}))


@pytest.mark.parametrize("namespace", ["scope_labels", "issue_labels"])
def test_gate_reference_can_name_either_label_family_without_guessing(namespace):
    from kodezart.types.domain.organize import MandateSpec, split_label_key

    reference = f"{namespace}.body.complete"
    spec = MandateSpec.model_validate(mandate_fields(gate_label_key=reference))
    family, key = split_label_key(spec.gate_label_key)
    assert family.value == namespace
    assert key == "body.complete"


@pytest.mark.parametrize(
    "reference", ["scope_labels.proposed", "scope_labels.approved"]
)
def test_phase_completion_is_an_issue_marker_not_scope_approval(reference):
    from pydantic import ValidationError

    from kodezart.types.domain.organize import MandateSpec

    with pytest.raises(ValidationError, match="issue_labels marker"):
        MandateSpec.model_validate(mandate_fields(terminal_marker_key=reference))


@pytest.mark.parametrize("field", ["rubric_prompt_key", "admission_prompt_key"])
def test_mandate_prompt_references_must_be_registered_prompt_roles(field):
    from pydantic import ValidationError

    from kodezart.types.domain.organize import MandateSpec

    with pytest.raises(ValidationError):
        MandateSpec.model_validate(mandate_fields(**{field: "invented_prompt_role"}))


def mandate_operation_fields():
    return {
        "operation_name": "fixture",
        "workspace": "workspace",
        "scope_labels": {
            "triage": "candidate scope",
            "proposed": "proposed scope",
            "approved": "approved scope",
        },
        "issue_labels": {
            "criterion": "check",
            "triage": "candidate issue",
            "groomed": "graph complete",
            "body": "body complete",
            "criteria": "criteria complete",
        },
        "organize_mandates": [
            mandate_fields(),
            mandate_fields(
                kind="ticket",
                gate_label_key="issue_labels.groomed",
                terminal_marker_key="issue_labels.body",
            ),
            mandate_fields(
                kind="criteria",
                gate_label_key="issue_labels.body",
                terminal_marker_key="issue_labels.criteria",
            ),
        ],
    }


def test_all_phase_references_resolve_to_operation_values_at_construction():
    from kodezart.types.domain.operation import OperationConfig

    operation = OperationConfig.model_validate(mandate_operation_fields())
    phases = operation.resolve_organize_mandates()
    assert [
        (phase.spec.kind.value, phase.gate_label, phase.terminal_marker)
        for phase in phases
    ] == [
        ("groom", "candidate scope", "graph complete"),
        ("ticket", "graph complete", "body complete"),
        ("criteria", "body complete", "criteria complete"),
    ]
    restored = OperationConfig.model_validate_json(operation.model_dump_json())
    assert restored.resolve_organize_mandates() == phases


def test_absent_mandate_table_is_legal_without_any_label_mapping():
    from kodezart.types.domain.operation import OperationConfig

    operation = OperationConfig(operation_name="fixture", workspace="workspace")
    assert operation.organize_mandates == ()
    assert operation.resolve_organize_mandates() == ()


@pytest.mark.parametrize("missing", ["groom", "ticket", "criteria"])
def test_a_declared_table_cannot_omit_a_phase(missing):
    from pydantic import ValidationError

    from kodezart.types.domain.operation import OperationConfig

    fields = mandate_operation_fields()
    fields["organize_mandates"] = [
        spec for spec in fields["organize_mandates"] if spec["kind"] != missing
    ]
    with pytest.raises(ValidationError, match=f"missing phase '{missing}'"):
        OperationConfig.model_validate(fields)


def test_duplicate_phases_are_rejected_even_with_every_phase_present():
    from pydantic import ValidationError

    from kodezart.types.domain.operation import OperationConfig

    fields = mandate_operation_fields()
    fields["organize_mandates"].append(fields["organize_mandates"][1])
    with pytest.raises(ValidationError, match="repeats phase 'ticket'"):
        OperationConfig.model_validate(fields)


@pytest.mark.parametrize("phase", [0, 1, 2])
@pytest.mark.parametrize("field", ["gate_label_key", "terminal_marker_key"])
def test_unmapped_keys_fail_while_constructing_the_operation(phase, field):
    from pydantic import ValidationError

    from kodezart.types.domain.operation import OperationConfig

    fields = mandate_operation_fields()
    fields["organize_mandates"][phase][field] = "issue_labels.missing"
    with pytest.raises(
        ValidationError, match=r"no nonempty mapping.*issue_labels.missing"
    ):
        OperationConfig.model_validate(fields)


def test_all_bad_references_are_reported_in_the_same_load_error():
    from pydantic import ValidationError

    from kodezart.types.domain.operation import OperationConfig

    fields = mandate_operation_fields()
    fields["organize_mandates"][0]["gate_label_key"] = "scope_labels.missing_gate"
    fields["organize_mandates"][2]["terminal_marker_key"] = "issue_labels.missing_end"
    with pytest.raises(ValidationError) as caught:
        OperationConfig.model_validate(fields)
    assert "scope_labels.missing_gate" in str(caught.value)
    assert "issue_labels.missing_end" in str(caught.value)


def test_qualified_reference_selects_the_mapping_even_when_keys_overlap():
    from kodezart.types.domain.operation import OperationConfig

    fields = mandate_operation_fields()
    fields["organize_mandates"][0]["gate_label_key"] = "issue_labels.triage"
    operation = OperationConfig.model_validate(fields)
    assert operation.resolve_organize_mandates()[0].gate_label == "candidate issue"


@pytest.mark.parametrize("field", ["gate_label_key", "terminal_marker_key"])
def test_scope_approval_cannot_be_used_through_an_issue_label_alias(field):
    from pydantic import ValidationError

    from kodezart.types.domain.operation import OperationConfig

    fields = mandate_operation_fields()
    fields["issue_labels"]["approval_alias"] = fields["scope_labels"]["approved"]
    fields["organize_mandates"][1][field] = "issue_labels.approval_alias"
    with pytest.raises(ValidationError, match="scope approval, which ends organize"):
        OperationConfig.model_validate(fields)


def test_scope_approval_is_not_an_organize_gate():
    from pydantic import ValidationError

    from kodezart.types.domain.operation import OperationConfig

    fields = mandate_operation_fields()
    fields["organize_mandates"][0]["gate_label_key"] = "scope_labels.approved"
    with pytest.raises(ValidationError, match="scope approval, which ends organize"):
        OperationConfig.model_validate(fields)


def test_blank_scope_label_is_not_a_resolved_gate():
    from pydantic import ValidationError

    from kodezart.types.domain.operation import OperationConfig

    fields = mandate_operation_fields()
    fields["scope_labels"]["triage"] = " "
    with pytest.raises(ValidationError, match="no nonempty mapping"):
        OperationConfig.model_validate(fields)


def write_mandate_operation(path, fields):
    import json

    lines = [
        f"operation_name = {json.dumps(fields['operation_name'])}",
        f"workspace = {json.dumps(fields['workspace'])}",
    ]
    for namespace in ("scope_labels", "issue_labels"):
        lines.append(f"[{namespace}]")
        lines.extend(
            f"{json.dumps(key)} = {json.dumps(value)}"
            for key, value in fields[namespace].items()
        )
    for spec in fields["organize_mandates"]:
        lines.append("[[organize_mandates]]")
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in spec.items())
    path.write_text("\n".join(lines))


def test_normal_toml_loader_resolves_the_declared_phase_table(tmp_path):
    from kodezart.adapters.toml_operation_config import load_operation_config

    path = tmp_path / "operation.toml"
    write_mandate_operation(path, mandate_operation_fields())
    operation = load_operation_config(path)
    assert (
        operation.resolve_organize_mandates()[2].terminal_marker == "criteria complete"
    )


@pytest.mark.parametrize("field", ["gate_label_key", "terminal_marker_key"])
async def test_invalid_table_stops_normal_boot_before_tracker_or_dispatch(
    tmp_path, monkeypatch, field
):
    from kodezart.core.config import AppConfig
    from kodezart.core.errors import OperationConfigError
    from kodezart.main import create_app, lifespan

    fields = mandate_operation_fields()
    fields["organize_mandates"][0][field] = "issue_labels.absent_at_boot"
    path = tmp_path / "operation.toml"
    write_mandate_operation(path, fields)

    async def unexpected_tracker_boot(**kwargs):
        pytest.fail("tracker boot started with an unresolved mandate reference")

    monkeypatch.setattr("kodezart.main.boot_tracker", unexpected_tracker_boot)
    app = create_app()
    app.state.config = AppConfig(operation_config=str(path), github_token=None)
    with pytest.raises(OperationConfigError, match=r"operation\.toml is invalid"):
        async with lifespan(app):
            pytest.fail(
                "dispatch became available with an unresolved mandate reference"
            )
