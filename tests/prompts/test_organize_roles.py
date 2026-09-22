"""Each organize role is independently resolved and required at boot."""

import shutil
from pathlib import Path

import pytest
import structlog

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.composition.prompts import boot_prompts
from kodezart.config.app import AppConfig
from kodezart.core.errors import PromptResolutionError
from kodezart.types.domain.prompts import PromptKey, SessionRole
from tests.prompts.sets import OPUS_SET, ORGANIZE_CASE, V5_SET
from tests.prompts.test_prompt_wiring import load_registry

#: The roles that open a session of their own.
ORGANIZE_ROLES = {
    "ORGANIZE_ASSESS": "organize_assess",
    "ORGANIZE_AUTHOR": "organize_author",
    "ORGANIZE_VERIFY": "organize_verify",
    "ORGANIZE_CRITERIA_AUTHOR": "organize_criteria_author",
}

#: The roles that state a row's accept conditions. A rubric opens no session:
#: it is rendered into the per-call rubric binding of whichever role above
#: judges the row, which is why it is resolved and required exactly as they
#: are and renders none of their per-call evidence.
ORGANIZE_RUBRIC_ROLES = {
    "ORGANIZE_GROOM_RUBRIC": "organize_groom_rubric",
    "ORGANIZE_SPEC_RUBRIC": "organize_spec_rubric",
}

ALL_ORGANIZE_ROLES = {**ORGANIZE_ROLES, **ORGANIZE_RUBRIC_ROLES}


def test_organize_roles_have_the_named_enum_members_and_values() -> None:
    for name, value in ALL_ORGANIZE_ROLES.items():
        assert PromptKey[name].value == value


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
@pytest.mark.parametrize("name", ALL_ORGANIZE_ROLES)
async def test_removing_each_organize_data_file_fails_application_prompt_boot(
    set_name: str, name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep metadata complete so the missing file itself causes the failure."""
    shutil.copytree(default_sets_root() / set_name, tmp_path / set_name)
    key = PromptKey[name]
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
@pytest.mark.parametrize("name", ORGANIZE_ROLES)
def test_each_role_renders_its_own_data_file(set_name: str, name: str) -> None:
    key = PromptKey[name]
    registry = load_registry(default_set=set_name)
    template = registry.template_for(key)
    assert registry.resolution_table()[key] == set_name
    assert (default_sets_root() / set_name / f"{key.value}.md").is_file()
    rendered = template.render(ORGANIZE_CASE)
    assert "Golden mandate rubric" in rendered
    assert "Golden source issue body" in rendered
    assert "Golden criterion issue body" in rendered
    assert "{{" not in rendered


def test_organize_roles_inherit_the_existing_authorship_and_judgment_policies() -> None:
    registry = load_registry(default_set=V5_SET)
    from tests.prompts.test_session_policy import v5_metadata

    metadata = v5_metadata()
    expected = {
        PromptKey.ORGANIZE_ASSESS: SessionRole.EVALUATIVE,
        PromptKey.ORGANIZE_VERIFY: SessionRole.EVALUATIVE,
        PromptKey.ORGANIZE_AUTHOR: SessionRole.GENERATIVE,
        PromptKey.ORGANIZE_CRITERIA_AUTHOR: SessionRole.GENERATIVE,
    }
    for key, role in expected.items():
        assert metadata.role_of(key.value) is role
        assert (
            registry.session_policy(key).effort is metadata.session_roles[role].effort
        )


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
@pytest.mark.parametrize("name", ORGANIZE_RUBRIC_ROLES)
def test_each_rubric_renders_without_placeholders(set_name: str, name: str) -> None:
    """A rubric is rendered into another role's prompt, so it carries no hole.

    It renders none of the per-call evidence the judging roles render: the
    accept conditions are the same whichever issue is being judged against
    them.
    """
    key = PromptKey[name]
    registry = load_registry(default_set=set_name)
    template = registry.template_for(key)
    assert registry.resolution_table()[key] == set_name
    rendered = template.render(ORGANIZE_CASE)
    assert "{{" not in rendered
    assert "Golden source issue body" not in rendered
    assert "Golden mandate rubric" not in rendered
