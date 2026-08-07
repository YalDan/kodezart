"""Boot-time prompt validation and observability (KOD-63/AC-7b, AC-8, AC-10)."""

import json
from pathlib import Path

import pytest

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.core.config import AppConfig
from kodezart.core.errors import PromptResolutionError
from kodezart.main import create_app, lifespan
from kodezart.types.domain.prompts import PromptKey


def _emitted(captured: str) -> list[dict[str, object]]:
    """Parse the JSON log lines the app emitted during boot."""
    events: list[dict[str, object]] = []
    for line in captured.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        events.append(json.loads(stripped))
    return events


async def test_lifespan_logs_one_prompt_resolution_table_event(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC-8: a SINGLE structured event carries the whole key -> source table."""
    app = create_app()
    async with lifespan(app):
        pass

    events = _emitted(capsys.readouterr().out)
    table_events = [e for e in events if e.get("event") == "prompt_resolution_table"]
    assert len(table_events) == 1
    table = table_events[0]["table"]
    assert isinstance(table, dict)
    assert set(table) == {key.value for key in PromptKey}
    assert set(table.values()) == {"claude-opus"}


async def test_lifespan_logs_the_engine_mismatch_note_informationally(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC-10: a set/engine mismatch is an informational note; resolution proceeds."""
    app = create_app()
    async with lifespan(app):
        pass

    events = _emitted(capsys.readouterr().out)
    mismatches = [e for e in events if e.get("event") == "prompt_set_engine_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0]["level"] == "info"
    assert mismatches[0]["declared_engines"] == ["claude-opus"]
    # Resolution proceeded unchanged despite the note.
    assert any(e.get("event") == "prompt_resolution_table" for e in events)
    assert app.state.workflow_engine is not None


async def test_boot_fails_loudly_when_a_configured_override_is_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC-7b: unresolvable keys are listed in a typed boot error, never defaulted."""
    monkeypatch.setenv(
        "KODEZART_PROMPT_TEMPLATE_OVERRIDES",
        f'{{"fix": "{tmp_path / "absent.md"}"}}',
    )
    app = create_app()
    with pytest.raises(PromptResolutionError) as excinfo:
        async with lifespan(app):
            pass
    assert PromptKey.FIX.value in excinfo.value.failing_keys


def test_default_config_points_at_the_in_repo_set() -> None:
    """The shipped default resolves 100% claude-opus."""
    config = AppConfig()
    assert config.prompt_set == "claude-opus"
    assert (default_sets_root() / config.prompt_set / "set.toml").is_file()
