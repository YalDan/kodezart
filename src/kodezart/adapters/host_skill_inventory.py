"""Host skill inventory — reads the user-scope skills and plugins on disk.

kodezart neither vendors nor installs skills.  They are provisioned by the
host at user scope (``<claude home>/skills`` for bare skills, plugin bundles
recorded in ``<claude home>/plugins/installed_plugins.json``).  This adapter
reads that inventory so the composition root can pre-flight a configured
allowlist before serving traffic.

Plugin skills resolve through the manifest, never through a walk of the
plugin cache: cache directories outlive uninstallation, so a direct walk
would report names the CLI would then silently filter — the same
false-positive hole the boot pre-flight exists to close, from the other
side.  In practice each manifest entry's ``installPath`` names
``plugins/cache/<marketplace>/<plugin>/<version>``, so that layout is
covered by reading the manifest.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from kodezart.core.errors import SkillInventoryError

_SKILL_MANIFEST = "SKILL.md"
_SKILLS_DIR = "skills"
_PLUGINS_DIR = "plugins"
_INSTALLED_PLUGINS = "installed_plugins.json"
_PLUGINS_KEY = "plugins"
_SCOPE_KEY = "scope"
_INSTALL_PATH_KEY = "installPath"
_USER_SCOPE = "user"
_PLUGIN_SEPARATOR = ":"
_MARKETPLACE_SEPARATOR = "@"


class HostSkillInventory:
    """``SkillInventory`` backed by the host's user-scope Claude directory."""

    def __init__(self, *, home_dir: str) -> None:
        self._root: Path = Path(home_dir).expanduser()

    def available(self) -> frozenset[str]:
        """Every skill name the host exposes at user scope.

        Bare skills are named by their directory; plugin skills are named
        ``<plugin>:<skill>``, matching how a session addresses them.
        """
        names: set[str] = set(_scan(self._root / _SKILLS_DIR))
        manifest = self._root / _PLUGINS_DIR / _INSTALLED_PLUGINS
        for plugin, install_path in _user_scope_installations(manifest):
            for skill in _scan(install_path / _SKILLS_DIR):
                names.add(f"{plugin}{_PLUGIN_SEPARATOR}{skill}")
        return frozenset(names)


def _scan(skills_dir: Path) -> set[str]:
    """Skill directory names under *skills_dir* that carry a ``SKILL.md``."""
    if not skills_dir.is_dir():
        return set()
    return {
        entry.name
        for entry in sorted(skills_dir.iterdir())
        if entry.is_dir() and (entry / _SKILL_MANIFEST).is_file()
    }


def _user_scope_installations(manifest: Path) -> list[tuple[str, Path]]:
    """``(plugin name, install path)`` for every user-scope installation.

    An absent manifest means no plugins are installed and yields nothing.  A
    manifest that exists but cannot be read as the documented shape raises,
    listing every problem at once.
    """
    if not manifest.is_file():
        return []
    try:
        document: object = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        msg = "Installed-plugins manifest is unreadable"
        raise SkillInventoryError(msg, problems=[f"{manifest}: {exc}"]) from exc

    problems: list[str] = []
    installations: list[tuple[str, Path]] = []
    for key, entries in _plugin_entries(document, problems).items():
        plugin = key.split(_MARKETPLACE_SEPARATOR, maxsplit=1)[0]
        for index, entry in enumerate(entries):
            path = _install_path(entry, f"{key}[{index}]", problems)
            if path is not None:
                installations.append((plugin, path))
    if problems:
        msg = "Installed-plugins manifest has an unexpected shape"
        raise SkillInventoryError(msg, problems=problems)
    return installations


def _plugin_entries(
    document: object,
    problems: list[str],
) -> Mapping[str, Sequence[object]]:
    """The ``plugins`` mapping, or an empty mapping with problems recorded."""
    if not isinstance(document, dict):
        problems.append("manifest root is not an object")
        return {}
    plugins = document.get(_PLUGINS_KEY)
    if not isinstance(plugins, dict):
        problems.append(f"{_PLUGINS_KEY!r} is not an object")
        return {}
    entries: dict[str, Sequence[object]] = {}
    for key, value in plugins.items():
        if not isinstance(value, list):
            problems.append(f"{key}: installations are not a list")
            continue
        entries[key] = value
    return entries


def _install_path(entry: object, label: str, problems: list[str]) -> Path | None:
    """The install path of a user-scope *entry*, or ``None``.

    ``None`` covers two distinct cases: an installation at some other scope
    (not part of the user-scope inventory) and a malformed entry (recorded in
    *problems*, which makes the whole read raise).
    """
    if not isinstance(entry, dict):
        problems.append(f"{label}: installation is not an object")
        return None
    scope = entry.get(_SCOPE_KEY)
    if not isinstance(scope, str):
        problems.append(f"{label}: {_SCOPE_KEY!r} is not a string")
        return None
    if scope != _USER_SCOPE:
        return None
    install_path = entry.get(_INSTALL_PATH_KEY)
    if not isinstance(install_path, str):
        problems.append(f"{label}: {_INSTALL_PATH_KEY!r} is not a string")
        return None
    path = Path(install_path).expanduser()
    if not path.is_absolute():
        problems.append(f"{label}: {_INSTALL_PATH_KEY} is not absolute")
        return None
    return path
