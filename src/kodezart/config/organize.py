"""Deployment bounds for the configured Organize owner; none has a default."""

from pydantic import ConfigDict, Field

from kodezart.types.domain.organize_owner import OrganizePolicy


class OrganizeSettings(OrganizePolicy):
    """The owner's two bounds, and the organize tick's own cadence and budget.

    The tick is scheduled under the grooming pass's name, on these two
    settings only. Unset, the tick is not scheduled: no other pass's cadence
    stands in for it (2026-09-24). Set one, set both; a boot with one
    refuses at load naming the pair.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    interval_seconds: float | None = Field(
        default=None,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds between organize ticks. Unset, the tick is not "
            "scheduled. Set together with the timeout."
        ),
    )
    timeout_seconds: float | None = Field(
        default=None,
        ge=60.0,
        le=86400.0,
        description=(
            "Seconds one organize tick may take before it is abandoned. Set "
            "together with the interval."
        ),
    )
