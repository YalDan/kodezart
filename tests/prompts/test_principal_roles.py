"""Three routes, three roles, and two identifiers per principal.

The passes route three things differently — the approval flip and its
assignment target, the word that creates a reply obligation, and the
out-of-band escalation that creates none — so a two-valued enum collapses
two of them into one and the routing becomes unexpressible.  The tests
here fail if a member is removed, and fail if the identifier a mention is
recognised by stops reaching a rendered pass.

Every assertion runs over the shipped example config, the shipped bindings
and the shipped templates.  Nothing here constructs a principal the loader
would not accept.
"""

from pathlib import Path

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.errors import OperationConfigError
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.types.domain.operation import OperationConfig, PrincipalRole
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.test_prompt_wiring import load_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "docs" / "operation.example.toml"

#: Each role and the routing that would have nowhere to go without it.
ROUTES: dict[PrincipalRole, str] = {
    PrincipalRole.APPROVER: "the approval flip and the assignment target",
    PrincipalRole.PRINCIPAL: "a word that creates a reply obligation",
    PrincipalRole.ESCALATION: "an out-of-band escalation creating no obligation",
}


def example_config() -> OperationConfig:
    return load_operation_config(EXAMPLE)


def test_the_three_routes_are_three_distinct_roles() -> None:
    """AC-39: the enum carries one member per route the passes take."""
    assert set(PrincipalRole) == set(ROUTES)
    assert len({member.value for member in PrincipalRole}) == len(ROUTES)


def test_the_shipped_example_declares_a_principal_in_every_role() -> None:
    """A role no instance can hold is a member nothing exercises."""
    declared = {principal.role for principal in example_config().principals}

    assert declared == set(PrincipalRole)


def test_a_principal_carries_the_identifier_a_mention_is_recognised_by() -> None:
    """AC-39: authority identifier and recognition identifier are two fields.

    The mention sweep is text matching. A model carrying only the
    identifier authority is checked against gives that sweep nothing to
    match on, which is the defect this closes.
    """
    config = example_config()

    for principal in config.principals:
        assert principal.handle
        assert principal.handle != principal.tracker_user
    assert config.approver().handle


def test_the_recognition_identifier_reaches_a_rendered_pass() -> None:
    """The sweep is renderable, not merely representable."""
    config = example_config()
    registry = load_registry()
    rendered = registry.template_for(PromptKey.FIRE_PREP_PASS).render(
        {**operation_bindings(config), "skills_reference": ""},
    )

    obliging = [
        principal
        for principal in config.principals
        if principal.role is PrincipalRole.PRINCIPAL
    ]
    assert obliging
    for principal in obliging:
        assert principal.handle in rendered
        assert principal.tracker_user in rendered


def test_a_second_approver_is_refused_rather_than_silently_ranked(
    tmp_path: Path,
) -> None:
    """Exactly one principal holds the approval flip; two is a loud failure."""
    body = EXAMPLE.read_text(encoding="utf-8") + (
        "\n[[principals]]\n"
        'tracker_user = "second"\n'
        'role = "approver"\n'
        'handle = "@second"\n'
    )
    path = tmp_path / "operation.toml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(OperationConfigError) as caught:
        load_operation_config(path)
    assert any("approver" in failure.lower() for failure in caught.value.failures)


def test_an_escalation_principal_does_not_hold_the_approval_flip() -> None:
    """The third role is a role, not a relabelled principal."""
    config = example_config()
    escalation = [
        principal
        for principal in config.principals
        if principal.role is PrincipalRole.ESCALATION
    ]

    assert escalation
    for principal in escalation:
        assert principal.tracker_user != config.approver().tracker_user
        assert principal.handle != config.approver().handle
