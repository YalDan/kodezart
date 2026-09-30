"""The amendment judge and the writer contract are data files bound by name.

KOD-660 and KOD-668.

Each role is a key resolved through the provider; the member that serves it is a
file in a set directory and never a Python module. Both halves are asserted here:
every shipped set supplies each member, and each member references exactly the
names the harness binds — no more, so no field can carry a claimant's reasoning or
session transcript, and no fewer, so a tag left standing over a dropped reference
is not a rendering.
"""

import re

import pytest

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.core.prompt_rendering import free_binding_names
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.sets import ALL_CASES, OPUS_SET, V5_SET, operation_registry
from tests.prompts.test_set_completeness import shipped_sets

#: The exact clause every shipped writer contract must state, whitespace-normalized
#: because the member wraps its own lines.
DESIGNATION_CLAUSE = (
    "A change to a designated protected test is itself such a departure, "
    "addressed to the pinned record that designates it."
)

#: Per role: how the rendered block delimits its value, then every tag the member
#: carries paired with the bound name rendered inside it, in the member's own
#: order, and the case that binds them. The bound set and the tag census are
#: derived from those pairs rather than restated.
ROLES: dict[PromptKey, tuple[str, tuple[tuple[str, str], ...], dict[str, object]]] = {
    PromptKey.AMENDMENT_JUDGE: (
        "<{tag}>{value}</{tag}>",
        (
            ("claim", "claim"),
            ("current_criteria", "criteria"),
            ("pinned_rulings", "pinned_rulings"),
            ("base_sha", "base_sha"),
        ),
        ALL_CASES["amendment_judge"][1],
    ),
    PromptKey.NATIVE_WRITER_CONTRACT: (
        "<{tag}>\n{value}\n</{tag}>",
        (("pinned_rulings", "pinned_rulings"),),
        ALL_CASES["native_writer_contract"][1],
    ),
}


def test_each_role_is_a_data_file_in_every_shipped_set() -> None:
    """Derived from the sets root, so a third set is covered the day it lands."""
    root = default_sets_root()
    names = shipped_sets()

    assert {OPUS_SET, V5_SET} <= set(names)
    for key in ROLES:
        member = f"{key.value}.md"
        for name in names:
            assert (root / name / member).is_file(), f"{name} supplies no {member}"
    # And nothing in the prompts package is code that could serve a role.
    assert list(root.parent.rglob("*.py")) == []


def test_every_shipped_set_states_the_designated_test_convention() -> None:
    """Derived over the sets root, so a third set is covered the day it lands.

    The convention the precommit read enforces is also stated to the writer: a
    change to a designated protected test is a departure addressed to the pinned
    record that designates it, not a change the writer may simply make.
    """
    names = shipped_sets()
    assert names
    for name in names:
        body = (
            operation_registry(default_set=name)
            .template_for(PromptKey.NATIVE_WRITER_CONTRACT)
            .body
        )
        assert DESIGNATION_CLAUSE in " ".join(body.split())


@pytest.mark.parametrize("default_set", [OPUS_SET, V5_SET])
@pytest.mark.parametrize("key", list(ROLES))
def test_the_template_binds_exactly_the_harness_names(
    key: PromptKey, default_set: str
) -> None:
    """Equality, not containment: the bound names are a closed set per role."""
    block, pairs, case = ROLES[key]
    registry = operation_registry(default_set=default_set)
    body = registry.template_for(key).body

    assert free_binding_names(body) == {name for _, name in pairs} | {
        "skills_reference"
    }
    # The census is taken over the member itself, where no bound value can
    # contribute a tag of its own.
    assert tuple(re.findall(r"<(\w+)>", body)) == tuple(tag for tag, _ in pairs)

    rendered = registry.template_for(key).render({**case, "skills_reference": ""})

    assert "{{" not in rendered
    for tag, name in pairs:
        assert block.format(tag=tag, value=case[name]) in rendered
