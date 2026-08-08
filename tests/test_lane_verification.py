"""Lane-level verification for PR 5 (KOD-54).

The six integration checks no sub-issue owns alone.
"""

import subprocess
from pathlib import Path

import pytest

from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.config import AppConfig
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.types.domain.prompts import PromptKey

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDENS_DIR = REPO_ROOT / "tests" / "prompts" / "goldens"
EXAMPLE = REPO_ROOT / "docs" / "operation.example.toml"


def default_registry() -> InRepoPromptRegistry:
    """The registry exactly as the composition root builds it."""
    config = AppConfig()
    operation = load_operation_config(EXAMPLE)
    return InRepoPromptRegistry.load(
        sets_root=default_sets_root(),
        default_set=config.prompt_set,
        set_overrides=config.prompt_set_overrides,
        template_overrides=config.prompt_template_overrides,
        bindings=dict(bindings_for(operation)),
    )


# ---------------------------------------------------------------------------
# V-1 — registry x pass-template integration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS],
)
def test_pass_templates_resolve_by_key_under_default_configuration(
    key: PromptKey,
) -> None:
    """Both pass templates resolve through the port and render, end to end."""
    registry = default_registry()
    assert registry.resolution_table()[key] == AppConfig().prompt_set
    rendered = registry.template_for(key).render({})
    assert rendered
    assert "{{" not in rendered


def test_claude_opus_completeness_check_passes_at_fifteen_keys() -> None:
    """Loading succeeds only because the default set supplies every key."""
    registry = default_registry()
    table = registry.resolution_table()
    assert len(table) == 15
    assert set(table) == set(PromptKey)


# ---------------------------------------------------------------------------
# V-2 — no golden was ever re-baselined
# ---------------------------------------------------------------------------


def git(*args: str) -> str:
    """Run git in the repository, returning stdout."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


PROMPT_SETS_PATH = "src/kodezart/prompts/sets"


def test_no_golden_file_was_rewritten_without_its_template_moving() -> None:
    """V-2: a golden is re-baselined only alongside the prompt it renders.

    The shortcut this guards against is re-baselining a golden so a
    byte-identity assertion passes while the template it renders is
    untouched.  A golden with more than one commit is an offender unless
    its most recent commit also changed the prompt set — which is what a
    legitimate prompt edit looks like and what a dodge never does.
    """
    goldens = sorted(GOLDENS_DIR.rglob("*.txt"))
    assert goldens, "no golden files found"

    set_commits = set(
        git("log", "--format=%H", "--", PROMPT_SETS_PATH).split(),
    )
    offenders: list[str] = []
    for golden in goldens:
        relative = golden.relative_to(REPO_ROOT).as_posix()
        log = git("log", "--format=%H", "--", relative).split()
        if len(log) > 1 and log[0] not in set_commits:
            offenders.append(f"{relative}: re-baselined at {log[0][:8]}")
    assert offenders == [], f"goldens re-baselined with no template change: {offenders}"


def test_both_golden_suites_exist_and_cover_the_relocated_keys() -> None:
    """V-2: KOD-63's empty-fragment suite and KOD-46's populated suite."""
    empty = {p.stem for p in (GOLDENS_DIR / "claude_opus_empty_skills").glob("*.txt")}
    populated = {
        p.stem for p in (GOLDENS_DIR / "claude_opus_populated_skills").glob("*.txt")
    }
    assert empty
    assert empty == populated


# ---------------------------------------------------------------------------
# V-3 — the sanitization gate covers everything the lane added
# ---------------------------------------------------------------------------


def test_every_writer_in_the_corrected_inventory_names_the_gate() -> None:
    """V-3: the five-writer inventory routes through the gate."""
    workflow = (
        REPO_ROOT / "src" / "kodezart" / "chains" / "ralph_workflow.py"
    ).read_text(encoding="utf-8")
    persister = (
        REPO_ROOT / "src" / "kodezart" / "adapters" / "git_change_persister.py"
    ).read_text(encoding="utf-8")

    for writer in (
        'writer_name="branch_name"',
        'writer_name="artifact_ticket_json"',
        'writer_name="artifact_criteria_json"',
        'writer_name="pr_title"',
        'writer_name="pr_body"',
        'writer_name="pr_comment"',
    ):
        assert writer in workflow, f"{writer} does not route through the gate"

    assert '"commit_message"' in persister
    assert '"commit_message_divergence_replay"' in persister


# ---------------------------------------------------------------------------
# AC-1 — one rendering path, no substitution in the registry
# ---------------------------------------------------------------------------


def test_the_registry_adapter_performs_no_substitution() -> None:
    """AC-1: exactly one rendering entry point renders every template."""
    registry_source = (
        REPO_ROOT / "src" / "kodezart" / "adapters" / "in_repo_prompt_registry.py"
    ).read_text(encoding="utf-8")
    assert "{{" not in registry_source

    renderer_source = (
        REPO_ROOT / "src" / "kodezart" / "core" / "prompt_rendering.py"
    ).read_text(encoding="utf-8")
    assert "def render_template(" in renderer_source
    assert renderer_source.count("def render_template(") == 1


def test_no_second_substitution_implementation_exists_anywhere_in_src() -> None:
    """AC-1, second half: the renderer is unique across the whole package.

    The bullet asks for the absence of ANY second substitution implementation,
    which the single-file check above cannot see.  One definition, in one
    module, renders registry-set templates and ported pass templates alike.
    """
    definitions = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "src").rglob("*.py")
        if "def render_template(" in path.read_text(encoding="utf-8")
    )
    assert definitions == ["src/kodezart/core/prompt_rendering.py"]
