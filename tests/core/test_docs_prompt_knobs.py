"""Documentation criteria for the prompt-set axis (KOD-63/AC-11)."""

from pathlib import Path

import pytest

from kodezart.composition.gating import build_outbound_gate
from kodezart.core.config import AppConfig
from kodezart.core.logging import get_logger
from kodezart.types.domain.gating import (
    ContentClass,
    GateVerdict,
    OutboundDestination,
    RedactionCategory,
    RepoVisibility,
    WriterShape,
)
from tests.adapters.test_judgment_scanner import ScriptedAuditExecutor, audit_result
from tests.fakes import SUPPRESS_ALL_SKILLS
from tests.prompts.test_prompt_wiring import load_registry

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
        "KODEZART_AGENT__MODEL",
    ):
        assert name in ENV_EXAMPLE


def test_readme_documents_the_prompt_set_axis() -> None:
    """README names both override mappings and the separate model axis."""
    for name in (
        "KODEZART_PROMPT_SET",
        "KODEZART_PROMPT_SET_OVERRIDES",
        "KODEZART_PROMPT_TEMPLATE_OVERRIDES",
        "KODEZART_AGENT__MODEL",
    ):
        assert name in README


def test_readme_points_at_the_relocated_prompt_layout() -> None:
    """Both prompt-path references were updated to the sets layout."""
    assert "src/kodezart/prompts/sets/" in README
    assert "`src/kodezart/prompts/`" not in README


def test_env_example_documents_every_skills_knob() -> None:
    """AC-4: both fields, the setting-sources field, and the host home dir."""
    for name in (
        "KODEZART_AGENT__SKILLS__MODE",
        "KODEZART_AGENT__SKILLS__ALLOWLIST",
        "KODEZART_AGENT__SETTING_SOURCES",
        "KODEZART_AGENT__HOME_DIR",
    ):
        assert name in ENV_EXAMPLE


def test_readme_documents_the_skills_model() -> None:
    """AC-4: three-state semantics, the suppress-all default and its rationale."""
    for name in (
        "KODEZART_AGENT__SKILLS__MODE",
        "KODEZART_AGENT__SKILLS__ALLOWLIST",
        "KODEZART_AGENT__SETTING_SOURCES",
    ):
        assert name in README
    assert "Shipped default" in README
    assert "host-provisioned at user scope" in README
    assert "target repository's own `.claude/`" in README


def test_env_example_keeps_fixed_admission_policy_out_of_configuration() -> None:
    """Credential shapes and privacy severity are shipped policy."""
    assert "KODEZART_DENY_PATTERNS" not in ENV_EXAMPLE
    assert "KODEZART_DENY_PATTERN_VERDICTS" not in ENV_EXAMPLE
    assert "KODEZART_AGENTIC_CONTENT_SCANNER_ENABLED" in ENV_EXAMPLE
    assert "fixed privacy policy has six rows" in README


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

    assert config.agent.model is None
    assert config.operation_config is None


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
    gate = await build_outbound_gate(
        config=config,
        operation=None,
        executor=ScriptedAuditExecutor([audit_result([])]),
        prompts=load_registry(),
        skills=SUPPRESS_ALL_SKILLS,
        log=get_logger(__name__),
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
