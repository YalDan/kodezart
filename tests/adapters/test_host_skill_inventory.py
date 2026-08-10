"""HostSkillInventory over a FIXTURED host tree (KOD-46 R10).

Every test builds its own host directory under ``tmp_path``.  Nothing here
reads the real user-scope Claude directory — the inventory's answer is the
boot pre-flight's ground truth, so it has to be measured against a tree the
test owns.
"""

import json
from pathlib import Path

import pytest

from kodezart.adapters.host_skill_inventory import HostSkillInventory
from kodezart.composition.preflight import preflight_skills
from kodezart.core.errors import SkillInventoryError, SkillPreflightError
from kodezart.core.protocols import SkillInventory
from kodezart.types.domain.skills import SkillsMode, SkillsSelection

MARKETPLACE = "example-marketplace"
PLUGIN = "example-plugin"
VERSION = "1.0.0"


def write_skill(skills_dir: Path, name: str) -> Path:
    """Create ``<skills_dir>/<name>/SKILL.md`` and return the skill dir."""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return skill_dir


def write_manifest(home: Path, plugins: dict[str, list[dict[str, object]]]) -> Path:
    """Write ``plugins/installed_plugins.json`` with the documented shape."""
    plugins_dir = home / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    manifest = plugins_dir / "installed_plugins.json"
    manifest.write_text(
        json.dumps({"version": 2, "plugins": plugins}),
        encoding="utf-8",
    )
    return manifest


def cache_install(home: Path, *, version: str = VERSION) -> Path:
    """The cache install path the real manifest points at."""
    return home / "plugins" / "cache" / MARKETPLACE / PLUGIN / version


@pytest.fixture
def host(tmp_path: Path) -> Path:
    """A host tree with one bare skill and one installed plugin skill.

    Mirrors the on-disk layout of a provisioned host: bare skills directly
    under ``skills/``, plugin skills under an install path recorded in
    ``installed_plugins.json``.
    """
    home = tmp_path / "claude-home"
    write_skill(home / "skills", "local-skill")
    install_path = cache_install(home)
    write_skill(install_path / "skills", "bundled-skill")
    write_manifest(
        home,
        {
            f"{PLUGIN}@{MARKETPLACE}": [
                {
                    "scope": "user",
                    "installPath": str(install_path),
                    "version": VERSION,
                },
            ],
        },
    )
    return home


def test_isinstance_skill_inventory(host: Path) -> None:
    assert isinstance(HostSkillInventory(home_dir=str(host)), SkillInventory)


def test_bare_user_scope_skills_resolve_by_directory_name(host: Path) -> None:
    """A ``skills/<name>/SKILL.md`` directory is inventoried as ``<name>``."""
    assert "local-skill" in HostSkillInventory(home_dir=str(host)).available()


def test_plugin_skills_resolve_through_the_installed_manifest(host: Path) -> None:
    """R10: a provisioned plugin skill is inventoried as ``<plugin>:<skill>``.

    The pre-R10 adapter scanned ``plugins/<dir>/skills`` — a level that holds
    only ``cache/``, ``marketplaces/`` and the manifest files — so it resolved
    no plugin-qualified name at all.
    """
    available = HostSkillInventory(home_dir=str(host)).available()
    assert f"{PLUGIN}:bundled-skill" in available
    assert available == {"local-skill", f"{PLUGIN}:bundled-skill"}


def test_marketplace_qualifier_is_stripped_from_the_skill_name(host: Path) -> None:
    """Session-visible names are ``<plugin>:<skill>``, never marketplace-tagged."""
    available = HostSkillInventory(home_dir=str(host)).available()
    assert not any(MARKETPLACE in name for name in available)


def test_every_user_scope_installation_of_a_plugin_contributes(tmp_path: Path) -> None:
    """The manifest records a LIST of installations per plugin key."""
    home = tmp_path / "claude-home"
    first = cache_install(home, version="1.0.0")
    second = cache_install(home, version="2.0.0")
    write_skill(first / "skills", "old-skill")
    write_skill(second / "skills", "new-skill")
    write_manifest(
        home,
        {
            f"{PLUGIN}@{MARKETPLACE}": [
                {"scope": "user", "installPath": str(first)},
                {"scope": "user", "installPath": str(second)},
            ],
        },
    )
    assert HostSkillInventory(home_dir=str(home)).available() == {
        f"{PLUGIN}:old-skill",
        f"{PLUGIN}:new-skill",
    }


def test_directory_without_a_skill_manifest_is_not_inventoried(host: Path) -> None:
    """A directory is a skill only when it carries a ``SKILL.md``."""
    (host / "skills" / "docs-only").mkdir()
    (host / "skills" / "docs-only" / "README.md").write_text("x", encoding="utf-8")
    (cache_install(host) / "skills" / "half-installed").mkdir()
    available = HostSkillInventory(home_dir=str(host)).available()
    assert "docs-only" not in available
    assert f"{PLUGIN}:half-installed" not in available


def test_non_user_scope_installations_are_not_user_scope_inventory(
    tmp_path: Path,
) -> None:
    """R5/R10: this adapter is the user-scope inventory by contract."""
    home = tmp_path / "claude-home"
    install_path = cache_install(home)
    write_skill(install_path / "skills", "project-skill")
    write_manifest(
        home,
        {
            f"{PLUGIN}@{MARKETPLACE}": [
                {"scope": "project", "installPath": str(install_path)},
            ],
        },
    )
    assert HostSkillInventory(home_dir=str(home)).available() == frozenset()


def test_vanished_install_directory_contributes_nothing(tmp_path: Path) -> None:
    """A stale manifest entry whose files are gone reports no skills."""
    home = tmp_path / "claude-home"
    write_manifest(
        home,
        {
            f"{PLUGIN}@{MARKETPLACE}": [
                {"scope": "user", "installPath": str(cache_install(home))},
            ],
        },
    )
    assert HostSkillInventory(home_dir=str(home)).available() == frozenset()


def test_absent_manifest_means_no_plugins_are_installed(tmp_path: Path) -> None:
    """No manifest, no installations — bare skills still resolve."""
    home = tmp_path / "claude-home"
    write_skill(home / "skills", "local-skill")
    # A populated cache with no manifest entry: uninstalled leftovers must NOT
    # be inventoried, or the pre-flight would pass for a name the CLI filters.
    write_skill(cache_install(home) / "skills", "orphaned-skill")
    assert HostSkillInventory(home_dir=str(home)).available() == {"local-skill"}


def test_absent_home_directory_yields_an_empty_inventory(tmp_path: Path) -> None:
    inventory = HostSkillInventory(home_dir=str(tmp_path / "missing"))
    assert inventory.available() == frozenset()


def test_home_dir_is_tilde_expanded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shipped ``~/.claude`` default form resolves against the home dir."""
    monkeypatch.setenv("HOME", str(tmp_path))
    write_skill(tmp_path / ".claude" / "skills", "local-skill")
    assert HostSkillInventory(home_dir="~/.claude").available() == {"local-skill"}


def test_unparseable_manifest_raises_instead_of_reporting_no_plugins(
    tmp_path: Path,
) -> None:
    """Swallowing the read would surface as "skill not provisioned"."""
    home = tmp_path / "claude-home"
    (home / "plugins").mkdir(parents=True)
    (home / "plugins" / "installed_plugins.json").write_text("{", encoding="utf-8")
    with pytest.raises(SkillInventoryError) as excinfo:
        HostSkillInventory(home_dir=str(home)).available()
    assert excinfo.value.problems


def test_unexpected_manifest_shape_lists_every_problem_at_once(
    tmp_path: Path,
) -> None:
    """Collect-all, the repository's convention for boot-time reads."""
    home = tmp_path / "claude-home"
    plugins_dir = home / "plugins"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "a@m": "not-a-list",
                    "b@m": [{"scope": 7}],
                    "c@m": [{"scope": "user", "installPath": 7}],
                    "d@m": [{"scope": "user", "installPath": "relative/path"}],
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SkillInventoryError) as excinfo:
        HostSkillInventory(home_dir=str(home)).available()
    problems = excinfo.value.problems
    assert len(problems) == 4
    assert any("a@m" in problem for problem in problems)
    assert any("b@m" in problem for problem in problems)
    assert any("c@m" in problem for problem in problems)
    assert any("d@m" in problem and "absolute" in problem for problem in problems)


def test_manifest_root_of_the_wrong_type_raises(tmp_path: Path) -> None:
    home = tmp_path / "claude-home"
    (home / "plugins").mkdir(parents=True)
    (home / "plugins" / "installed_plugins.json").write_text("[]", encoding="utf-8")
    with pytest.raises(SkillInventoryError):
        HostSkillInventory(home_dir=str(home)).available()


# ---------------------------------------------------------------------------
# R4 x R10 — the boot pre-flight over the real adapter
# ---------------------------------------------------------------------------


def test_explicit_allowlist_naming_a_plugin_skill_preflights_clean(
    host: Path,
) -> None:
    """The defect R10 fixes: this aborted boot for a provisioned skill."""
    selection = SkillsSelection(
        mode=SkillsMode.EXPLICIT,
        allowlist=("local-skill", f"{PLUGIN}:bundled-skill"),
    )
    preflight_skills(selection, HostSkillInventory(home_dir=str(host)))


def test_explicit_allowlist_naming_an_absent_plugin_skill_fails_at_boot(
    host: Path,
) -> None:
    """Still loud for a name that genuinely resolves to nothing."""
    selection = SkillsSelection(
        mode=SkillsMode.EXPLICIT,
        allowlist=(f"{PLUGIN}:absent", "other-absent"),
    )
    with pytest.raises(SkillPreflightError) as excinfo:
        preflight_skills(selection, HostSkillInventory(home_dir=str(host)))
    assert excinfo.value.unresolvable == (f"{PLUGIN}:absent", "other-absent")
