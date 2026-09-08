"""Native initiative membership and dates have no copied config roster."""

import re

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.prompts import boot_prompts
from kodezart.core.config import AppConfig
from kodezart.core.errors import OperationConfigError
from kodezart.core.logging import get_logger
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.test_operation_config import raw_example, write_toml


@pytest.mark.parametrize(
    "roster",
    [
        [],
        [{"id": "stale-native-identity"}],
        [{"id": "stale", "target_date": "2099-12-31"}],
    ],
)
def test_retired_initiative_roster_refuses_at_the_real_toml_loader(tmp_path, roster):
    raw = raw_example()
    raw["initiatives"] = roster
    with pytest.raises(OperationConfigError) as raised:
        load_operation_config(write_toml(tmp_path, raw))
    assert "initiatives" in str(raised.value)
    assert "2099-12-31" not in str(raised.value)
    assert "stale-native-identity" not in str(raised.value)


@pytest.mark.parametrize("prompt_set", ["claude-opus", "anthropic_v5"])
async def test_composed_grooming_renders_without_copied_initiative_ids_or_dates(
    tmp_path, prompt_set
):
    raw = raw_example()
    raw.pop("initiatives", None)
    operation = load_operation_config(write_toml(tmp_path, raw))
    prompts = await boot_prompts(
        config=AppConfig(prompt_set=prompt_set),
        operation=operation,
        log=get_logger("test.native_initiatives"),
    )
    rendered = prompts.template_for(PromptKey.GROOMING_PASS).render(
        {"record_title": "grooming fixture", "skills_reference": ""}
    )
    assert rendered and "{{" not in rendered
    assert "example-initiative" not in rendered
    assert re.search(r"\b\d{4}-\d{2}-\d{2}\b", rendered) is None
    assert operation.teams and operation.repos
    if prompt_set == "claude-opus":
        guidance = " ".join(rendered.lower().split())
        for required in (
            "every issue in the declared teams, narrowed by their declared scopes",
            "discover their projects and initiatives from the current tracker",
            "read each applicable initiative and project's current target date "
            "from the tracker",
            "an absent target stays absent, never invented or borrowed",
            "never move a date",
        ):
            assert required in guidance
