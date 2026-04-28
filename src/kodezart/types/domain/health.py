"""HealthStatus domain value object."""

from kodezart.types.base import CamelCaseModel


class HealthStatus(CamelCaseModel):
    """Health check value object for ``GET /api/v1/health``."""

    healthy: bool
    version: str
    service: str
