"""Application choices consumed by the HTTP boundary."""

from pydantic import BaseModel, ConfigDict, Field


class HttpSettings(BaseModel):
    """FastAPI metadata, debug behavior and route mounting."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    project_name: str = Field(
        default="kodezart", description="FastAPI application title."
    )
    debug: bool = Field(
        default=False, description="Enable debug mode, /docs and /redoc."
    )
    api_v1_prefix: str = Field(
        default="/api/v1", description="Prefix for v1 API routes."
    )
