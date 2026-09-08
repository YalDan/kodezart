"""The ruling role is a required data template served by application boot."""

import shutil

import pytest
import structlog

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.composition.prompts import boot_prompts
from kodezart.core.config import AppConfig
from kodezart.core.errors import PromptRenderError, PromptResolutionError
from kodezart.types.domain.prompts import PromptKey, SessionRole
from tests.prompts.sets import OPUS_SET, V5_SET
from tests.prompts.test_prompt_wiring import load_registry
from tests.prompts.test_session_policy import v5_metadata

CASE = {
    "issue_key": "native/issue",
    "issue_body": "The requested deliverable and its open question.",
    "criteria": '[{"issue_key":"native/criterion","body":"The native Check."}]',
    "base_ref": "recorded-exact-sha",
    "validation_findings": "The independently observed feasibility verdicts.",
}


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
async def test_ruling_template_is_required_by_actual_application_boot(
    set_name, tmp_path, monkeypatch
):
    shutil.copytree(default_sets_root() / set_name, tmp_path / set_name)
    key = PromptKey("fire_time_ruling")
    (tmp_path / set_name / f"{key.value}.md").unlink()
    monkeypatch.setattr(
        "kodezart.composition.prompts.default_sets_root", lambda: tmp_path
    )
    with pytest.raises(PromptResolutionError) as caught:
        await boot_prompts(
            config=AppConfig(prompt_set=set_name),
            operation=None,
            log=structlog.get_logger(__name__),
        )
    assert caught.value.failing_keys == (key.value,)


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
def test_ruling_role_renders_native_inputs_without_a_previous_calls_values(set_name):
    key = PromptKey("fire_time_ruling")
    registry = load_registry(default_set=set_name)
    template = registry.template_for(key)
    rendered = template.render(CASE)
    assert registry.resolution_table()[key] == set_name
    assert (default_sets_root() / set_name / "fire_time_ruling.md").is_file()
    for value in CASE.values():
        assert value in rendered
    alternate = {name: "different " + value for name, value in CASE.items()}
    assert template.render(alternate) != rendered
    assert template.render(CASE) == rendered
    assert "{{" not in rendered


@pytest.mark.parametrize("missing", list(CASE))
def test_ruling_source_cannot_be_silently_omitted(missing):
    template = load_registry().template_for(PromptKey("fire_time_ruling"))
    incomplete = dict(CASE)
    del incomplete[missing]
    with pytest.raises(PromptRenderError):
        template.render(incomplete)


def test_ruling_role_uses_existing_evaluative_session_policy():
    key = PromptKey("fire_time_ruling")
    metadata = v5_metadata()
    assert metadata.role_of(key.value) is SessionRole.EVALUATIVE
    registry = load_registry(default_set=V5_SET)
    assert registry.session_policy(key) == registry.session_policy(
        PromptKey.CRITERIA_VALIDATION
    )
