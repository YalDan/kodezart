"""Grooming reads configured phase evidence without assuming a phase table."""

import pytest

from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.prompts import PromptKey
from tests.domain.test_organize import mandate_operation_fields
from tests.fakes import pass_render_variables
from tests.prompts.test_operation_config import raw_example
from tests.prompts.test_prompt_wiring import load_registry


def declared_operation():
    fields = raw_example()
    declared = mandate_operation_fields()
    for key in ("scope_labels", "issue_labels", "organize_mandates"):
        fields[key] = declared[key]
    return OperationConfig.model_validate(fields)


def test_operation_binding_contains_resolved_phase_metadata():
    bindings = operation_bindings(declared_operation())
    assert bindings["organize_mandates"] == [
        {
            "kind": "groom",
            "gate_label": "candidate scope",
            "terminal_marker": "graph complete",
        },
        {
            "kind": "ticket",
            "gate_label": "graph complete",
            "terminal_marker": "body complete",
        },
        {
            "kind": "criteria",
            "gate_label": "body complete",
            "terminal_marker": "criteria complete",
        },
    ]
    assert bindings["organize_mandates_absent"] is None


@pytest.mark.parametrize("prompt_set", ["anthropic_v5", "claude-opus"])
@pytest.mark.parametrize("declared", [False, True])
def test_grooming_consumes_the_declared_or_absent_phase_table(prompt_set, declared):
    operation = (
        declared_operation()
        if declared
        else OperationConfig.model_validate(raw_example())
    )
    registry = load_registry(
        default_set=prompt_set, bindings=operation_bindings(operation)
    )
    rendered = registry.template_for(PromptKey.GROOMING_PASS).render(
        {**pass_render_variables(PromptKey.GROOMING_PASS), "skills_reference": ""}
    )
    if declared:
        for line in (
            "groom: gate `candidate scope`, completion `graph complete`.",
            "ticket: gate `graph complete`, completion `body complete`.",
            "criteria: gate `body complete`, completion `criteria complete`.",
        ):
            assert line in rendered
        assert "Report discrepancies without setting phase markers" in rendered
        assert "The operation declares these organize phase markers" in rendered
    else:
        assert "The operation declares these organize phase markers" not in rendered
        assert (
            "do not infer phase completion from labels or invent phase mappings"
            in rendered
        )
        assert "completion `graph complete`" not in rendered
        bindings = operation_bindings(operation)
        assert bindings["organize_mandates"] is None
        assert bindings["organize_mandates_absent"] is True
