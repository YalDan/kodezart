"""Organize evidence varies per dispatch and cannot collide with boot bindings."""

import json
from pathlib import Path

import pytest
import structlog
from pydantic import create_model

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.prompts import boot_prompts
from kodezart.core.config import AppConfig
from kodezart.core.errors import PromptNamespaceCollisionError, PromptRenderError
from kodezart.core.prompt_namespaces import (
    PER_CALL_VARIABLE_NAMES,
    SET_FRAGMENT_NAMES,
    bindings_for,
)
from kodezart.domain.prompt_variables import organize_variables
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.organize import (
    AdmissionResult,
    AdmissionVerdict,
    RefusalKind,
)
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.sets import EXAMPLE_OPERATION, OPUS_SET, V5_SET
from tests.prompts.test_prompt_wiring import load_registry

ORGANIZE_BINDINGS = {
    "mandate_rubric",
    "issue_body",
    "linked_issue_bodies",
    "criterion_issue_bodies",
    "refusal_evidence",
    "defect_classes",
}
READ_ROLES = [PromptKey.ORGANIZE_ASSESS, PromptKey.ORGANIZE_VERIFY]
AUTHOR_ROLES = [PromptKey.ORGANIZE_AUTHOR, PromptKey.ORGANIZE_CRITERIA_AUTHOR]


def refusal() -> AdmissionResult:
    return AdmissionResult(
        issue_id="external/42",
        verdict=AdmissionVerdict.NOT_BUILDABLE,
        invented_decision="The response fields are unspecified.",
        evidence="The output model is absent from the issue body.",
        refusal_kind=RefusalKind.SPEC_GAP,
    )


def variables(rubric: str = "Selected rubric") -> dict[str, object]:
    return organize_variables(
        mandate_rubric=rubric,
        issue_body="Current source body\nwith a second line.",
        linked_issue_bodies=["First linked body", "Second linked body"],
        criterion_issue_bodies=["First criterion body", "Second criterion body"],
        refusal_evidence=refusal(),
        defect_classes=["underspecified-model", "unsupported-claim"],
    )


def test_named_per_call_roster_is_disjoint_from_both_other_namespaces() -> (
    None
):
    assert set(variables()) == ORGANIZE_BINDINGS
    assert ORGANIZE_BINDINGS <= PER_CALL_VARIABLE_NAMES
    assert ORGANIZE_BINDINGS.isdisjoint(SET_FRAGMENT_NAMES)
    operation = load_operation_config(EXAMPLE_OPERATION)
    assert ORGANIZE_BINDINGS.isdisjoint(bindings_for(operation))
    assert ORGANIZE_BINDINGS.isdisjoint(OperationConfig.model_fields)


@pytest.mark.parametrize("name", sorted(ORGANIZE_BINDINGS))
async def test_a_configuration_field_collision_is_named_at_prompt_boot(
    name: str, tmp_path: Path
) -> None:
    """A fixture schema extension cannot capture a reserved per-call root.

    Production config forbids unknown fields. A derived fixture models an
    author adding such a field; boot must refuse it even before a template
    or operation projection has started consuming that field.
    """
    fixture_type = create_model(
        "CollidingOperation", __base__=OperationConfig, **{name: (str, ...)}
    )
    path = tmp_path / "operation.toml"
    path.write_text(
        'operation_name = "fixture"\nworkspace = "fixture"\n', encoding="utf-8"
    )
    operation = fixture_type.model_validate(
        {**load_operation_config(path).model_dump(), name: "configured collision"}
    )
    with pytest.raises(PromptNamespaceCollisionError) as caught:
        await boot_prompts(
            config=AppConfig(), operation=operation, log=structlog.get_logger(__name__)
        )
    assert caught.value.colliding == (name,)


@pytest.mark.parametrize("name", sorted(ORGANIZE_BINDINGS))
async def test_a_projected_configuration_binding_collision_is_named_at_boot(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation = load_operation_config(EXAMPLE_OPERATION)
    projected = {**bindings_for(operation), name: "fixture projected binding"}
    monkeypatch.setattr(
        "kodezart.core.prompt_namespaces.operation_bindings", lambda _: projected
    )
    with pytest.raises(PromptNamespaceCollisionError) as caught:
        await boot_prompts(
            config=AppConfig(), operation=operation, log=structlog.get_logger(__name__)
        )
    assert caught.value.colliding == (name,)


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
@pytest.mark.parametrize("key", [*READ_ROLES, *AUTHOR_ROLES])
def test_two_calls_render_distinct_rubrics_and_all_current_source_bodies(
    set_name: str, key: PromptKey
) -> None:
    registry = load_registry(default_set=set_name)
    template = registry.template_for(key)
    first = template.render({**variables("First rubric"), "base_ref": "selected/base"})
    second = template.render(
        {**variables("Second rubric"), "base_ref": "selected/base"}
    )
    assert "First rubric" in first and "Second rubric" not in first
    assert "Second rubric" in second and "First rubric" not in second
    assert ORGANIZE_BINDINGS.isdisjoint(template.bindings)
    for body in (
        "Current source body\nwith a second line.",
        "First linked body",
        "Second linked body",
        "First criterion body",
        "Second criterion body",
        "underspecified-model",
        "unsupported-claim",
        "selected/base",
    ):
        assert body in first
        assert body in second


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
@pytest.mark.parametrize("key", READ_ROLES)
def test_read_only_roles_do_not_render_prior_refusal_or_author_rationale(
    set_name: str, key: PromptKey
) -> None:
    rendered = (
        load_registry(default_set=set_name)
        .template_for(key)
        .render(
            {
                **variables(),
                "base_ref": "main",
                "refusal_evidence": "Prior refusal must not enter fresh judgment",
                "author_reasoning": "Author rationale must not enter fresh judgment",
            }
        )
    )
    assert "Prior refusal must not enter fresh judgment" not in rendered
    assert "Author rationale must not enter fresh judgment" not in rendered


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
@pytest.mark.parametrize("key", AUTHOR_ROLES)
def test_authoring_receives_the_typed_refusal_decision_and_evidence(
    set_name: str, key: PromptKey
) -> None:
    supplied = variables()
    assert json.loads(str(supplied["refusal_evidence"])) == refusal().model_dump(
        mode="json", by_alias=False
    )
    rendered = (
        load_registry(default_set=set_name)
        .template_for(key)
        .render({**supplied, "base_ref": "main"})
    )
    assert refusal().invented_decision in rendered
    assert refusal().evidence in rendered
    assert RefusalKind.SPEC_GAP.value in rendered


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
@pytest.mark.parametrize("key", [*READ_ROLES, *AUTHOR_ROLES])
def test_an_empty_child_family_and_first_authoring_round_remain_explicit(
    set_name: str, key: PromptKey
) -> None:
    supplied = organize_variables(
        mandate_rubric="First round rubric",
        issue_body="Source without children",
        linked_issue_bodies=(),
        criterion_issue_bodies=(),
        refusal_evidence=None,
        defect_classes=(),
    )
    assert supplied["linked_issue_bodies"] == ()
    assert supplied["criterion_issue_bodies"] == ()
    assert supplied["defect_classes"] == ()
    assert supplied["refusal_evidence"] is None
    rendered = (
        load_registry(default_set=set_name)
        .template_for(key)
        .render({**supplied, "base_ref": "main"})
    )
    assert "<refusal_evidence>" not in rendered
    assert "None" not in rendered
    assert "{{" not in rendered


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
@pytest.mark.parametrize("key", [*READ_ROLES, *AUTHOR_ROLES])
@pytest.mark.parametrize(
    "missing",
    ["mandate_rubric", "issue_body", "linked_issue_bodies", "criterion_issue_bodies"],
)
def test_required_source_bindings_cannot_silently_disappear(
    set_name: str, key: PromptKey, missing: str
) -> None:
    supplied = variables()
    del supplied[missing]
    with pytest.raises(PromptRenderError):
        load_registry(default_set=set_name).template_for(key).render(
            {**supplied, "base_ref": "main"}
        )
