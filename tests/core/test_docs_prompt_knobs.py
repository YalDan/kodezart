"""Documentation criteria for the prompt-set axis (KOD-63/AC-11)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")


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
