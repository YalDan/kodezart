"""The tracker credential on ``AppConfig`` — sourcing and hygiene.

One field, four properties, each asserted rather than promised: it is
sourced from the environment, it is absent from both serializations, its
``repr`` never renders the value, and no boot log line carries it.

The assertion set is the knowledge credential's, applied to this field
(KOD-130 AC-1), plus ``repr`` — which is what ``SecretStr`` adds over
exclusion alone.
"""

import json
from typing import Final

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig
from kodezart.main import create_app, lifespan

#: Built by concatenation: binding a ``lin_api_...`` literal to a name
#: would trip ruff S105 (hardcoded-password-string), which is active over
#: ``tests/**``.  The body is long enough to be a realistic value and is
#: distinctive enough that substring searches over log output mean
#: something.
_FIXTURE_BODY: Final[str] = "Q7" * 24
_FIXTURE_CREDENTIAL: Final[str] = "lin_api_" + _FIXTURE_BODY

_ENV_VAR: Final[str] = "KODEZART_TRACKER_TOKEN"


def test_the_credential_resolves_from_its_kodezart_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The field is env-sourced under the shared prefix."""
    monkeypatch.setenv(_ENV_VAR, _FIXTURE_CREDENTIAL)

    token = AppConfig().tracker_token
    assert token is not None
    assert token.get_secret_value() == _FIXTURE_CREDENTIAL


def test_an_unset_environment_yields_none_not_a_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absence is ``None`` — never an empty string standing in for a value."""
    monkeypatch.delenv(_ENV_VAR, raising=False)

    assert AppConfig().tracker_token is None


def test_an_unknown_sibling_key_still_trips_extra_forbid() -> None:
    """Adding the field left the typo guard in force."""
    with pytest.raises(ValidationError):
        AppConfig(tracker_tokn=_FIXTURE_CREDENTIAL)  # type: ignore[call-arg]


def test_neither_serialization_carries_the_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``model_dump()`` and ``model_dump_json()`` contain zero occurrences."""
    monkeypatch.setenv(_ENV_VAR, _FIXTURE_CREDENTIAL)
    config = AppConfig()

    assert _FIXTURE_CREDENTIAL not in json.dumps(config.model_dump(mode="json"))
    assert _FIXTURE_CREDENTIAL not in config.model_dump_json()
    assert _FIXTURE_BODY not in str(config.model_dump())


def test_repr_masks_the_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``repr`` and ``str`` render the mask, never the value.

    Exclusion governs serialization only — a ``repr`` lands in tracebacks
    and debug logs without ever passing through a dump, which is why the
    field is a ``SecretStr`` and not a plain excluded ``str``.
    """
    monkeypatch.setenv(_ENV_VAR, _FIXTURE_CREDENTIAL)
    config = AppConfig()

    assert _FIXTURE_BODY not in repr(config)
    assert _FIXTURE_BODY not in str(config)
    assert _FIXTURE_BODY not in repr(config.tracker_token)
    assert _FIXTURE_BODY not in str(config.tracker_token)


def test_the_field_is_still_readable_after_being_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exclusion governs serialization only; the value stays reachable.

    Without this, the serialization assertions above would also pass over
    a field that never loaded at all.
    """
    monkeypatch.setenv(_ENV_VAR, _FIXTURE_CREDENTIAL)

    token = AppConfig().tracker_token
    assert token is not None
    assert token.get_secret_value() == _FIXTURE_CREDENTIAL


async def test_no_boot_log_line_carries_the_credential(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Structlog output across ``create_app()`` + ``lifespan()`` is clean."""
    monkeypatch.setenv(_ENV_VAR, _FIXTURE_CREDENTIAL)

    app = create_app()
    async with lifespan(app):
        pass

    captured = capsys.readouterr()
    emitted = captured.out + captured.err

    # The capture is only evidence if boot actually emitted its log lines.
    assert '"event"' in emitted
    assert _FIXTURE_CREDENTIAL not in emitted
    assert _FIXTURE_BODY not in emitted
