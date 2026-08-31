"""The knowledge-server grant as configuration: vocabulary and boot rules.

Three boot outcomes are asserted here, and they must coexist: an entry
naming no session type, a grant with no credential, and the shipped empty
grant that boots clean. Each is a different rule, and none may mask
another.
"""

from typing import Final

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig
from kodezart.main import create_app, lifespan
from kodezart.types.domain.session import SessionType

_CREDENTIAL: Final[str] = "ntn_" + ("Q" * 44)
_GRANTS_VAR: Final[str] = "KODEZART_KNOWLEDGE_SESSION_GRANTS"
_TOKEN_VAR: Final[str] = "KODEZART_KNOWLEDGE_MCP_TOKEN"
_URL_VAR: Final[str] = "KODEZART_KNOWLEDGE_MCP_SERVER_URL"
_SELF_HOSTED_URL: Final[str] = "https://knowledge.invalid/mcp"
#: Any non-empty map: the model refuses a grant that names a session type
#: and carries none, so the builder has to be handed one.
_MAP: Final[str] = "── fixture map ──"


def test_the_shipped_grant_names_no_session_type() -> None:
    """The mechanism ships; the grant is operator configuration."""
    assert AppConfig().knowledge_session_grants == []
    assert AppConfig().knowledge_grant(knowledge_map="").granted == ()


def test_no_session_type_is_granted_by_the_shipped_default() -> None:
    """Exhaustive over the vocabulary, so a new member cannot ship granted."""
    grant = AppConfig().knowledge_grant(knowledge_map="")

    for session_type in SessionType:
        assert grant.grants(session_type) is False


def test_the_grant_list_resolves_from_its_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The intended first grant: ticket-driven fire sessions and nothing else."""
    monkeypatch.setenv(_GRANTS_VAR, '["ticket_fire"]')
    monkeypatch.setenv(_TOKEN_VAR, _CREDENTIAL)
    monkeypatch.setenv(_URL_VAR, _SELF_HOSTED_URL)

    grant = AppConfig().knowledge_grant(knowledge_map=_MAP)

    assert grant.granted == (SessionType.TICKET_FIRE,)
    assert grant.grants(SessionType.TICKET_FIRE) is True
    assert grant.grants(SessionType.API_QUERY) is False


def test_an_empty_grant_list_is_a_legal_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "No session gets it" is expressible, and needs no credential."""
    monkeypatch.setenv(_GRANTS_VAR, "[]")
    monkeypatch.delenv(_TOKEN_VAR, raising=False)

    assert AppConfig().knowledge_grant(knowledge_map="").granted == ()


def test_an_entry_naming_no_session_type_aborts_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never a silent no-grant: the offender and the legal values are named."""
    monkeypatch.setenv(_GRANTS_VAR, '["ticket_fire", "nightly_sweep"]')
    monkeypatch.setenv(_TOKEN_VAR, _CREDENTIAL)

    with pytest.raises(ValidationError) as excinfo:
        AppConfig()

    reported = str(excinfo.value)
    assert "nightly_sweep" in reported
    for session_type in SessionType:
        assert session_type.value in reported


def test_every_offending_entry_is_listed_not_just_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two typos are two errors — an operator fixes both in one pass."""
    monkeypatch.setenv(_GRANTS_VAR, '["nightly_sweep", "backfill"]')
    monkeypatch.setenv(_TOKEN_VAR, _CREDENTIAL)

    with pytest.raises(ValidationError) as excinfo:
        AppConfig()

    reported = str(excinfo.value)
    assert "nightly_sweep" in reported
    assert "backfill" in reported


def test_a_non_empty_grant_without_the_credential_aborts_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cross-field rule, naming the variable that is missing."""
    monkeypatch.setenv(_GRANTS_VAR, '["ticket_fire"]')
    monkeypatch.delenv(_TOKEN_VAR, raising=False)

    with pytest.raises(ValidationError) as excinfo:
        AppConfig()

    reported = str(excinfo.value)
    assert _TOKEN_VAR in reported
    assert SessionType.TICKET_FIRE.value in reported


def test_the_credential_rule_does_not_mask_the_vocabulary_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both defects at once still reports the unknown entry.

    The cross-field check runs after field validation, so a configuration
    that trips both must not surface only the later one.
    """
    monkeypatch.setenv(_GRANTS_VAR, '["nightly_sweep"]')
    monkeypatch.delenv(_TOKEN_VAR, raising=False)

    with pytest.raises(ValidationError) as excinfo:
        AppConfig()

    assert "nightly_sweep" in str(excinfo.value)


async def test_the_shipped_grant_boots_clean_with_no_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The empty grant with an unset credential is a working deployment."""
    monkeypatch.delenv(_TOKEN_VAR, raising=False)
    monkeypatch.delenv(_GRANTS_VAR, raising=False)

    app = create_app()
    async with lifespan(app):
        assert app.state.workflow_engine is not None
