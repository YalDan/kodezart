"""Exhaustive SkillsSelection -> SDK ``skills`` mapping.

The SDK accepts ``list[str] | Literal["all"] | None``.  ``None`` is NOT
skills-off — it leaves the CLI defaults in force — so no branch below ever
produces it.
"""

from collections.abc import Sequence
from typing import Literal

from kodezart.types.domain.skills import SettingSource, SkillsMode, SkillsSelection

_SDK_SOURCE: dict[SettingSource, Literal["user", "project", "local"]] = {
    SettingSource.USER: "user",
    SettingSource.PROJECT: "project",
    SettingSource.LOCAL: "local",
}


def map_skills(selection: SkillsSelection) -> list[str] | Literal["all"]:
    """Map the three-state selection onto the SDK option value."""
    if selection.mode is SkillsMode.NONE:
        return []
    if selection.mode is SkillsMode.ALL:
        return "all"
    return list(selection.allowlist)


def map_setting_sources(
    sources: Sequence[SettingSource],
) -> list[Literal["user", "project", "local"]]:
    """Map the configured setting sources onto the SDK option value."""
    return [_SDK_SOURCE[source] for source in sources]
