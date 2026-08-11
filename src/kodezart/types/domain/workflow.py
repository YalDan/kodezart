"""Workflow state definitions for the ralph loop and outer pipeline."""

from typing import NotRequired, Self, TypedDict

from langchain_core.runnables import RunnableConfig
from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.accept import AcceptVerdict, FlaggedItem
from kodezart.types.domain.agent import TicketDraftOutput
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.criteria import (
    CriteriaArtifact,
    CriteriaValidation,
    CriterionFailure,
    GeneratedCriterion,
    ValidatedCriterion,
)
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.trajectory import IterationRecord as IterationRecord
from kodezart.types.domain.trajectory import LoopTrajectory as LoopTrajectory

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
    """Context for stages that execute code against a repository.

    ``base_branch`` is not a field.  It is the recorded base's ref and
    nothing else, so there is no second place a base could enter the run
    and no way for a surface to be handed one baseline while another
    surface uses a different one.
    """

    base_spec: BaseSpec
    permission_mode: str = Field(min_length=1)
    allowed_tools: list[str]

    @property
    def base_branch(self) -> str:
        """The ref every scope surface compares against."""
        return self.base_spec.base_branch


class RalphLoopContext(ExecutionContext):
    """Context for the quality-gating ralph loop."""

    feature_branch: str = Field(min_length=1)
    ralph_branch: str = Field(min_length=1)
    acceptance_criteria: list[ValidatedCriterion] = Field(min_length=1)
    repo_visibility: RepoVisibility


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
    """State for the quality gating loop (execute -> evaluate -> iterate).

    ``iteration_commit_sha`` is NotRequired (PEP 655): on iter 1 it
    starts absent, then is written by ``_execute_node`` every iteration
    with the per-iter commit SHA (or ``None`` on no-change iters).
    ``_evaluate_node`` reads it via ``state.get(...)``.  There is no
    cumulative iteration SHA field — that would clobber the per-iter
    semantic of the SSE event.
    """

    iteration: int
    verdict: AcceptVerdict
    pending_failures: list[CriterionFailure]
    iteration_records: list[IterationRecord]
    iteration_commit_sha: NotRequired[str | None]


class WorkflowState(TypedDict):
    """State for the outer workflow pipeline.

    ``feature_tip_sha`` is the canonical feature-branch tip SHA after the
    last successful consolidation; ``None`` until ``_merge_to_feature_node``
    runs.  ``review_base_sha`` / ``review_head_sha`` are the exact 40-char
    SHAs the evaluator's ``ChangesetDigest`` is computed between — set by
    consolidation nodes, read by ``_review_against_ticket_node``.

    ``trajectory`` carries the most recent quality-gate invocation's
    ``LoopTrajectory``; ``None`` until the first gate invocation projects
    one.
    """

    feature_branch: str
    ralph_branch: str
    ticket: TicketDraftOutput | None
    acceptance_criteria: list[GeneratedCriterion]
    criteria_artifact: CriteriaArtifact | None
    criteria_validation: CriteriaValidation | None
    criteria_regeneration_rounds: int
    criteria_infeasible: bool
    accept_verdict: AcceptVerdict
    flagged_items: list[FlaggedItem]
    total_iterations: int
    feature_tip_sha: str | None
    review_base_sha: str | None
    review_head_sha: str | None
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
    repo_visibility: RepoVisibility
    trajectory: LoopTrajectory | None
