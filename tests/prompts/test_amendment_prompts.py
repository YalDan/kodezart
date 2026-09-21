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
from kodezart.domain.rulings import EMPTY_REGISTRY
from kodezart.types.domain.agent import Ruling
from kodezart.types.domain.amendment import AmendmentClaim
from kodezart.types.domain.prompts import PromptKey
from tests.domain.test_rulings import ruling_data
from tests.fakes import make_tracker_issue
from tests.prompts.sets import OPUS_SET, V5_SET, operation_registry
from tests.prompts.test_set_completeness import shipped_sets

#: What the guard binds the judge member with (services/native_amendments.py:451-460).
CASE_JUDGE: dict[str, str] = {
    "claim": AmendmentClaim(
        subject={"kind": "criterion", "id": "external/check"},
        stage="implementation",
        ground="unsatisfiable_at_base",
        departure="A proposed departure",
        claimed_capability=None,
    ).model_dump_json(),
    "criteria": make_tracker_issue(
        "external/check",
        parent_key="external/42",
        issue_labels=frozenset({"criterion"}),
        body="**Check:** the observable fixture Check",
    ).model_dump_json(),
    "pinned_rulings": Ruling.model_validate(ruling_data()).model_dump_json(),
    "base_sha": "a" * 40,
}

#: Per role: how the rendered block delimits its value, then every tag the member
#: carries paired with the bound name rendered inside it, in the member's own
#: order, and the case that binds them. The bound set and the tag census are
#: derived from those pairs rather than restated.
ROLES: dict[PromptKey, tuple[str, tuple[tuple[str, str], ...], dict[str, str]]] = {
    PromptKey.AMENDMENT_JUDGE: (
        "<{tag}>{value}</{tag}>",
        (
            ("claim", "claim"),
            ("current_criteria", "criteria"),
            ("pinned_rulings", "pinned_rulings"),
            ("base_sha", "base_sha"),
        ),
        CASE_JUDGE,
    ),
    PromptKey.NATIVE_WRITER_CONTRACT: (
        "<{tag}>\n{value}\n</{tag}>",
        (("pinned_rulings", "pinned_rulings"),),
        {"pinned_rulings": EMPTY_REGISTRY},
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
