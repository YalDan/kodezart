"""The stable identity of a deliverable created during a scope run."""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.scope import ScopeRef


class IssueIdentity(CamelCaseModel):
    """Scope kind and key both participate in deliverable identity."""

    model_config = ConfigDict(frozen=True)

    scope_key: ScopeRef
    deliverable_key: str = Field(min_length=1)
