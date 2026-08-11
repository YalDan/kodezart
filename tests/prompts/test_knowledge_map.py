"""The what-lives-where fragment: one registry key, granted sessions only.

Attaching the knowledge server gives a session the capability; this fragment
tells it what lives where.  It is a function-key PRELUDE rather than a
set-level fragment, so it enters the function-key census and leaves every
existing golden byte untouched.
"""

import re
from pathlib import Path
from typing import Final

import pytest

from kodezart.adapters.in_repo_prompt_registry import InRepoPromptRegistry
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.knowledge import boot_knowledge_grant
from kodezart.core.config import AppConfig
from kodezart.core.errors import PromptRenderError, PromptResolutionError
from kodezart.core.logging import get_logger
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.core.prompt_rendering import free_binding_names
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType
from tests.fakes import EXECUTOR_MODULES, knowledge_grant_for, recorded_session
from tests.prompts.test_prompt_wiring import load_registry, write_set

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SET_DIR: Final[Path] = (
    REPO_ROOT / "src" / "kodezart" / "prompts" / "sets" / "claude-opus"
)
EXAMPLE: Final[Path] = REPO_ROOT / "docs" / "operation.example.toml"
FRAGMENT: Final[Path] = SET_DIR / f"{PromptKey.KNOWLEDGE_MAP.value}.md"

#: The four content classes the parent locks, as the references that carry
#: their destinations.  Read off the shipped fragment is not an option: the
#: test would then be checking the fragment against itself.
CONTENT_CLASS_REFERENCES: Final[frozenset[str]] = frozenset(
    {
        "knowledge.run_logs",
        "knowledge.memories",
        "knowledge.personas",
        "knowledge.notes",
    }
)


def example_config() -> OperationConfig:
    """The shipped annotated example, loaded and structurally validated."""
    return load_operation_config(EXAMPLE)


def registry_for(config: OperationConfig) -> InRepoPromptRegistry:
    """The shipped set, bound to *config*'s namespace."""
    return load_registry(bindings=dict(bindings_for(config)))


def rendered_map(config: OperationConfig) -> str:
    """The fragment as a granted session receives it."""
    return registry_for(config).template_for(PromptKey.KNOWLEDGE_MAP).render({})


# ---------------------------------------------------------------------------
# KOD-84-AC-1 — the render, with every destination substituted
# ---------------------------------------------------------------------------


def test_the_fragment_renders_every_destination_from_configuration() -> None:
    """AC-1: four references, four configured values, no placeholder left."""
    config = example_config()

    rendered = rendered_map(config)

    for key in ("run_logs", "memories", "personas", "notes"):
        assert config.knowledge[key] in rendered, key
    assert "{{" not in rendered
    assert "}}" not in rendered


def test_the_fragment_references_exactly_the_four_content_classes() -> None:
    """The semantic content the parent locked, read off the template."""
    references = free_binding_names(FRAGMENT.read_text(encoding="utf-8"))

    assert references == CONTENT_CLASS_REFERENCES


def test_a_granted_session_receives_the_map_and_its_own_prompt() -> None:
    """The prelude precedes the caller's prompt, never replaces it."""
    config = example_config()
    rendered = rendered_map(config)

    composed = f"{rendered}\n\nthe caller's prompt"

    assert composed.startswith(rendered)
    assert composed.endswith("the caller's prompt")


# ---------------------------------------------------------------------------
# KOD-84-AC-3 — granted sessions only, and non-granted renderings unmoved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_a_granted_session_prompt_carries_the_map(module: str) -> None:
    """One grant decision, two consequences: the server AND the prelude."""
    grant = knowledge_grant_for(SessionType.TICKET_FIRE)

    session = await recorded_session(
        module,
        grant=grant,
        session_type=SessionType.TICKET_FIRE,
        prompt="the caller's prompt",
    )

    assert grant.knowledge_map in session.prompt
    assert session.prompt.endswith("the caller's prompt")
    assert session.options.mcp_servers is not None


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
@pytest.mark.parametrize(
    "session_type",
    [SessionType.API_QUERY, SessionType.COMMIT_MESSAGE, SessionType.CONTENT_AUDIT],
)
async def test_every_non_granted_session_prompt_is_byte_identical(
    module: str,
    session_type: SessionType,
) -> None:
    """AC-3: not a spot check — each type outside the grant, byte for byte."""
    grant = knowledge_grant_for(SessionType.TICKET_FIRE)

    session = await recorded_session(
        module,
        grant=grant,
        session_type=session_type,
        prompt="the caller's prompt",
    )

    assert session.prompt == "the caller's prompt"
    assert grant.knowledge_map not in session.prompt


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_the_shipped_empty_grant_preludes_no_session_at_all(
    module: str,
) -> None:
    """Exhaustive over the vocabulary: nothing granted, nothing preluded."""
    for session_type in SessionType:
        session = await recorded_session(
            module,
            session_type=session_type,
            prompt="the caller's prompt",
        )
        assert session.prompt == "the caller's prompt"


def test_the_grant_cannot_carry_a_server_without_a_map_or_the_reverse() -> None:
    """The two consequences are one value, so they cannot disagree."""
    with pytest.raises(ValueError, match="carries no knowledge map"):
        knowledge_grant_for(SessionType.TICKET_FIRE, knowledge_map="")

    with pytest.raises(ValueError, match="names no session type but carries"):
        AppConfig().knowledge_grant(knowledge_map="a map nothing renders")


# ---------------------------------------------------------------------------
# KOD-84-AC-3 (boot) + AC-4 — the render is the boot act
# ---------------------------------------------------------------------------


async def test_a_granted_boot_renders_the_map_into_the_resolved_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The map is resolved once, at boot, and rides the grant from there."""
    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", '["ticket_fire"]')
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_TOKEN", "ntn_" + ("M" * 44))
    config = example_config()

    grant = await boot_knowledge_grant(
        config=AppConfig(),
        prompts=registry_for(config),
        log=get_logger(__name__),
    )

    assert grant.granted == (SessionType.TICKET_FIRE,)
    assert grant.knowledge_map == rendered_map(config)


async def test_the_shipped_boot_renders_nothing_and_needs_no_references() -> None:
    """An operation declaring no knowledge references still boots."""
    bare = OperationConfig(operation_name="bare", workspace="bare-workspace")

    grant = await boot_knowledge_grant(
        config=AppConfig(),
        prompts=registry_for(bare),
        log=get_logger(__name__),
    )

    assert grant.granted == ()
    assert grant.knowledge_map == ""


@pytest.mark.parametrize(
    "missing",
    ["run_logs", "memories", "personas", "notes"],
)
async def test_a_missing_destination_aborts_a_granted_boot_naming_it(
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    """AC-4: the unresolvable reference is named at startup, not in a prompt."""
    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", '["ticket_fire"]')
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_TOKEN", "ntn_" + ("N" * 44))
    config = example_config()
    incomplete = config.model_copy(
        update={
            "knowledge": {k: v for k, v in config.knowledge.items() if k != missing}
        },
    )

    with pytest.raises(PromptRenderError) as excinfo:
        await boot_knowledge_grant(
            config=AppConfig(),
            prompts=registry_for(incomplete),
            log=get_logger(__name__),
        )

    assert f"knowledge.{missing}" in str(excinfo.value)


async def test_every_missing_destination_is_named_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One error listing all four, so an operator fixes them in one pass."""
    monkeypatch.setenv("KODEZART_KNOWLEDGE_SESSION_GRANTS", '["ticket_fire"]')
    monkeypatch.setenv("KODEZART_KNOWLEDGE_MCP_TOKEN", "ntn_" + ("O" * 44))
    config = example_config()
    stripped = config.model_copy(update={"knowledge": {}})

    with pytest.raises(PromptRenderError) as excinfo:
        await boot_knowledge_grant(
            config=AppConfig(),
            prompts=registry_for(stripped),
            log=get_logger(__name__),
        )

    reported = str(excinfo.value)
    for reference in CONTENT_CLASS_REFERENCES:
        assert reference in reported


# ---------------------------------------------------------------------------
# KOD-84-AC-4 — template hygiene: destinations render, prose never carries them
# ---------------------------------------------------------------------------


def test_no_configured_identity_or_destination_string_lives_in_the_fragment() -> None:
    """The template source names roles; configuration names instances."""
    source = FRAGMENT.read_text(encoding="utf-8")
    config = example_config()

    configured: list[str] = [
        config.operation_name,
        config.workspace,
        *config.knowledge.values(),
        *config.endpoints.values(),
        *(entry.name for entry in config.records.values()),
        *(entry.id for entry in config.records.values()),
        *(entry.name for entry in config.documents.values()),
        *(entry.id or "" for entry in config.documents.values()),
        *(principal.handle for principal in config.principals),
        *(principal.tracker_user for principal in config.principals),
    ]

    for value in configured:
        assert value and value not in source, value


def test_the_fragment_carries_no_org_shaped_literal() -> None:
    """No endpoint, no handle, no date — the same shapes the passes forbid."""
    source = FRAGMENT.read_text(encoding="utf-8")

    assert re.search(r"https?://", source) is None
    assert re.search(r"(?<![\w/>])@[A-Za-z][\w.-]{2,}", source) is None
    assert re.search(r"\b\d{4}-\d{2}-\d{2}\b", source) is None


# ---------------------------------------------------------------------------
# KOD-84-AC-5 — the key enters the function-key census
# ---------------------------------------------------------------------------


def test_the_key_is_present_in_the_default_set() -> None:
    """A key, not set metadata — which is what puts it in the census."""
    registry = load_registry()

    assert registry.resolution_table()[PromptKey.KNOWLEDGE_MAP] == "claude-opus"
    assert PromptKey.KNOWLEDGE_MAP.value in {path.stem for path in SET_DIR.glob("*.md")}


def test_removing_the_fragment_from_a_set_trips_the_completeness_check(
    tmp_path: Path,
) -> None:
    """AC-5: the set-completeness rule now covers this key like every other."""
    members = {
        key.value: f"body:{key.value}"
        for key in PromptKey
        if key is not PromptKey.KNOWLEDGE_MAP
    }
    write_set(tmp_path, "incomplete", members)

    with pytest.raises(PromptResolutionError) as excinfo:
        load_registry(sets_root=tmp_path, default_set="incomplete")

    assert PromptKey.KNOWLEDGE_MAP.value in excinfo.value.failing_keys


# ---------------------------------------------------------------------------
# KOD-84-AC-6 — a fifth content class costs configuration, never code
# ---------------------------------------------------------------------------


def test_a_fifth_content_class_renders_through_the_same_path(
    tmp_path: Path,
) -> None:
    """AC-6: one more reference, one more line, no code change anywhere."""
    config = example_config()
    extended = config.model_copy(
        update={"knowledge": {**config.knowledge, "incident_reports": "Fixture — IR"}},
    )
    members = {key.value: f"body:{key.value}" for key in PromptKey}
    members[PromptKey.KNOWLEDGE_MAP.value] = (
        FRAGMENT.read_text(encoding="utf-8").rstrip("\n")
        + "\n- incident reports — {{knowledge.incident_reports}}"
    )
    write_set(tmp_path, "extended", members)

    registry = load_registry(
        sets_root=tmp_path,
        default_set="extended",
        bindings=dict(bindings_for(extended)),
    )
    rendered = registry.template_for(PromptKey.KNOWLEDGE_MAP).render({})

    assert "Fixture — IR" in rendered
    assert extended.knowledge["run_logs"] in rendered
    assert "{{" not in rendered
