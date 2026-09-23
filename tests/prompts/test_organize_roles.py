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


#: Every set shipped, read off the sets root rather than listed here, so a set
#: added beside the two today is held to the same paragraph.
SHIPPED_SETS = sorted(
    path.name for path in default_sets_root().iterdir() if path.is_dir()
)

#: The load-bearing sentences of the checklist-adoption paragraph, one clause
#: each: adopt and do not restate, one criterion per item not already stated,
#: the item's own text as the Check, nothing reworded, merged, split or
#: dropped, and the body left where it is.
CHECKLIST_ADOPTION = (
    "When the issue body already carries a checklist a person wrote, adopt it "
    "rather than restating it:",
    "propose exactly one criterion for each checklist item that no existing "
    "criterion's Check already states,",
    "use the item's own text, unchanged and without its list marker or tick box, "
    "as that criterion's Check.",
    "Do not reword, merge, split or drop an item,",
    "do not propose moving or removing the checklist; the body stays as it is.",
)

#: The landed sentence that keeps adoption from becoming an edit of a child.
NO_CRITERION_EDIT = "Editing an existing criterion is unavailable"


def rendered_criteria_author(set_name: str) -> str:
    """The criteria author as *set_name* renders it, whitespace folded."""
    template = load_registry(default_set=set_name).template_for(
        PromptKey.ORGANIZE_CRITERIA_AUTHOR
    )
    return " ".join(template.render(ORGANIZE_CASE).split())


def adoption_paragraph(rendered: str) -> str:
    """The paragraph from its first sentence to its last, as rendered."""
    start = rendered.index(CHECKLIST_ADOPTION[0])
    end = rendered.index(CHECKLIST_ADOPTION[-1], start) + len(CHECKLIST_ADOPTION[-1])
    return rendered[start:end]


@pytest.mark.parametrize("set_name", SHIPPED_SETS)
def test_the_criteria_author_adopts_a_body_checklist_verbatim_in_every_set(
    set_name: str,
) -> None:
    """Each set tells the criteria author to adopt a checklist, not restate it.

    One assertion per sentence, so a set that loses or rewords any clause is
    named, and then the paragraph itself is compared with every other set's:
    equality alone would hold for two sets that both lacked it.
    """
    assert {V5_SET, OPUS_SET} <= set(SHIPPED_SETS)
    rendered = rendered_criteria_author(set_name)

    for sentence in CHECKLIST_ADOPTION:
        assert sentence in rendered, sentence
    assert NO_CRITERION_EDIT in rendered
    # The paragraph is the only place the prompt speaks of a checklist, so no
    # second sentence elsewhere can tell the author to treat one differently.
    assert rendered.count("checklist") == adoption_paragraph(rendered).count(
        "checklist"
    )
    assert {
        adoption_paragraph(rendered_criteria_author(other)) for other in SHIPPED_SETS
    } == {adoption_paragraph(rendered)}
