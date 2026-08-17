"""Skill-selection domain types.

The SDK's ``skills`` option is ``list[str] | Literal["all"] | None`` where
``None`` is NOT skills-off — it leaves the CLI's own defaults in force.  The
three-state type below therefore has NO ``None`` inhabitant: every mode maps
to an explicit SDK value.
"""

from collections.abc import Sequence
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

    def narrowed_to(self, names: Sequence[str]) -> Self:
        """This selection, narrowed to *names*.

        Effective availability is the INTERSECTION of what the deployment
        allows and what the role declares — two independent bounds, either
        of which can bind.  Suppression therefore always wins: a role
        declaring skills under ``NONE`` still loads none, because the
        operator's switch is not a role's to reopen.
        """
        if self.mode is SkillsMode.NONE:
            return self
        wanted = [
            name
            for name in names
            if self.mode is SkillsMode.ALL or name in self.allowlist
        ]
        if not wanted:
            return type(self)(mode=SkillsMode.NONE)
        return type(self)(mode=SkillsMode.EXPLICIT, allowlist=tuple(wanted))

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
