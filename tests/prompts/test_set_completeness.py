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
from tests.prompt_census import PROMPT_FUNCTION_NAMES
from tests.prompts.sets import ALL_CASES, V5_SET
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
    """The independent role names exactly match the registered enum."""
    assert {key.value for key in PromptKey} == PROMPT_FUNCTION_NAMES


#: The keys no render case covers yet, each one older than this census.  A case
#: is fixture variables plus a declared artifact tag, so authoring the four is
#: work of its own and is owed separately; they are named here so the census
#: holds for every other key instead of not running at all.  Exact in both
#: directions below: authoring one of these cases reds this list rather than
#: leaving it to go stale.
KEYS_WITHOUT_A_RENDER_CASE = frozenset(
    {
        PromptKey.AMENDMENT_AUTHOR,
        PromptKey.AMENDMENT_JUDGE,
        PromptKey.NATIVE_WRITER_CONTRACT,
        PromptKey.WRITE_BACK_VERIFY,
    }
)


def test_every_registered_function_key_has_a_render_case() -> None:
    """The roster is a census of the keys, not a sample of them.

    The one guard that catches a key dropped from the case roster and the
    artifact tags together: each of those two rosters is otherwise pinned only
    by the other, so a key missing from both is missing from nothing.  It lives
    here rather than beside the roster it reads because ``sets.py`` is not a
    module pytest collects, where this census never ran at all.
    """
    assert {key for key, _ in ALL_CASES.values()} == (
        set(PromptKey) - KEYS_WITHOUT_A_RENDER_CASE
    )


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


@pytest.mark.parametrize("missing", list(PromptKey))
def test_a_set_missing_one_key_raises_the_typed_boot_error(
    missing: PromptKey,
    tmp_path: Path,
) -> None:
    """Removing one key from a set names that key in the typed boot error.

    The skills table stays complete, so the member file is the only thing the
    set no longer supplies: a registry that stopped looking for the member and
    trusted the table alone would pass otherwise.
    """
    members = complete_members("fixture")
    del members[missing.value]
    write_set(tmp_path, "fixture", members, skills={key.value: [] for key in PromptKey})

    with pytest.raises(PromptResolutionError) as excinfo:
        load_registry(sets_root=tmp_path, default_set="fixture")
    assert missing.value in excinfo.value.failing_keys
