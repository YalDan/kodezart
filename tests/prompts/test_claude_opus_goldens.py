"""KOD-83 — the legacy corpus, frozen byte-identical.

Three guarantees the registry suite cannot give, because at KOD-63 there was
neither a second set nor a flipped default to test against:

1. every registered function key renders to a checked-in golden,
2. the same goldens hold with the post-flip default configured and
   ``claude-opus`` selected explicitly,
3. a content-hash manifest over the template sources fails, naming the key,
   rather than letting a golden be silently re-baselined.

The golden corpus KOD-63 shipped is EXTENDED, never forked: the thirteen keys
it covers keep their cases verbatim and the five it left uncovered gain
theirs, all in the same directory.
"""

import hashlib
import json
from pathlib import Path

import pytest

from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.config import AppConfig
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.test_prompt_wiring import (
    DEFAULT_SET,
    GOLDEN_CASES,
    GOLDENS,
    REPO_ROOT,
    TASK_MD,
    load_registry,
)

#: The set name the default moves to (KOD-93). Named here so the flip test
#: reads the same string the configuration will.
V5_SET = "anthropic_v5"

MANIFEST = Path(__file__).parent / "claude_opus_source_hashes.json"
POPULATED = GOLDENS.parent / "claude_opus_populated_skills"
EXAMPLE_OPERATION = REPO_ROOT / "docs" / "operation.example.toml"

AUDITED_PAYLOAD = "Golden payload under audit.\nSecond line."
AUDIT_DESTINATION = "a public code-hosting surface"

# The five keys KOD-63's goldens leave uncovered: they had no 92597c0
# baseline to be byte-identical to, which is a reason to skip a BASELINE
# claim and no reason at all to leave them unfrozen. Rendered from the same
# kind of fixed fixtures, plus the operation namespace the two pass keys and
# the knowledge map address.
EXTENDED_CASES: dict[str, tuple[PromptKey, dict[str, object]]] = {
    "content_audit": (
        PromptKey.CONTENT_AUDIT,
        {"content": AUDITED_PAYLOAD, "destination": AUDIT_DESTINATION},
    ),
    "knowledge_map": (PromptKey.KNOWLEDGE_MAP, {}),
    "fire_prep_pass": (PromptKey.FIRE_PREP_PASS, {}),
    "grooming_pass": (PromptKey.GROOMING_PASS, {}),
    "remediation_ticket": (
        PromptKey.REMEDIATION_TICKET,
        {
            "original_ticket": TASK_MD,
            "done_work": "golden done work",
            "failure_evidence": "golden failure evidence",
        },
    ),
}

ALL_CASES: dict[str, tuple[PromptKey, dict[str, object]]] = {
    **GOLDEN_CASES,
    **EXTENDED_CASES,
}


def operation_registry(*, default_set: str = DEFAULT_SET) -> InRepoPromptRegistry:
    """The legacy set with the example operation namespace bound.

    The extra names are additive: a template that references none of them
    renders exactly as it does under the registry suite's empty bindings,
    which is what lets one registry serve all eighteen cases.
    """
    return load_registry(
        default_set=default_set,
        bindings=dict(operation_bindings(load_operation_config(EXAMPLE_OPERATION))),
    )


def render_case(registry: InRepoPromptRegistry, golden_name: str) -> str:
    """Render one case with the skills fragment bound EMPTY."""
    key, variables = ALL_CASES[golden_name]
    return registry.template_for(key).render({**variables, "skills_reference": ""})


def render_case_with_declared_skills(
    registry: InRepoPromptRegistry,
    golden_name: str,
) -> str:
    """Render one case with the key's DECLARED skills loadout bound."""
    key, variables = ALL_CASES[golden_name]
    return registry.template_for(key).render(variables)


def source_files() -> list[Path]:
    """Every checked-in source file of the legacy set, manifest order."""
    set_dir = default_sets_root() / DEFAULT_SET
    return sorted(set_dir.iterdir())


# ---------------------------------------------------------------------------
# KOD-83-AC-1 — one test per function key
# ---------------------------------------------------------------------------


def test_every_registered_function_key_has_a_golden() -> None:
    """No key escapes the byte-identity guarantee — including the net-new ones."""
    covered = {key for key, _ in ALL_CASES.values()}
    assert covered == set(PromptKey)


@pytest.mark.parametrize("golden_name", sorted(ALL_CASES))
def test_rendered_output_equals_the_checked_in_golden(golden_name: str) -> None:
    """Mutating any legacy template byte fails at least one of these."""
    rendered = render_case(operation_registry(), golden_name)
    expected = (GOLDENS / f"{golden_name}.txt").read_text(encoding="utf-8")
    assert rendered == expected


@pytest.mark.parametrize("golden_name", sorted(EXTENDED_CASES))
def test_extended_case_populated_variant_matches_its_golden(golden_name: str) -> None:
    """Both fragment variants stay paired, so neither suite covers less.

    KOD-46's populated suite is parameterised over the thirteen cases that
    existed when it was written; the five added here supply their own
    populated arm rather than leaving five checked-in goldens unasserted.
    """
    rendered = render_case_with_declared_skills(operation_registry(), golden_name)
    expected = (POPULATED / f"{golden_name}.txt").read_text(encoding="utf-8")
    assert rendered == expected


# ---------------------------------------------------------------------------
# KOD-83-AC-2 — flip invariance
# ---------------------------------------------------------------------------


def test_goldens_hold_under_flipped_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the post-flip default configured, explicit selection is unchanged.

    The default set is what a flip moves; selecting ``claude-opus`` by name
    is the rollback path, and this is its executable form. Passes only if
    the flip leaves the legacy set untouched.
    """
    monkeypatch.setenv("KODEZART_PROMPT_SET", V5_SET)
    assert AppConfig.from_env().prompt_set == V5_SET

    registry = operation_registry()
    assert set(registry.resolution_table().values()) == {DEFAULT_SET}

    mismatched = [
        golden_name
        for golden_name in sorted(ALL_CASES)
        if render_case(registry, golden_name)
        != (GOLDENS / f"{golden_name}.txt").read_text(encoding="utf-8")
    ]
    assert mismatched == []


# ---------------------------------------------------------------------------
# KOD-83-AC-3 — the content-hash manifest
# ---------------------------------------------------------------------------


def test_legacy_source_hashes_unchanged() -> None:
    """A legacy source edit fails HERE, naming the key, not by re-baselining."""
    manifest: dict[str, str] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    measured = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_files()
    }

    assert sorted(manifest) == sorted(measured), (
        "the legacy set gained or lost a source file; "
        "the manifest is the record that has to say so"
    )
    changed = sorted(
        name for name, digest in measured.items() if manifest[name] != digest
    )
    assert changed == [], f"legacy template sources changed: {', '.join(changed)}"


def test_manifest_covers_every_registered_key() -> None:
    """The manifest is a census of the set, not a sample of it."""
    manifest: dict[str, str] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert set(manifest) == {f"{key.value}.md" for key in PromptKey} | {"set.toml"}
