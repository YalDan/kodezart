"""Configured repository marker reaches each actual pass prompt that writes it."""

import pytest

from kodezart.core.prompt_namespaces import bindings_for
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.prompts import PromptKey
from tests.fakes import pass_render_variables
from tests.prompts.test_operation_config import raw_example
from tests.prompts.test_prompt_wiring import load_registry


@pytest.mark.parametrize(
    ("prompt_set", "key"),
    [
        ("claude-opus", PromptKey.FIRE_PREP_PASS),
        ("claude-opus", PromptKey.GROOMING_PASS),
        ("anthropic_v5", PromptKey.FIRE_PREP_PASS),
    ],
)
def test_actual_staging_prompt_renders_configured_native_repository_marker(
    prompt_set, key
):
    raw = raw_example()
    for team in raw["teams"].values():
        team.pop("repository", None)
    if len(raw["repos"]) == 1:
        raw["repos"].append(
            {**raw["repos"][0], "url": "https://example.invalid/fixture/second"}
        )
    raw["marker_prefixes"]["repository"] = "independent-repository-marker"
    operation = OperationConfig.model_validate(raw)
    registry = load_registry(default_set=prompt_set, bindings=bindings_for(operation))
    rendered = registry.template_for(key).render(pass_render_variables(key))
    assert '<!-- independent-repository-marker url="..." -->' in rendered
    assert '<!-- kodezart-repo url="..." -->' not in rendered
    assert "{{marker_prefixes.repository}}" not in rendered
