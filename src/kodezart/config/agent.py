"""Deployment choices used by agent sessions and their boot preflight."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

import kodezart
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
    workflows_plugin_dir: str = Field(
        default=str(Path(kodezart.__file__).resolve().parents[2] / ".claude"),
        description=(
            "Local Claude Code plugin every session loads, so the named "
            "workflows in its workflows/ folder can be launched from any "
            "working directory; the checkout's .claude by default."
        ),
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
    dangerously_allow_host_mcp: bool = Field(
        default=False,
        description=(
            "Switch the working-directory MCP guard OFF for every session: "
            "strict_mcp_config becomes False, so a session gets every MCP "
            "server the operator's user-level Claude configuration declares "
            "and any server the session's working directory declares. "
            "Measured 2026-09-24 on Claude Code 2.1.281: with strict mode "
            "off, headless Claude Code started in a directory holding a "
            ".mcp.json that declared a server tried to start that server, so "
            "a cloned repository can run a command on this machine through a "
            "session; with the flag on, sessions reached the Linear server "
            "under the operator's stored login (76 tools) and any tracker "
            "write such a session makes carries the operator's login user, "
            "not this deployment's key. Off unless the operator accepts "
            "exactly that."
        ),
    )
    rate_limit_max_wait_seconds: float = Field(
        default=18000.0,
        ge=0.0,
        le=604800.0,
        description=(
            "Total seconds one session may spend waiting out provider rate "
            "limits before it gives up. A session the provider stops on a "
            "rate limit is run again after a wait: until the reset the "
            "provider states, plus a small jitter, or, when it states none, "
            "an exponential back-off from "
            "retry_rate_limit_floor_seconds doubling to a 30-minute cap. The "
            "job and its workspace stay while it waits. Once this total is "
            "spent the existing failure path runs unchanged, and later "
            "rate-limited sessions stop waiting until one session ends "
            "without a rate limit or this many seconds pass. 0 turns the wait "
            "off. Default 18000, five hours."
        ),
    )
