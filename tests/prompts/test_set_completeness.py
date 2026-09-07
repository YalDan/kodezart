"""KOD-83-AC-6 — every shipped set resolves every registered function key.

Completeness is asserted for each set INDEPENDENTLY OF WHICH ONE IS DEFAULT:
the registry validates the default set at boot, so naming a set as the default
is how a test asks "is this one complete?".  That is the property the rollback
path rests on — the legacy set has to keep resolving every key after the
default moves away from it, or "rollback is one env var" is a claim with no
test behind it.
"""

from pathlib import Path

import pytest

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.core.errors import PromptResolutionError
from kodezart.types.domain.prompts import PromptKey
from tests.prompt_census import PROMPT_FUNCTION_COUNT
from tests.prompts.sets import V5_SET
from tests.prompts.test_prompt_wiring import (
    DEFAULT_SET,
    complete_members,
    load_registry,
    write_set,
)


def shipped_sets() -> list[str]:
    """Every set directory the repository ships, by name."""
    return sorted(path.name for path in default_sets_root().iterdir() if path.is_dir())


V5_SET_DIR = default_sets_root() / V5_SET


def test_the_census_is_the_enum() -> None:
    """The shared census and the enum are one number, checked in one place."""
    assert len(PromptKey) == PROMPT_FUNCTION_COUNT


def test_at_least_the_legacy_set_is_shipped() -> None:
    """Non-vacuity: the parameterisation below is never an empty sweep."""
    assert DEFAULT_SET in shipped_sets()


@pytest.mark.parametrize("set_name", shipped_sets())
def test_every_shipped_set_resolves_every_registered_key(set_name: str) -> None:
    """Loading a set as the default succeeds only if it supplies every key."""
    registry = load_registry(default_set=set_name)
    table = registry.resolution_table()
    assert set(table) == set(PromptKey)
    assert set(table.values()) == {set_name}


@pytest.mark.skipif(
    not V5_SET_DIR.is_dir(),
    reason=f"the {V5_SET} set is authored by KOD-88; nothing to assert until it ships",
)
def test_the_new_set_is_shipped_and_complete() -> None:
    """The second set is named explicitly, so its absence cannot pass silently."""
    assert V5_SET in shipped_sets()
    assert set(load_registry(default_set=V5_SET).resolution_table()) == set(PromptKey)


def test_the_legacy_set_stays_complete_when_the_default_names_another_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rollback property: completeness belongs to the set, not the default.

    The configured default names the set the flip moves to; selecting the
    legacy set by name still resolves every key. True before that set is
    authored and after, which is what makes it the rollback evidence.
    """
    monkeypatch.setenv("KODEZART_PROMPT_SET", V5_SET)
    table = load_registry(default_set=DEFAULT_SET).resolution_table()
    assert set(table) == set(PromptKey)
    assert set(table.values()) == {DEFAULT_SET}


@pytest.mark.parametrize(
    "missing",
    [PromptKey.FIX, PromptKey.EVALUATION, PromptKey.KNOWLEDGE_MAP],
)
def test_a_set_missing_one_key_raises_the_typed_boot_error(
    missing: PromptKey,
    tmp_path: Path,
) -> None:
    """Removing one key from a set names that key in the typed boot error."""
    members = complete_members("fixture")
    del members[missing.value]
    write_set(tmp_path, "fixture", members)

    with pytest.raises(PromptResolutionError) as excinfo:
        load_registry(sets_root=tmp_path, default_set="fixture")
    assert missing.value in excinfo.value.failing_keys
