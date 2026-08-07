"""Host skill inventory — reads the user-scope skills and plugins on disk.

kodezart neither vendors nor installs skills.  They are provisioned by the
host at user scope (``~/.claude/skills`` and plugin bundles under
``~/.claude/plugins``).  This adapter reads that inventory so the composition
root can pre-flight a configured allowlist before serving traffic.
"""

from pathlib import Path

_SKILL_MANIFEST = "SKILL.md"
_SKILLS_DIR = "skills"
_PLUGINS_DIR = "plugins"
_PLUGIN_SEPARATOR = ":"


class HostSkillInventory:
    """``SkillInventory`` backed by the host's user-scope Claude directory."""

    def __init__(self, *, home_dir: str) -> None:
        self._root: Path = Path(home_dir).expanduser()

    def available(self) -> frozenset[str]:
        """Every skill name the host exposes at user scope."""
        names: set[str] = set()
        names.update(_scan(self._root / _SKILLS_DIR))
        plugins_root = self._root / _PLUGINS_DIR
        if plugins_root.is_dir():
            for plugin_dir in sorted(plugins_root.iterdir()):
                if not plugin_dir.is_dir():
                    continue
                for skill in _scan(plugin_dir / _SKILLS_DIR):
                    names.add(f"{plugin_dir.name}{_PLUGIN_SEPARATOR}{skill}")
        return frozenset(names)


def _scan(skills_dir: Path) -> set[str]:
    if not skills_dir.is_dir():
        return set()
    return {
        entry.name
        for entry in sorted(skills_dir.iterdir())
        if entry.is_dir() and (entry / _SKILL_MANIFEST).is_file()
    }
