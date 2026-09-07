"""Boot-time prompt validation and observability (KOD-63/AC-7b, AC-8, AC-10).

KOD-93 moved the default set. The assertions that pinned the legacy set in
the DEFAULT position are relocated rather than deleted: each keeps its claim
by gaining an explicit-selection twin, which is the rollback path, while the
default-position assertion names the set the flip installed.
"""

import json
import tomllib
from pathlib import Path

import pytest

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.core.config import AppConfig
from kodezart.core.errors import PromptResolutionError
from kodezart.main import create_app, lifespan
from kodezart.types.domain.prompts import PromptKey

#: The set the flip installed, and the one it displaced. Both are read from
#: the shipped configuration rather than asserted as literals where the
#: default is the subject; the literals below are the claims themselves.
V5_SET = "anthropic_v5"
LEGACY_SET = "claude-opus"


def _emitted(captured: str) -> list[dict[str, object]]:
    """Parse the JSON log lines the app emitted during boot."""
    events: list[dict[str, object]] = []
    for line in captured.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        events.append(json.loads(stripped))
    return events


def _declared_engines(set_name: str) -> list[str]:
    """Engines *set_name* declares, read off the shipped set metadata."""
    with (default_sets_root() / set_name / "set.toml").open("rb") as handle:
        metadata: dict[str, object] = tomllib.load(handle)
    engines = metadata["engines"]
    assert isinstance(engines, list)
    return [str(engine) for engine in engines]


async def test_lifespan_logs_one_prompt_resolution_table_event(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC-8 + KOD-93-AC-7: ONE event, and by default it names the v5 set.

    The count claim is KOD-63's and unchanged; the value claim is the one
    KOD-93 moved, and it is what makes the live corpus readable from a
    single log line instead of inferred from which files happen to exist.
    """
    app = create_app()
    async with lifespan(app):
        pass

    events = _emitted(capsys.readouterr().out)
    table_events = [e for e in events if e.get("event") == "prompt_resolution_table"]
    assert len(table_events) == 1
    table = table_events[0]["table"]
    assert isinstance(table, dict)
    assert set(table) == {key.value for key in PromptKey}
    assert set(table.values()) == {V5_SET}


async def test_lifespan_logs_a_wholly_legacy_table_when_the_set_is_selected(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The displaced claim, kept: explicit selection still resolves 100% legacy.

    The twin of the test above. What the flip moved is which set answers by
    default, never whether the legacy set can answer at all.
    """
    monkeypatch.setenv("KODEZART_PROMPT_SET", LEGACY_SET)
    monkeypatch.setenv("KODEZART_TICKET_REVIEW_MODE", "reviewed")
    app = create_app()
    async with lifespan(app):
        pass

    events = _emitted(capsys.readouterr().out)
    table_events = [e for e in events if e.get("event") == "prompt_resolution_table"]
    assert len(table_events) == 1
    table = table_events[0]["table"]
    assert isinstance(table, dict)
    assert set(table) == {key.value for key in PromptKey}
    assert set(table.values()) == {LEGACY_SET}


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
    assert mismatches[0]["declared_engines"] == _declared_engines(V5_SET)
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


def test_default_config_points_at_an_in_repo_set() -> None:
    """The shipped default names a set that is actually in the tree.

    The default's VALUE is KOD-93's subject and is pinned by that issue's
    own criteria in the configuration suite; what stays here is the claim
    those criteria cannot make — that whatever the default names, the
    directory backing it ships.
    """
    config = AppConfig()
    assert (default_sets_root() / config.prompt_set / "set.toml").is_file()


def test_the_legacy_set_still_ships_and_is_still_selectable() -> None:
    """The displaced claim, kept: losing the default is not losing the set."""
    monkeypatched = AppConfig(prompt_set=LEGACY_SET)
    assert monkeypatched.prompt_set == LEGACY_SET
    assert (default_sets_root() / LEGACY_SET / "set.toml").is_file()
