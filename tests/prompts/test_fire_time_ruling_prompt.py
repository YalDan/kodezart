"""The pre-loop question role is a data file in every shipped set (KOD-638).

A role is a key resolved through the provider; which template serves it is a
file in a set directory and never a Python module. Both halves are asserted
here: every shipped set supplies the member, and the prompts package holds no
code that could stand in for one.
"""

import pytest

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.core.prompt_rendering import free_binding_names
from kodezart.types.domain.agent import RulingAnswer
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.sets import OPUS_SET, V5_SET, operation_registry, render_case
from tests.prompts.test_set_completeness import shipped_sets

MEMBER = f"{PromptKey.FIRE_TIME_RULING.value}.md"

#: What the step binds the member with, by name.
BOUND = frozenset({"issue_key", "task_md", "pinned_rulings"})

#: Every field of one answer, under the name the session answers with.  Taken
#: from the model rather than listed, so a field added to it enters the census
#: the day it lands instead of the day someone remembers this file.
ANSWER_FIELDS = frozenset(
    field.alias or name for name, field in RulingAnswer.model_fields.items()
)

#: A field the member need not name, against the reason it need not.  Empty:
#: every field of an answer is a field the member asks for by name today.
UNASKED: dict[str, str] = {}


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
    registry = operation_registry(default_set=default_set)

    rendered = render_case(registry, MEMBER[:-3])

    # The member references each of them, so a tag left standing over a
    # dropped reference is not a rendering this row accepts.
    assert BOUND <= free_binding_names(
        registry.template_for(PromptKey.FIRE_TIME_RULING).body
    )
    assert "{{" not in rendered
    assert "<issue_key>external/42</issue_key>" in rendered
    assert "<pinned_answers>" in rendered
    assert "<task_md>" in rendered


def test_every_answer_field_is_asked_for_by_name_in_every_shipped_set() -> None:
    """The member's prose is the only thing that fills a session-declared field.

    Nothing downstream can derive `supersedesQuestion` or `deliverable`: the
    step reads what the session answered, so a field the member stops naming
    is a field that arrives empty for ever, with every other test green. A
    census rather than one assertion per field, because a census cannot go
    stale as the model grows.
    """
    root = default_sets_root()
    names = shipped_sets()

    # The census is total over the model: an alias collision would silently
    # shrink it, and an exemption for a field the model dropped is stale.
    assert len(ANSWER_FIELDS) == len(RulingAnswer.model_fields)
    assert set(UNASKED) <= ANSWER_FIELDS
    asked = ANSWER_FIELDS - set(UNASKED)
    assert asked

    assert {OPUS_SET, V5_SET} <= set(names)
    for name in names:
        body = (root / name / MEMBER).read_text(encoding="utf-8")
        # Named as the field it is, not merely mentioned in passing prose.
        unasked = sorted(field for field in asked if f"`{field}`" not in body)
        assert unasked == [], f"{name}/{MEMBER} asks for no {unasked}"
