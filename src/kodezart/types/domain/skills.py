"""Skill-selection domain types.

The SDK's ``skills`` option is ``list[str] | Literal["all"] | None`` where
``None`` is NOT skills-off — it leaves the CLI's own defaults in force.  The
three-state type below therefore has NO ``None`` inhabitant: every mode maps
to an explicit SDK value.
"""

from enum import StrEnum
from typing import Self

from pydantic import ConfigDict, model_validator

from kodezart.types.base import CamelCaseModel


class SkillsMode(StrEnum):
    """Three-state skill selection.

    - ``NONE``: suppress every skill (SDK ``[]``).
    - ``ALL``: every discovered skill (SDK ``"all"``).
    - ``EXPLICIT``: exactly the allowlist (SDK ``list[str]``).
    """

    NONE = "none"
    ALL = "all"
    EXPLICIT = "explicit"


class SettingSource(StrEnum):
    """Settings sources the SDK loads for a session."""

    USER = "user"
    PROJECT = "project"
    LOCAL = "local"


class SkillsSelection(CamelCaseModel):
    """The configured skill selection threaded to executor sessions."""

    model_config = ConfigDict(frozen=True)

    mode: SkillsMode
    allowlist: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_mode_allowlist_agreement(self) -> Self:
        """EXPLICIT needs names; every other mode must not carry any."""
        if self.mode is SkillsMode.EXPLICIT and not self.allowlist:
            msg = "skills mode EXPLICIT requires a non-empty allowlist"
            raise ValueError(msg)
        if self.mode is not SkillsMode.EXPLICIT and self.allowlist:
            msg = f"skills mode {self.mode.value} must not carry an allowlist"
            raise ValueError(msg)
        return self
