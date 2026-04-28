"""Pure domain logic for health checks — no I/O, no side effects."""

from kodezart.types.domain.health import HealthStatus

_APP_VERSION = "0.1.0"


def check_health() -> HealthStatus:
    """Return the current application health status. Pure function with no I/O."""
    return HealthStatus(healthy=True, version=_APP_VERSION, service="kodezart")
