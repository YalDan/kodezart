"""The pre-loop question role is a data file in every shipped set (KOD-638).

A role is a key resolved through the provider; which template serves it is a
file in a set directory and never a Python module. Both halves are asserted
here: every shipped set supplies the member, and the prompts package holds no
code that could stand in for one.
"""

import pytest

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.sets import OPUS_SET, V5_SET, operation_registry, render_case

MEMBER = f"{PromptKey.FIRE_TIME_RULING.value}.md"


def shipped_sets() -> list[str]:
    return sorted(path.name for path in default_sets_root().iterdir() if path.is_dir())


def test_the_role_is_a_data_file_in_every_shipped_set() -> None:
    """Derived from the sets root, so a third set is covered the day it lands."""
    root = default_sets_root()
    names = shipped_sets()

    assert {OPUS_SET, V5_SET} <= set(names)
    for name in names:
        assert (root / name / MEMBER).is_file(), f"{name} supplies no {MEMBER}"
    # And nothing in the prompts package is code that could serve the role.
    assert list(root.parent.rglob("*.py")) == []


@pytest.mark.parametrize("default_set", [OPUS_SET, V5_SET])
def test_the_template_renders_with_the_steps_own_bindings(default_set: str) -> None:
    """The three names the step binds are the three the member references."""
    rendered = render_case(operation_registry(default_set=default_set), MEMBER[:-3])

    assert "{{" not in rendered
    assert "<issue_key>external/42</issue_key>" in rendered
    assert "<pinned_answers>" in rendered
    assert "<task_md>" in rendered
