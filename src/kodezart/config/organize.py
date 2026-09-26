"""Deployment bounds for the configured Organize owner; none has a default."""

from pydantic import ConfigDict

from kodezart.types.domain.organize_owner import OrganizePolicy


class OrganizeSettings(OrganizePolicy):
    """The owner's two bounds, and nothing else.

    No cadence lives here: the organize stages are not a scheduled pass.
    They run inside a scope run, which the approval label admits and the
    standing scopes' heartbeat submits on the dispatch cadence.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
