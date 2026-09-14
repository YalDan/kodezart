"""Deployment choices used by agent sessions and their boot preflight."""

from pydantic import BaseModel, ConfigDict, Field

from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.skills import SettingSource, SkillsMode, SkillsSelection


class AgentSettings(BaseModel):
    """Agent model selection, native settings and host-provisioned skills."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    model: str | None = Field(
        default=None, description="Primary model override; None uses the SDK default."
    )
    fallback_model: str | None = Field(
        default=None, description="Fallback model; None declares no fallback."
    )
    session_models: dict[PromptKey, str] = Field(
        default_factory=dict, description="Model overrides by prompt function key."
    )
    output_style: str | None = Field(
        default=None,
        description="Declared native output style; None leaves the CLI default.",
    )
    home_dir: str = Field(
        default="~/.claude", description="Host user-scope skills and plugins directory."
    )
    setting_sources: list[SettingSource] = Field(
        default_factory=lambda: [
            SettingSource.USER,
            SettingSource.PROJECT,
            SettingSource.LOCAL,
        ],
        description="Native settings sources loaded in every session.",
    )
    skills: SkillsSelection = Field(
        default_factory=lambda: SkillsSelection(mode=SkillsMode.NONE),
        description="Suppress-all, all or an explicit skill allowlist.",
    )
