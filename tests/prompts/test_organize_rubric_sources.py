"""Rubric sources follow explicit set selection without changing session bodies."""

import pytest
from pydantic import ValidationError

from kodezart.core.errors import PromptResolutionError
from kodezart.types.domain.prompts import PromptKey, PromptSetMetadata
from tests.prompts.test_prompt_wiring import complete_members, load_registry, write_set


@pytest.mark.parametrize("prompt_set", ["claude-opus", "anthropic_v5"])
@pytest.mark.parametrize(
    "key",
    [PromptKey.GROOMING_PASS, PromptKey.TICKET_REVIEW, PromptKey.CRITERIA_VALIDATION],
)
def test_native_rubric_is_separate_from_its_original_session_body(prompt_set, key):
    template = load_registry(default_set=prompt_set).template_for(key)
    rubric = template.rubric_template()
    assert rubric.key is key
    assert rubric.source == template.source
    assert rubric.bindings == template.bindings
    assert rubric.body not in template.body
    assert rubric.render({}).strip()
    assert rubric.rubric_body is None


@pytest.mark.parametrize("declare", [False, True])
def test_selected_set_owns_the_rubric_and_never_borrows_default(tmp_path, declare):
    write_set(tmp_path, "base", complete_members("base"))
    override = write_set(tmp_path, "override", {"grooming_pass": "override session"})
    base_metadata = tmp_path / "base" / "set.toml"
    base_metadata.write_text(
        base_metadata.read_text() + '\n[rubrics]\ngrooming_pass = "base rubric"\n'
    )
    if declare:
        path = override / "set.toml"
        path.write_text(
            path.read_text() + '\n[rubrics]\ngrooming_pass = "selected rubric"\n'
        )
    template = load_registry(
        sets_root=tmp_path,
        default_set="base",
        set_overrides={"grooming_pass": "override"},
    ).template_for(PromptKey.GROOMING_PASS)
    assert template.body == "override session"
    if declare:
        assert template.rubric_template().render({}) == "selected rubric"
    else:
        with pytest.raises(PromptResolutionError, match="grooming_pass"):
            template.rubric_template()


def test_template_path_override_does_not_borrow_a_set_rubric(tmp_path):
    path = tmp_path / "scheduled.md"
    path.write_text("Explicit scheduled body.")
    template = load_registry(
        template_overrides={"grooming_pass": str(path)}
    ).template_for(PromptKey.GROOMING_PASS)
    assert template.body == "Explicit scheduled body."
    with pytest.raises(PromptResolutionError, match="grooming_pass"):
        template.rubric_template()


@pytest.mark.parametrize(
    "rubrics", [{"unknown": "text"}, {"grooming_pass": ""}, {"grooming_pass": "   "}]
)
def test_rubric_metadata_rejects_unknown_keys_and_empty_sources(rubrics):
    with pytest.raises(ValidationError):
        PromptSetMetadata.model_validate(
            {
                "name": "test",
                "engines": [],
                "fragments": {"skills_reference_header": ""},
                "rubrics": rubrics,
            }
        )
