"""Prompt function keys and set metadata.

A key names a ROLE, not a file.  Which template serves a role is resolved
per key through the precedence chain in ``InRepoPromptRegistry``; a set is
a directory of data files, never Python.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PromptKey(StrEnum):
    """Every prompt role the pipeline dispatches.

    ``EVALUATION`` and ``POST_MERGE_REVIEW`` are deliberately distinct even
    though the claude-opus set maps them to identical content — separate
    keys let a future set split the roles with zero code change.
    """

    BRANCH_NAME = "branch_name"
    TICKET_CREATE = "ticket_create"
    TICKET_REVIEW = "ticket_review"
    TICKET_REVISION = "ticket_revision"
    ACCEPTANCE_CRITERIA = "acceptance_criteria"
    CRITERIA_VALIDATION = "criteria_validation"
    IMPLEMENTATION = "implementation"
    EVALUATION = "evaluation"
    ITERATION_FEEDBACK = "iteration_feedback"
    POST_MERGE_REVIEW = "post_merge_review"
    FIX = "fix"
    REMEDIATION_TICKET = "remediation_ticket"
    COMMIT_MESSAGE = "commit_message"
    PR_DESCRIPTION = "pr_description"
    FIRE_PREP_PASS = "fire_prep_pass"
    GROOMING_PASS = "grooming_pass"
    CONTENT_AUDIT = "content_audit"
    #: What lives where.  A prelude composed into a session that is granted
    #: the knowledge server, and into no other — a key rather than set-level
    #: fragment metadata, so it enters this census and the set-completeness
    #: rule covers it like every other role.
    KNOWLEDGE_MAP = "knowledge_map"


class OrchestrationPrimitive(StrEnum):
    """How a generative role is told to fan its investigation out.

    Deterministic configuration, never a model judgement: the value is set
    from what the harness enumeration measured, and it selects which
    fragment fills the orchestration slot.
    """

    #: A named repo-owned workflow script coordinates the fan-out and
    #: merges the evidence in code.
    WORKFLOW = "workflow"
    #: Parallel agent dispatches in a single turn, merged by the session.
    AGENT = "agent"


class PromptSetFragments(BaseModel):
    """Set-level fragment content bound into every member of the set.

    Authored as data in ``set.toml`` so no prompt prose lives in code.

    Every field beyond the skills header is three-state by absence:
    ``None`` means the set declares no such fragment and contributes
    nothing of that kind, which is how a set authored before a fragment
    existed stays composed exactly as it was.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    skills_reference_header: str
    house_rules: str | None = None
    suppression_proxy: str | None = None
    ultrathink_instruction: str | None = None
    ultracode_instruction: str | None = None
    #: The shared fan-out spec both orchestration fragments carry, and the
    #: two primitive-specific blocks one of which fills the slot.
    investigation_spec: str | None = None
    orchestration_workflow: str | None = None
    orchestration_agents: str | None = None


class AgentDefinitionSpec(BaseModel):
    """What ``set.toml`` declares about one lens; its prompt is a data file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str
    tools: list[str]


class PromptSetMetadata(BaseModel):
    """Parsed ``set.toml``.

    ``extra="forbid"`` makes an unknown section a typed boot error while
    keeping the type additively extensible — a new section is one more
    field, never a reshape of this model or of its consumers.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    engines: list[str]
    skills: dict[str, list[str]]
    fragments: PromptSetFragments
    #: Roles that carry no reasoning-depth instruction. A set that names
    #: none has no utility roster, which is a statement about the set and
    #: not a default standing in for one.
    utility_keys: list[str] = Field(default_factory=list)
    #: Typed lens definitions the set contributes, keyed by lens name.
    definitions: dict[str, AgentDefinitionSpec] = Field(default_factory=dict)
    #: How this set's generative roles are told to fan out. ``None`` means
    #: the set declares no orchestration at all, and no member of it may
    #: carry the orchestration slot.
    orchestration_primitive: OrchestrationPrimitive | None = None
