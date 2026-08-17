"""Session-scoped configuration carried across the executor port.

Everything here is a cross-component convention, so it is a typed domain
model rather than a dictionary: a subagent definition, the depth/cost
levers a session runs at, and the harness gates that decide whether a
named workflow can fire.  The adapters build the SDK's own shapes from
these; no mapping leaks outward.

An empty :class:`AgentDefinition` sequence is the mechanical guarantee
that a session spawns no subagents.  A template sentence asking a model
not to fan out is a request; an empty definition list is a guarantee, and
the definitions' own read-only tool lists are the second bound — effective
capability is the intersection of a definition's tools and the session
allowlist, either of which can bind.
"""

from enum import StrEnum
from typing import Final

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel


class SessionEffort(StrEnum):
    """Reasoning-effort levels the engine accepts for a session or agent."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class AgentDefinition(CamelCaseModel):
    """One typed lens a session may dispatch.

    A lens is a definition, never a persona: its mandate is its
    ``description``, its instructions are its ``prompt``, and what it can
    reach is its ``tools`` list.  One definition per lens, shared by every
    consumer, so no second copy can drift.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    tools: tuple[str, ...] = Field(min_length=1)


class WorkflowAccess(CamelCaseModel):
    """The harness gates a session needs before a named workflow can fire.

    Measured on this harness: the workflow primitive is real and reachable
    headlessly, but it needs a non-plan permission mode and an allowlist
    entry, so a session that declares this still only fires a workflow when
    its own configuration permits one.
    """

    model_config = ConfigDict(frozen=True)

    workflows_path: str = Field(min_length=1)
    size_guideline: int = Field(gt=0)
    enabled: bool


class SessionPolicy(CamelCaseModel):
    """What a dispatch declares about its session beyond prompt and tools.

    Every field is three-state by absence: ``None`` means this dispatch
    declares nothing, and the session runs on whatever the composition root
    configured at construction.  It is not a default value standing in for
    a decision — it is the absence of a session-scoped decision, which is
    exactly what every dispatch site expressed before this port widened.
    """

    model_config = ConfigDict(frozen=True)

    system_prompt_append: str | None = None
    effort: SessionEffort | None = None
    model: str | None = None
    fallback_model: str | None = None
    workflow_access: WorkflowAccess | None = None


#: The policy a dispatch passes when it declares nothing session-scoped.
UNCONFIGURED_SESSION_POLICY: Final[SessionPolicy] = SessionPolicy()

#: The definition list an evaluative dispatch passes.  Named so the
#: guarantee reads as a guarantee at the call site rather than as an
#: incidental empty literal.
NO_SUBAGENTS: Final[tuple[AgentDefinition, ...]] = ()
