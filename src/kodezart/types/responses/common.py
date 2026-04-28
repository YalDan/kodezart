"""BaseResponse — base for all API response models."""

from datetime import UTC, datetime

from pydantic import Field

from kodezart.types.base import CamelCaseModel


class BaseResponse(CamelCaseModel):
    """Standard API response envelope for non-streaming endpoints.

    The ``data`` field contains endpoint-specific payload; ``error`` is
    populated on failure.
    """

    success: bool = Field(description="Whether the operation succeeded.")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    data: dict[str, object] | None = Field(default=None)
    error: str | None = Field(default=None)
