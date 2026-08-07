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
