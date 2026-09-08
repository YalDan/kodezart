"""Deployment choices consumed by Git composition."""

from pydantic import BaseModel, ConfigDict, Field


class GitSettings(BaseModel):
    """Repository locations, remote routing and generated commit identity."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    remote: str = Field(
        default="origin", description="Remote for fetch, push and head probes."
    )
    base_url: str = Field(
        default="https://github.com", description="Base URL for owner/repo shorthand."
    )
    clone_cache_dir: str = Field(
        default="/tmp/kodezart-clones",
        description="Local bare repository cache directory.",
    )
    integration_workspace_dir: str = Field(
        default="/tmp/kodezart-integration",
        description="Directory for temporary base integration worktrees.",
    )
    committer_name: str = Field(
        default="kodezart", description="Name for generated commits."
    )
    committer_email: str = Field(
        default="kodezart@noreply.dev", description="Email for generated commits."
    )
