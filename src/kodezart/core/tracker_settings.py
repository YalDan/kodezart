"""Native tracker connection and adapter deployment choices."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from kodezart.types.domain.tracker import TrackerBackend


class TrackerSettings(BaseModel):
    """The HTTP tracker connection opened during application startup."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    backend: TrackerBackend = Field(
        default=TrackerBackend.LINEAR, description="Tracker adapter."
    )
    server_name: str = Field(default="linear", description="MCP server identity.")
    server_url: str = Field(
        default="https://mcp.linear.app/mcp", description="Native MCP endpoint."
    )
    auth_header: str = Field(
        default="Authorization", min_length=1, description="Credential header."
    )
    auth_scheme: str = Field(
        default="Bearer", min_length=1, description="Credential scheme."
    )
    token: SecretStr | None = Field(
        default=None,
        exclude=True,
        description="Long-lived tracker credential; absent leaves the tracker unwired.",
    )
    timeout_seconds: float = Field(
        default=30.0, ge=5, le=120, description="HTTP exchange timeout in seconds."
    )
    call_timeout_seconds: float = Field(
        default=60.0,
        ge=1,
        le=120,
        description="Timeout waiting for one MCP tool answer.",
    )
    sse_read_timeout_seconds: float = Field(
        default=300.0,
        ge=30,
        le=3600,
        description="Maximum quiet time on the session stream.",
    )
    error_detail_limit: int = Field(
        default=500,
        ge=80,
        le=8000,
        description="Maximum characters retained from a server error.",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Retries after the initial transient tracker failure.",
    )
    retry_backoff_factor: float = Field(
        default=1.0,
        ge=0.1,
        le=30,
        description="Initial retry delay in seconds; later delays double.",
    )
