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

ORGANIZE_ROLES = {
    "ORGANIZE_ASSESS": "organize_assess",
    "ORGANIZE_AUTHOR": "organize_author",
    "ORGANIZE_VERIFY": "organize_verify",
    "ORGANIZE_CRITERIA_AUTHOR": "organize_criteria_author",
}


def test_organize_roles_have_the_named_enum_members_and_values() -> None:
    for name, value in ORGANIZE_ROLES.items():
        assert PromptKey[name].value == value


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
@pytest.mark.parametrize("name", ORGANIZE_ROLES)
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


def _flowed(rendered: str) -> str:
    """Prompt prose is hard-wrapped; a sentence is read across its line breaks."""
    return " ".join(rendered.split())


def _render(set_name: str, name: str) -> str:
    return (
        load_registry(default_set=set_name)
        .template_for(PromptKey[name])
        .render(ORGANIZE_CASE)
    )


#: KOD-74-AC-32 — the gradability question, as a rendered judging prompt must
#: carry it: the environments this scope declares are searched, a deliverable
#: none of them demonstrates is refused rather than admitted, and the refusal
#: names where the demonstration goes instead.
GRADABILITY_INSTRUCTION = (
    "gradability as well as buildability",
    "environments its work runs and is demonstrated in",
    "demonstrable in none of them is not_buildable",
    "undemonstrable refusal",
    "names the environments searched",
    "demonstration is relocated to",
)

#: The same for the criteria author: a criterion is authored only with the
#: thing that will grade it named.
DEMONSTRATION_INSTRUCTION = (
    "Evidence can actually be filled",
    "exact runnable test that will demonstrate it at the graded commit",
    "observation that will be recorded instead",
    "names neither is refused before it is created",
)


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
@pytest.mark.parametrize("name", ["ORGANIZE_ASSESS", "ORGANIZE_VERIFY"])
@pytest.mark.parametrize("instruction", GRADABILITY_INSTRUCTION)
def test_each_judging_prompt_asks_gradability_over_the_declared_environments(
    set_name: str, name: str, instruction: str
) -> None:
    """A wiring claim about the rendered prompt, never a compliance one.

    That the question is asked is not that the answer obeys it; what the
    typed refusal carries once it is answered is asserted over the owner in
    ``tests/chains/test_organize_owner.py``.
    """
    assert instruction in _flowed(_render(set_name, name))


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
@pytest.mark.parametrize("instruction", DEMONSTRATION_INSTRUCTION)
def test_the_criteria_author_is_asked_for_a_test_or_an_observation(
    set_name: str, instruction: str
) -> None:
    """The author names what will fill Evidence, or the criterion is refused."""
    assert instruction in _flowed(_render(set_name, "ORGANIZE_CRITERIA_AUTHOR"))


@pytest.mark.parametrize("set_name", [OPUS_SET, V5_SET])
def test_the_body_author_is_asked_neither_question(set_name: str) -> None:
    """The negative control: neither instruction is corpus-wide boilerplate."""
    rendered = _flowed(_render(set_name, "ORGANIZE_AUTHOR"))
    assert "gradability as well as buildability" not in rendered
    assert "Evidence can actually be filled" not in rendered
