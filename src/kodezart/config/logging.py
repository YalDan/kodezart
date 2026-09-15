"""Deployment choices consumed by the native logging setup."""

from pydantic import BaseModel, ConfigDict, Field


class LoggingSettings(BaseModel):
    """Minimum severity and JSON or console rendering."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    level: str = Field(
        default="INFO",
        pattern=r"(?i)^(NOTSET|DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)$",
        description="Case-insensitive standard level; WARN/FATAL aliases allowed.",
    )
    pretty: bool = Field(
        default=False, description="Colorized console output; false emits JSON lines."
    )
