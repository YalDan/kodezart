"""Deployment bounds for the configured Organize owner; neither has a default."""

from pydantic import ConfigDict, Field

from kodezart.types.domain.organize_owner import OrganizePolicy


class OrganizeSettings(OrganizePolicy):
    """The owner's two bounds, and the organize tick's own cadence and budget.

    The tick is scheduled under the grooming pass's row, and until 2026-09-24
    it took that row's interval and timeout with no way to set its own. The
    operator's standing grooming cadence is six hours, which made a triaged
    scope wait up to six hours for its groom phase. These two fields are the
    tick's own; ``None`` means the grooming pass's value, so a deployment that
    sets neither keeps the cadence it had.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    interval_seconds: float | None = Field(
        default=None,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds between organize ticks. Unset, the tick runs on "
            "KODEZART_GROOMING_PASS_INTERVAL_SECONDS; the same bounds apply."
        ),
    )
    timeout_seconds: float | None = Field(
        default=None,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds one organize tick may take before it is abandoned. Unset, "
            "the tick runs under KODEZART_GROOMING_PASS_TIMEOUT_SECONDS; the "
            "same bounds apply."
        ),
    )
