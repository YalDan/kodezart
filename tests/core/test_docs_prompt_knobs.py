"""Documentation criteria for the prompt-set axis (KOD-63/AC-11)."""

from pathlib import Path

import pytest

from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.core.config import AppConfig
from kodezart.types.domain.gating import (
    ContentClass,
    GateVerdict,
    OutboundDestination,
    RedactionCategory,
    RepoVisibility,
    WriterShape,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
ENV_EXAMPLE = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

# A clone URL carrying a credential — the payload the credential category
# exists to stop, used here to prove the shipped category survives a
# `cp .env.example .env`.
TOKEN_BEARING_URL = (
    "git clone https://x-access-token:ghp_"
    + "A" * 36
    + "@example.invalid/owner/repo.git"
)


def config_from_env_example() -> AppConfig:
    """AppConfig exactly as `cp .env.example .env` would produce it."""
    return AppConfig(_env_file=ENV_EXAMPLE_PATH)


def test_env_example_documents_every_prompt_knob() -> None:
    """Both override mappings, the set knob, and the model axis are present."""
    for name in (
        "KODEZART_PROMPT_SET",
        "KODEZART_PROMPT_SET_OVERRIDES",
        "KODEZART_PROMPT_TEMPLATE_OVERRIDES",
        "KODEZART_MODEL",
    ):
        assert name in ENV_EXAMPLE


def test_readme_documents_the_prompt_set_axis() -> None:
    """README names both override mappings and the separate model axis."""
    for name in (
        "KODEZART_PROMPT_SET",
        "KODEZART_PROMPT_SET_OVERRIDES",
        "KODEZART_PROMPT_TEMPLATE_OVERRIDES",
        "KODEZART_MODEL",
    ):
        assert name in README


def test_readme_points_at_the_relocated_prompt_layout() -> None:
    """Both prompt-path references were updated to the sets layout."""
    assert "src/kodezart/prompts/sets/" in README
    assert "`src/kodezart/prompts/`" not in README


def test_env_example_documents_every_skills_knob() -> None:
    """AC-4: both fields, the setting-sources field, and the host home dir."""
    for name in (
        "KODEZART_SKILLS_MODE",
        "KODEZART_SKILLS_ALLOWLIST",
        "KODEZART_SETTING_SOURCES",
        "KODEZART_CLAUDE_HOME_DIR",
    ):
        assert name in ENV_EXAMPLE


def test_readme_documents_the_skills_model() -> None:
    """AC-4: three-state semantics, the suppress-all default and its rationale."""
    for name in (
        "KODEZART_SKILLS_MODE",
        "KODEZART_SKILLS_ALLOWLIST",
        "KODEZART_SETTING_SOURCES",
    ):
        assert name in README
    assert "Shipped default" in README
    assert "host-provisioned at user scope" in README
    assert "target repository's own `.claude/`" in README


def test_env_example_documents_the_gate_knobs() -> None:
    """AC-9: every pattern set and verdict mapping originates in AppConfig."""
    assert "KODEZART_DENY_PATTERNS" in ENV_EXAMPLE
    assert "KODEZART_DENY_PATTERN_VERDICTS" in ENV_EXAMPLE


def test_readme_documents_the_three_verdicts_and_fail_closed_rule() -> None:
    """AC-7/AC-8: the verdicts and the no-exemption rule are documented."""
    for token in ("`clean`", "`redacted`", "`blocked`"):
        assert token in README
    assert "fail-closed with no exemption" in README
    assert "OutboundContentBlockedError" in README


def test_env_example_documents_the_operation_config_pointer() -> None:
    """D-2: exactly one pointer, documented alongside the rest."""
    assert "KODEZART_OPERATION_CONFIG" in ENV_EXAMPLE


# ---------------------------------------------------------------------------
# AC-12 — the example file must CONSTRUCT, not merely mention, the defaults
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_pristine_environment")
def test_env_example_constructs_the_shipped_defaults() -> None:
    """The three values AC-12 names, read off a config built from the file.

    `str | None` fields take their real default only when the entry is
    ABSENT: an empty assignment binds `''`, which is a different value with
    different behaviour.
    """
    config = config_from_env_example()

    assert config.model is None
    assert config.operation_config is None
    assert config.deny_patterns[RedactionCategory.CREDENTIALS] != []


@pytest.mark.usefixtures("_pristine_environment")
def test_env_example_is_indistinguishable_from_shipping_no_env_file_at_all() -> None:
    """The general guard: every entry documents its own default, or is absent.

    Field-by-field equality, so the next knob added to the file cannot
    reintroduce the class of drift AC-12 caught without failing here.
    """
    assert config_from_env_example() == AppConfig(_env_file=None)


@pytest.mark.usefixtures("_pristine_environment")
async def test_credential_gating_survives_a_copy_of_the_example_file() -> None:
    """The concrete leak: a token-bearing URL on a PUBLIC target is blocked."""
    config = config_from_env_example()
    gate = PatternOutboundContentGate(
        scanners=[RegexContentScanner(patterns=config.deny_patterns)],
        verdicts=config.deny_pattern_verdicts,
    )

    decision = await gate.gate(
        content=TOKEN_BEARING_URL,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )

    assert decision.verdict is GateVerdict.BLOCKED
    assert RedactionCategory.CREDENTIALS in decision.categories
