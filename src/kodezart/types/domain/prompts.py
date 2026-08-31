"""Prompt function keys and set metadata.

A key names a ROLE, not a file.  Which template serves a role is resolved
per key through the precedence chain in ``InRepoPromptRegistry``; a set is
a directory of data files, never Python.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kodezart.types.domain.subagents import SessionEffort


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
    #: The in-session critique the create-only ticket mode composes into the
    #: creator's member.  Declared by the set and selected by the mode, so
    #: whether a session critiques its draft is never a conditional the
    #: session evaluates for itself.
    ticket_create_critique: str | None = None
    #: The scan-window, run-record and knowledge-destination clauses every
    #: scheduled pass of a set shares.  One source and one clause per
    #: mechanism, because each pass owes the same three and a copy per
    #: member is a copy that can drift.
    pass_mechanisms: str | None = None


class SessionRole(StrEnum):
    """What kind of work a prompt key's session does.

    Session policy attaches to the ROLE, not to the key: changing what a
    judgment session costs is then one edit in a data file rather than an
    edit per key, and a new key inherits a decision instead of needing one.
    """

    #: Authors a new artifact from a task.
    GENERATIVE = "generative"
    #: Grades an artifact against stated criteria and returns a verdict.
    EVALUATIVE = "evaluative"
    #: Emits a name, a message, a description, or a prelude.
    UTILITY = "utility"
    #: Changes the workspace.
    IMPLEMENTATION = "implementation"


class SessionRolePolicy(BaseModel):
    """What one role's sessions run at, and which keys belong to it.

    ``effort`` is NAMED rather than left absent even where it equals the
    engine default: the harness exposes no read-back of the level it
    resolved, so a policy that says "the default" cannot be compared with
    one that says "one below the default", and the relation between the
    two is the point of the declaration.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    effort: SessionEffort
    skills: list[str] = Field(default_factory=list)
    keys: list[str]


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
    #: Per-KEY skill loadouts.  A set declares these OR session roles, never
    #: both: two tables deciding one loadout is two answers that can differ.
    skills: dict[str, list[str]] = Field(default_factory=dict)
    #: Per-ROLE session policy, keyed by role.  Empty means the set declares
    #: none and its dispatches carry no session-scoped decision at all.
    session_roles: dict[SessionRole, SessionRolePolicy] = Field(default_factory=dict)
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

    @model_validator(mode="after")
    def _check_one_loadout_source(self) -> Self:
        """One mechanism decides a loadout, and a key belongs to one role."""
        if self.skills and self.session_roles:
            msg = (
                f"prompt set {self.name!r} declares both per-key skills and "
                "session roles; a loadout has one source"
            )
            raise ValueError(msg)

        seen: set[str] = set()
        for role, policy in self.session_roles.items():
            duplicated = sorted(seen.intersection(policy.keys))
            if duplicated:
                msg = (
                    f"prompt set {self.name!r} assigns {duplicated} to more "
                    f"than one session role, including {role.value!r}"
                )
                raise ValueError(msg)
            seen.update(policy.keys)

        utility = self.session_roles.get(SessionRole.UTILITY)
        if utility is not None and set(utility.keys) != set(self.utility_keys):
            msg = (
                f"prompt set {self.name!r} declares a utility roster that "
                "disagrees with the utility role's keys"
            )
            raise ValueError(msg)
        return self

    def role_of(self, key: str) -> SessionRole | None:
        """The role *key* belongs to, or ``None`` when it belongs to none."""
        return next(
            (role for role, policy in self.session_roles.items() if key in policy.keys),
            None,
        )

    def effort_of(self, key: str) -> SessionEffort | None:
        """The effort *key*'s role declares, or ``None`` without roles."""
        role = self.role_of(key)
        return None if role is None else self.session_roles[role].effort

    def skill_names(self, key: str) -> list[str] | None:
        """*key*'s declared loadout, from whichever mechanism this set uses.

        ``None`` means the set decides nothing for this key — a per-key
        table that omits it, or a role roster that never claims it.  Both
        are the same boot failure to the caller, which is what makes an
        unassigned key loud instead of defaulted.
        """
        role = self.role_of(key)
        if role is not None:
            return list(self.session_roles[role].skills)
        if key in self.skills:
            return list(self.skills[key])
        return None
