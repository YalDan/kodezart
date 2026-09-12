"""Deployment bounds for the configured Organize owner; neither has a default."""

from pydantic import ConfigDict

from kodezart.types.domain.organize_owner import OrganizePolicy


class OrganizeSettings(OrganizePolicy):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
