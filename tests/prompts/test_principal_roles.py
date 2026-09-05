"""Three routes, a SET of roles per principal, and two identifiers each.

The passes route three things differently — the approval flip, the word
that creates a reply obligation, and the target prepared fires and triage
filings are assigned to — so a two-valued enum collapses two of them and
the routing becomes unexpressible.  ``roles`` is set-valued because one
principal demonstrably holds two of the three at once, which a singular
field can only express as a duplicate entry or a lost routing.

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
from tests.prompts.sets import PER_RUN
from tests.prompts.test_prompt_wiring import load_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "docs" / "operation.example.toml"

#: Each role and the routing that would have nowhere to go without it.
ROUTES: dict[PrincipalRole, str] = {
    PrincipalRole.APPROVER: "the approval flip",
    PrincipalRole.PRINCIPAL: "a word that creates a reply obligation",
    PrincipalRole.ASSIGNEE: "the target a prepared fire is assigned to",
}


def example_config() -> OperationConfig:
    return load_operation_config(EXAMPLE)


def test_the_three_routes_are_three_distinct_roles() -> None:
    """AC-39: the enum carries one member per route the passes take."""
    assert set(PrincipalRole) == set(ROUTES)
    assert len({member.value for member in PrincipalRole}) == len(ROUTES)


def test_the_shipped_example_declares_a_principal_in_every_role() -> None:
    """A role no instance can hold is a member nothing exercises."""
    declared = {
        role for principal in example_config().principals for role in principal.roles
    }

    assert declared == set(PrincipalRole)


def test_one_principal_holds_two_roles_at_once() -> None:
    """The set is load-bearing rather than a container around one value.

    This is the case a singular ``role`` cannot express: the same principal
    holds the approval act AND is what prepared work is assigned to.  With
    a singular field the config must either duplicate the person or drop
    one of the two routings, and both are silent.
    """
    config = example_config()
    approver = config.approver()

    assert PrincipalRole.ASSIGNEE in approver.roles
    assert len(approver.roles) > 1
    assert [
        principal
        for principal in config.principals
        if principal.tracker_user == approver.tracker_user
    ] == [approver]


def test_every_principal_carries_the_principal_role() -> None:
    """``principal`` is the floor: authority is added to it, never instead."""
    for principal in example_config().principals:
        assert PrincipalRole.PRINCIPAL in principal.roles


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


def test_a_principal_is_recognisable_on_the_forge_as_well_as_the_tracker() -> None:
    """One principal, two surfaces, two identifiers — and three states.

    Review-borne mentions are answered on the forge, so a principal whose
    forge name differs from their tracker name is unrecognisable there
    without a second identifier.  ``None`` is a real state — a principal
    who never appears on the forge legitimately has none — so the example
    carries both, and neither is inferred from the other.
    """
    config = example_config()
    named = [p for p in config.principals if p.forge_handle is not None]
    absent = [p for p in config.principals if p.forge_handle is None]

    assert named
    assert absent
    for principal in named:
        assert principal.forge_handle != principal.handle
        assert principal.forge_handle != principal.tracker_user


def test_both_identifiers_reach_a_rendered_pass() -> None:
    """Recognition and authority both reach the sweep, as the routine states.

    Reshaped to the verbatim template under KOD-60 R20(b): the routine's
    roster carries every principal's authority identifier and role word,
    the mention sweep recognises the ACCOUNT by its own identities, and the
    forge handle's promised reader — the clause naming one principal two
    ways across two surfaces — is restored with the verbatim text.
    """
    config = example_config()
    registry = load_registry()
    rendered = registry.template_for(PromptKey.FIRE_PREP_PASS).render(
        {**operation_bindings(config), **PER_RUN, "skills_reference": ""},
    )

    obliging = [
        principal
        for principal in config.principals
        if PrincipalRole.PRINCIPAL in principal.roles
    ]
    assert obliging
    for principal in obliging:
        assert principal.tracker_user in rendered
    for identity in config.agent_identities:
        assert identity in rendered
    approver = config.approver()
    assert approver.forge_handle is not None
    assert approver.forge_handle in rendered


def test_a_second_approver_is_refused_rather_than_silently_ranked(
    tmp_path: Path,
) -> None:
    """Exactly one principal holds the approval flip; two is a loud failure."""
    body = EXAMPLE.read_text(encoding="utf-8") + (
        "\n[[principals]]\n"
        'tracker_user = "second"\n'
        'roles = ["approver", "principal"]\n'
        'handle = "@second"\n'
    )
    path = tmp_path / "operation.toml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(OperationConfigError) as caught:
        load_operation_config(path)
    assert any("approver" in failure.lower() for failure in caught.value.failures)


def test_a_second_assignee_is_refused_rather_than_silently_ranked(
    tmp_path: Path,
) -> None:
    """The assignment target is singular for the same reason the flip is.

    Two assignees is not a wider grant, it is an ambiguous one: a prepared
    fire has one target, and a config declaring two makes the pass pick.
    """
    body = EXAMPLE.read_text(encoding="utf-8") + (
        "\n[[principals]]\n"
        'tracker_user = "second"\n'
        'roles = ["assignee", "principal"]\n'
        'handle = "@second"\n'
    )
    path = tmp_path / "operation.toml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(OperationConfigError) as caught:
        load_operation_config(path)
    assert any("assignee" in failure.lower() for failure in caught.value.failures)


def test_a_principal_without_the_principal_role_is_refused(tmp_path: Path) -> None:
    """Authority without the floor is an entry the mention sweep cannot use."""
    body = EXAMPLE.read_text(encoding="utf-8") + (
        "\n[[principals]]\n"
        'tracker_user = "roleless"\n'
        "roles = []\n"
        'handle = "@roleless"\n'
    )
    path = tmp_path / "operation.toml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(OperationConfigError) as caught:
        load_operation_config(path)
    assert any("principal role" in failure.lower() for failure in caught.value.failures)
