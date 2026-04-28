"""Workflow state definitions for the ralph loop and outer pipeline."""

from typing import Self, TypedDict

from langchain_core.runnables import RunnableConfig
from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.agent import CriterionResult, TicketDraftOutput

_LANGGRAPH_RESERVED_PREFIX = "__pregel_"
_LANGGRAPH_RESERVED_KEYS: frozenset[str] = frozenset(
    {
        "thread_id",
        "checkpoint_id",
        "checkpoint_ns",
        "checkpoint_map",
    }
)


# ---------------------------------------------------------------------------
# Immutable context models (extracted from LangGraph configurable dicts)
# ---------------------------------------------------------------------------


class WorkflowContext(CamelCaseModel):
    """Immutable context shared by all workflow stages."""

    model_config = ConfigDict(frozen=True)

    prompt: str = Field(min_length=1)
    repo_path: str | None = None
    repo_url: str | None = None
    cache_key: str = Field(min_length=1)
    workspace_path: str | None = None

    @classmethod
    def from_configurable(cls, config: RunnableConfig) -> Self:
        """Build from a LangGraph RunnableConfig, stripping reserved keys."""
        raw = config["configurable"]
        cleaned = {
            k: v
            for k, v in raw.items()
            if k not in _LANGGRAPH_RESERVED_KEYS
            and not k.startswith(_LANGGRAPH_RESERVED_PREFIX)
        }
        return cls.model_validate(cleaned)


class ExecutionContext(WorkflowContext):
    """Context for stages that execute code against a repository."""

    base_branch: str = Field(min_length=1)
    permission_mode: str = Field(min_length=1)
    allowed_tools: list[str]


class RalphLoopContext(ExecutionContext):
    """Context for the quality-gating ralph loop."""

    feature_branch: str = Field(min_length=1)
    ralph_branch: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)


# ---------------------------------------------------------------------------
# TypedDict state schemas (mutable, for LangGraph node communication)
# ---------------------------------------------------------------------------


class TicketGenerationState(TypedDict):
    """State for the ticket generation sub-graph."""

    draft_iteration: int
    review_count: int
    current_draft: TicketDraftOutput | None
    review_feedback: str | None
    review_suggestions: list[str]
    approved: bool
    creator_session_id: str | None
    reviewer_session_id: str | None


class RalphLoopState(TypedDict):
    """State for the quality gating loop (execute -> evaluate -> iterate)."""

    iteration: int
    accepted: bool
    pending_failures: list[CriterionResult]
    last_commit_sha: str | None


class WorkflowState(TypedDict):
    """State for the outer workflow pipeline."""

    feature_branch: str
    ralph_branch: str
    ticket: TicketDraftOutput | None
    acceptance_criteria: list[str]
    accepted: bool
    total_iterations: int
    last_commit_sha: str | None
    merged: bool
    merge_error: str | None
    review_passed: bool
    review_feedback: str | None
    fix_rounds_used: int
    pr_url: str | None
    pr_number: int | None
    ci_passed: bool | None
    ci_summary: str | None
    repo_url: str | None
