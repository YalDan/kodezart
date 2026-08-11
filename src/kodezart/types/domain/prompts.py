"""Prompt function keys and set metadata.

A key names a ROLE, not a file.  Which template serves a role is resolved
per key through the precedence chain in ``InRepoPromptRegistry``; a set is
a directory of data files, never Python.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


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


class PromptSetFragments(BaseModel):
    """Set-level fragment content bound into every member of the set.

    Authored as data in ``set.toml`` so no prompt prose lives in code.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    skills_reference_header: str


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
