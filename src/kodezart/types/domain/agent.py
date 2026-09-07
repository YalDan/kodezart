"""Agent event domain models for SSE streaming."""

from enum import StrEnum
from typing import Literal

from pydantic import (
    ConfigDict,
    Field,
    field_validator,
)

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.accept import AcceptVerdict, SherlockFlag
from kodezart.types.domain.branch import BaseInput, WorkRefRole
from kodezart.types.domain.ci import CIStatus
from kodezart.types.domain.consolidation import ConsolidationStatus
from kodezart.types.domain.criteria import (
    CRITERION_ID_PATTERN,
    ContractCorrection,
    CriteriaValidation,
    CriteriaValidationOutput,
    CriterionId,
    DraftedCriterion,
    FanInReport,
    GeneratedCriterion,
)
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.persist import ArtifactPersistStatus
from kodezart.types.domain.remediation import RemediationEntry
from kodezart.types.domain.ticket_review import TicketApproval, TicketReviewMode
from kodezart.types.domain.trajectory import LoopTrajectory

# ---------------------------------------------------------------------------
# Soft-failure raise-site identifier (typed alias)
# ---------------------------------------------------------------------------
#
# ``RaiseSite`` is defined here — the upstream end of the dependency
# chain — so that both ``types/domain/agent.ErrorEvent`` and
# ``core/errors.NoStructuredOutputError`` can refer to the SAME typed
# alias.  ``core/errors.py`` imports ``RaiseSite`` from this
# module; ``ErrorEvent.raise_site`` references it directly.  Drift
# between two parallel ``Literal`` lists is structurally impossible.

RaiseSite = Literal[
    "ticket_creator",
    "ticket_reviewer",
    "branch_name",
    "acceptance_criteria",
    "criteria_validation",
    "ralph_evaluator",
    "post_merge_review",
    "pr_description",
    "commit_message",
    "remediation_ticket",
    "content_audit",
]

# ---------------------------------------------------------------------------
# Ticket-generation structured outputs
# ---------------------------------------------------------------------------


class CodeReference(CamelCaseModel):
    """A reference to a specific location in the codebase."""

    location: str = Field(
        min_length=1,
        description=(
            "Where in the repository this points: a file path, optionally "
            "with a line range or a symbol name."
        ),
    )
    note: str = Field(
        min_length=1,
        description="What the implementer needs to know about this location.",
    )


class FileChange(CamelCaseModel):
    """A single file-level change required to implement the ticket."""

    file_path: str = Field(
        min_length=1,
        description="Repository-relative path of the file this change touches.",
    )
    change_type: Literal["create", "modify", "delete"] = Field(
        description="Whether the file is created, modified or deleted.",
    )
    description: str = Field(
        min_length=1,
        description="What changes in this file, stated as an observable outcome.",
    )
    rationale: str = Field(
        min_length=1,
        description="Why this change is required by the task, not merely useful.",
    )


class CritiqueSeverity(StrEnum):
    """How much a critique finding costs if it ships unaddressed."""

    BLOCKING = "blocking"
    SIGNIFICANT = "significant"
    MINOR = "minor"


class CritiqueFinding(CamelCaseModel):
    """One defect the fresh-context critic found in a drafted artifact."""

    quoted_passage: str = Field(
        min_length=1,
        description=(
            "The offending passage, quoted from the draft so the reader can "
            "locate it without re-deriving the finding."
        ),
    )
    defect: str = Field(
        min_length=1,
        description="What is wrong with that passage, stated as a defect.",
    )
    severity: CritiqueSeverity = Field(
        description=(
            "blocking: the draft cannot be acted on. significant: it can, but "
            "the result will miss the task. minor: everything else."
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How sure you are the defect is real, from 0 to 1. Report low "
            "confidence rather than filtering — routing downstream decides."
        ),
    )


class CritiqueFlag(CamelCaseModel):
    """One thing the critic flags for a supervisor's attention.

    The channel the persona rewrite had to preserve: a finding about WHY a
    component exists rather than whether it works — over-implementation,
    machinery no measured failure justifies, a goal quietly substituted.
    It is a typed, routable item because a flag that cannot be routed is a
    persona artefact with a new name.

    Distinct from the accept gate's ``SherlockFlag``, which anchors to a
    criterion id: this one anchors to a passage of a drafted artifact,
    which has no criteria yet.  Same wire field name on two producers,
    two anchors, one definition each.
    """

    subject: str = Field(
        min_length=1,
        description=(
            "What is flagged: the component, claim, or decision the "
            "supervisor should look at, named precisely enough to find."
        ),
    )
    reason: str = Field(
        min_length=1,
        description=(
            "Why it is flagged: the measured failure that would justify it "
            "and is absent, or the goal it serves that the task never asked "
            "for."
        ),
    )


class DraftCritiqueOutput(CamelCaseModel):
    """The draft-critic lens's whole verdict on one drafted artifact."""

    sound: bool = Field(
        description=(
            "Whether the draft satisfies the task as it stands: true only "
            "when no blocking or significant finding remains."
        ),
    )
    findings: list[CritiqueFinding] = Field(
        default_factory=list,
        description=(
            "Every defect found, unfiltered by severity or confidence; "
            "empty when the draft is sound."
        ),
    )
    sherlock_flags: list[CritiqueFlag] = Field(
        default_factory=list,
        description=(
            "Findings about why the draft's parts exist rather than whether "
            "they work; empty when nothing warrants a supervisor's attention."
        ),
    )


class TicketDraftOutput(CamelCaseModel):
    """Structured output for a generated implementation ticket.

    The downstream generate_criteria agent has its own job — it derives
    observable acceptance criteria from the full formatted ticket.
    """

    title: str = Field(
        min_length=1,
        max_length=120,
        description="One line naming what the ticket delivers.",
    )
    summary: str = Field(
        min_length=1,
        description=(
            "What the change accomplishes, for a reader with no other context."
        ),
    )
    context: str = Field(
        min_length=1,
        description=(
            "The repository facts the implementation rests on, including the "
            "documentation sources consulted."
        ),
    )
    references: list[CodeReference] = Field(
        default_factory=list,
        description="Locations in the repository the implementer starts from.",
    )
    required_changes: list[FileChange] = Field(
        min_length=1,
        description="Every file the change must touch, one entry per file.",
    )
    out_of_scope: list[str] = Field(
        default_factory=list,
        description="Work this ticket deliberately excludes.",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Questions the ticket could not settle from the repository.",
    )
    sherlock_flags: list[CritiqueFlag] = Field(
        default_factory=list,
        description=(
            "Every flag the draft critic raised about why a part of this "
            "ticket exists rather than whether it works, carried out "
            "unchanged; empty when none was raised."
        ),
    )


class TicketReviewOutput(CamelCaseModel):
    """Structured output for a ticket review."""

    approved: bool = Field(
        description=(
            "Whether the draft is fit to implement: false while any blocking "
            "or significant finding remains."
        ),
    )
    feedback: str = Field(
        min_length=1,
        description="Every finding, with its severity and your confidence in it.",
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="Concrete revisions that would resolve the findings.",
    )


class AgentEvent(CamelCaseModel):
    """Base SSE event. ``type`` discriminates variants."""

    type: str


class TaskUsageInfo(CamelCaseModel):
    """Token usage and timing metadata for a sub-task."""

    total_tokens: int
    tool_uses: int
    duration_ms: int


class UserMessageEvent(AgentEvent):
    """User message echoed back in the SSE stream."""

    type: Literal["user_message"] = "user_message"
    content: str

    @field_validator("content", mode="before")
    @classmethod
    def _coerce_to_str(cls, v: object) -> str:
        return v if isinstance(v, str) else str(v)


class AssistantTextEvent(AgentEvent):
    """Text content generated by Claude."""

    type: Literal["assistant_text"] = "assistant_text"
    text: str
    model: str


class AssistantThinkingEvent(AgentEvent):
    """Extended thinking content from Claude."""

    type: Literal["assistant_thinking"] = "assistant_thinking"
    thinking: str
    model: str


class ToolUseEvent(AgentEvent):
    """Agent requesting a tool invocation."""

    type: Literal["tool_use"] = "tool_use"
    name: str
    input: dict[str, object]
    id: str
    model: str


class ToolResultEvent(AgentEvent):
    """Result returned from a tool invocation."""

    type: Literal["tool_result"] = "tool_result"
    content: str | list[dict[str, object]] | None = None
    tool_use_id: str
    is_error: bool | None = None


class SystemEvent(AgentEvent):
    """System-level event from the Claude SDK.

    The session's opening frame is the one that reports what the session
    actually loaded — the engine id among its ``data``, and beside it the
    output style its system prompt runs under.  ``output_style`` is that
    reported value, and it is ``None`` on every other subtype, which
    knows nothing about one.
    """

    type: Literal["system"] = "system"
    subtype: str
    data: dict[str, object]
    output_style: str | None = None


class TaskStartedEvent(AgentEvent):
    """Sub-task spawned by the agent."""

    type: Literal["task_started"] = "task_started"
    subtype: str
    task_id: str
    description: str
    uuid: str
    session_id: str
    tool_use_id: str | None = None
    task_type: str | None = None
    data: dict[str, object]


class TaskProgressEvent(AgentEvent):
    """Progress update from a running sub-task."""

    type: Literal["task_progress"] = "task_progress"
    subtype: str
    task_id: str
    description: str
    usage: TaskUsageInfo
    uuid: str
    session_id: str
    tool_use_id: str | None = None
    last_tool_name: str | None = None
    data: dict[str, object]


class TaskUpdatedEvent(AgentEvent):
    """Lifecycle state change of a background sub-task.

    Distinct from ``task_notification`` because it is not interchangeable
    with it: the SDK documents that a background task's terminal state
    can arrive ONLY here, with the matching notification suppressed — a
    task stopped externally reports ``killed`` on this message and
    nothing else.  ``terminal`` is resolved at the adapter boundary
    against the SDK's own terminal-status set, which spans both message
    vocabularies, so a consumer tracking task ids clears them from
    either message without importing the vendor's constant.
    """

    type: Literal["task_updated"] = "task_updated"
    subtype: str
    task_id: str
    status: str | None = None
    terminal: bool
    patch: dict[str, object]
    uuid: str | None = None
    session_id: str | None = None
    data: dict[str, object]


class TaskNotificationEvent(AgentEvent):
    """Completion notification from a sub-task."""

    type: Literal["task_notification"] = "task_notification"
    subtype: str
    task_id: str
    status: Literal["completed", "failed", "stopped"]
    output_file: str
    summary: str
    uuid: str
    session_id: str
    tool_use_id: str | None = None
    usage: TaskUsageInfo | None = None
    data: dict[str, object]


class ResultEvent(AgentEvent):
    """Terminal event with metrics, session ID, and output."""

    type: Literal["result"] = "result"
    subtype: str
    duration_ms: int
    duration_api_ms: int
    is_error: bool
    num_turns: int
    session_id: str
    stop_reason: str | None = None
    total_cost_usd: float | None = None
    usage: dict[str, object] | None = None
    result: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    structured_output: dict[str, object] | None = None


class StreamDataEvent(AgentEvent):
    """Raw SDK stream event forwarded for transparency."""

    type: Literal["stream_event"] = "stream_event"
    session_id: str
    event: dict[str, object]


class ErrorEvent(AgentEvent):
    """Error event emitted on agent or workspace failure.

    Wire shape locked with ``extra="forbid"`` — a future field-name
    typo on the producer side raises at validation time rather than
    silently breaking downstream SSE consumers.

    The typed optional fields below promote the genuinely consumed
    observability slots to top-level named attributes, replacing the
    previous draft's ``details: dict[str, object]`` bag.  Each is
    independently type-checkable by downstream consumers.

    The ``raise_site`` field references the ``RaiseSite`` typed alias
    defined above in this module.  ``core/errors.py`` imports
    the SAME alias, so the eight-literal enumeration has exactly one
    authoritative definition and any future addition is enforced
    everywhere by ``mypy``.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["error"] = "error"
    error: str
    error_kind: str | None = None
    cause_class: str | None = None
    stop_reason: str | None = None
    raise_site: RaiseSite | None = None
    rate_limit_rejected: bool | None = None
    exit_code: int | None = None
    stderr_tail: str | None = None
    # The soft-failure variant slots.  Two recorded deaths reported the
    # same wire event for two different failures — no result event at
    # all, versus a result carrying no structured output — and the
    # result text held the answer in plain words.  A reader holding only
    # this frame can now tell them apart.
    result_event_observed: bool | None = None
    subtype: str | None = None
    num_turns: int | None = None
    duration_ms: int | None = None
    result_tail: str | None = None


class RateLimitWarningEvent(AgentEvent):
    """Emitted when the Claude SDK reports a rate-limit warning or rejection."""

    type: Literal["rate_limit_warning"] = "rate_limit_warning"
    status: Literal["allowed_warning", "rejected"]
    resets_at: int | None = None
    utilization: float | None = None
    rate_limit_type: str | None = None


class CommitMessageOutput(CamelCaseModel):
    """Structured output schema for agent-generated commit messages."""

    title: str = Field(
        min_length=1,
        max_length=72,
        description=(
            "Conventional-commit summary line saying what the change does, "
            "in the imperative."
        ),
    )
    body: str = Field(
        default="",
        description="Why the change was made; empty when the why is obvious.",
    )


class CriterionResult(CamelCaseModel):
    """Per-criterion evaluation result, keyed by the harness's stable id.

    ``criterion`` is two different things on the two directions this model
    travels, and only one of them has a defence.

    OUTBOUND it carries the HARNESS's text, looked up by id:
    ``grade_iteration`` overwrites the field on both the answered and the
    unanswered path, and the result reaches a human in the
    ``workflow_iteration`` and ``workflow_review`` SSE frames, where a
    reader sees which criterion a verdict is about without joining the
    frame against the criteria set.  Measured on the handler's own
    ``model_dump(by_alias=True, exclude_none=True)``.

    INBOUND it is the evaluator's ECHO, and that has no defence: nothing
    ever reads it, so requiring it costs a full reproduction of every
    criterion's text on every iteration and every post-merge review and
    buys nothing.  Dropping the inbound obligation without dropping the
    outbound field means splitting this model in two; KOD-91 deliverable 3
    is already rewriting these field descriptions and removes it there,
    with the two prompt lines that demand it.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    criterion_id: CriterionId = Field(
        pattern=CRITERION_ID_PATTERN,
        description=(
            "The dispatched criterion's id, echoed exactly. Return one result "
            "per dispatched id and invent none."
        ),
    )
    criterion: str = Field(
        min_length=1,
        description="The criterion's text as dispatched.",
    )
    passed: bool = Field(
        description=(
            "Whether the changeset satisfies this criterion. Insufficient "
            "evidence is a fail."
        ),
    )
    reasoning: str = Field(
        min_length=1,
        description=(
            "The evidence for the verdict, citing output you ran: file paths "
            "with line numbers, test names, lint rule identifiers."
        ),
    )


class AcceptanceCriteriaOutput(CamelCaseModel):
    """Structured output for acceptance criteria evaluation.

    ``sherlock_flags`` carries the synthesis's own concerns as data.  A
    reasoning error the evaluator noticed but could only write into prose
    is invisible to every consumer downstream of it; as a typed field it
    rides the iteration event and reaches the pull-request body.
    """

    criteria_results: list[CriterionResult] = Field(
        min_length=1,
        description=(
            "Exactly one result per dispatched criterion id, covering every id."
        ),
    )
    sherlock_flags: list[SherlockFlag] = Field(
        default_factory=list,
        description=(
            "Reasoning concerns raised in your own name rather than against "
            "one criterion's verdict."
        ),
    )


class BranchNameOutput(CamelCaseModel):
    """Agent-generated branch name slug."""

    slug: str = Field(
        min_length=1,
        max_length=50,
        description=(
            "Lowercase hyphen-separated branch slug saying what the change "
            "does. No prefix."
        ),
    )


class ContentAuditFinding(CamelCaseModel):
    """One finding from the judgment scanner's audit session.

    ``start``/``end`` are absent when the finding localizes to no span —
    "this paragraph implies an unreleased capability" has nothing to
    excise, and the gate blocks rather than redacting such a finding.
    """

    start: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Character offset where the leaking span starts, counted from the "
            "start of the payload. Absent when the leak is carried by a "
            "passage rather than a substring."
        ),
    )
    end: int | None = Field(
        default=None,
        ge=0,
        description="Character offset just past the leaking span, on the same terms.",
    )
    rationale: str = Field(
        min_length=1,
        description=(
            "One sentence saying what a stranger would learn, and why it matters."
        ),
    )


class ContentAuditOutput(CamelCaseModel):
    """The audit session's whole verdict: every finding, or none."""

    findings: list[ContentAuditFinding] = Field(
        default_factory=list,
        description=(
            "One finding per distinct thing a stranger would learn; empty when none."
        ),
    )


class GeneratedCriteriaOutput(CamelCaseModel):
    """Agent-generated acceptance criteria from ticket + codebase analysis.

    Identity is NOT part of this shape — ``AC-n`` ids are minted
    harness-side from emission order, so nothing a model echoes can
    renumber a criterion WITHIN one dispatch.

    The guarantee stops at the dispatch boundary and does not hold across
    a regeneration round: ``mint_criteria`` always enumerates from 1 and
    ``DraftedCriterion`` carries no id, so a regenerator cannot return an
    identity even in principle.  Round two's ``AC-3`` is whatever landed
    third, not round one's ``AC-3``.
    """

    criteria: list[DraftedCriterion] = Field(
        min_length=1,
        description=(
            "The smallest set of falsifiable checks that, all passing, "
            "establish the ticket is done."
        ),
    )
    reasoning: str = Field(
        min_length=1,
        description="How the set was derived from the ticket and the repository.",
    )


class PRDescriptionOutput(CamelCaseModel):
    """Structured output for agent-generated PR descriptions."""

    title: str = Field(
        min_length=1,
        max_length=120,
        description="Pull-request title: what changed, in one line.",
    )
    description: str = Field(
        min_length=1,
        description=(
            "Pull-request body: what changed and why, how each acceptance "
            "criterion is met, and what a reviewer should scrutinize first."
        ),
    )


class WorkflowReviewEvent(AgentEvent):
    """Emitted after post-merge review against ticket criteria."""

    type: Literal["workflow_review"] = "workflow_review"
    passed: bool
    evaluation: AcceptanceCriteriaOutput
    fix_rounds_used: int
    fan_in: FanInReport | None = None


class WorkflowPREvent(AgentEvent):
    """Emitted after a pull request is opened.

    ``feature_tip_sha`` is the pushed tip the pull request is opened over.
    It rides here because this event is what the lifecycle write-back reads
    to record the issue's DELIVERABLE work ref, and a ref without the sha
    it was pushed at cannot serve as a dependent lane's base (KOD-149).

    ``delivered`` is REQUIRED and the producer states it, because two nodes
    open pull requests and they mean opposite things.  The accepted path's
    pull request is what the run delivered; the stall exit's carries the
    best iteration of a run its own acceptance gate rejected, opened so a
    human can read what was reached.  A work ref is at-most-one and nothing
    can delete it, so a rejected branch recorded as THE deliverable is the
    base every dependent lane resolves to from then on — which is why this
    is stated at the producer rather than guessed at downstream.
    """

    type: Literal["workflow_pr"] = "workflow_pr"
    pr_url: str
    pr_number: int
    feature_branch: str
    base_branch: str
    feature_tip_sha: str = Field(min_length=40, max_length=40)
    delivered: bool


class WorkflowCIEvent(AgentEvent):
    """Emitted after CI monitoring completes or is skipped.

    ``ci_status`` is a required enum, so it serializes on every frame
    without a wrap serializer forcing the key back in — which is what a
    nullable tri-state bool needed under ``exclude_none=True``.
    """

    type: Literal["workflow_ci"] = "workflow_ci"
    ci_status: CIStatus
    summary: str
    ref: str


class WorkflowIterationEvent(AgentEvent):
    """Emitted after each ralph loop iteration.

    ``trajectory`` is the loop's progress memory folded over every
    iteration so far.  It is required — the loop→workflow seam carries
    it on this existing channel rather than on a second event type.

    ``verdict`` is three-state.  It replaced a boolean ``accepted``:
    a run whose only failures are soft signals ships AND has something to
    say, and no boolean could carry both.
    """

    type: Literal["workflow_iteration"] = "workflow_iteration"
    iteration: int
    branch: str
    commit_sha: str | None = None
    verdict: AcceptVerdict
    evaluation: AcceptanceCriteriaOutput
    trajectory: LoopTrajectory
    fan_in: FanInReport | None = None


class WorkflowConsolidationEvent(AgentEvent):
    """Emitted after every `BranchMerger.consolidate(...)` call.

    Reports the four-way outcome (``ALREADY_INTEGRATED``,
    ``FAST_FORWARDED``, ``DIVERGENT``, ``SOURCE_MISSING``) without
    routing semantics — routing lives in the workflow graph's
    conditional edges, not on this event.  No ``phase`` field: both
    emission sites (post-loop and post-fix) are distinguished by
    event ordering relative to ``WorkflowReviewEvent``.
    """

    type: Literal["workflow_consolidation"] = "workflow_consolidation"
    status: ConsolidationStatus
    feature_branch: str = Field(min_length=1)
    source_branch: str = Field(min_length=1)
    feature_tip_sha: str = Field(min_length=40, max_length=40)


class WorkflowRemediationEvent(AgentEvent):
    """Emitted once per remediation round, when its ticket exists.

    ``entry`` is on the event because three failure routes reach one
    component: without it a reader watching the stream could not tell
    which failure the round is answering, and the round would look the
    same whichever way the run got there.
    """

    type: Literal["workflow_remediation"] = "workflow_remediation"
    entry: RemediationEntry
    round_index: int
    ticket: TicketDraftOutput
    base_ref: str


class WorkflowArtifactsEvent(AgentEvent):
    """Emitted after workflow artifacts are written to the ralph branch.

    ``IGNORED_BY_TARGET`` is not a variant of success: the target
    repository's ignore rules match the artifact directory, so no run
    will ever land artifacts there until they change.
    """

    type: Literal["workflow_artifacts"] = "workflow_artifacts"
    status: ArtifactPersistStatus
    branch: str = Field(min_length=1)


class WorkflowCompleteEvent(AgentEvent):
    """Emitted when the ralph loop finishes.

    ``outcome`` is the sole terminal discriminator — required and
    non-nullable, so ``exclude_none=True`` can never drop it and no
    serializer hack is needed to force it onto the wire.  ``ci_status``
    now holds on the same ground, and ``merge_error`` says what its
    string actually carries: the merge failure, never a general error
    channel.
    """

    type: Literal["workflow_complete"] = "workflow_complete"
    feature_branch: str
    ralph_branch: str
    total_iterations: int
    accepted: bool
    outcome: WorkflowOutcome
    merged: bool = False
    final_commit_sha: str | None = None
    merge_error: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    ci_status: CIStatus = CIStatus.not_monitored
    trajectory: LoopTrajectory | None = None
    criteria_validation: CriteriaValidation | None = None


class WorkflowVisibilityEvent(AgentEvent):
    """Emitted once per run when repository visibility is resolved."""

    type: Literal["workflow_visibility"] = "workflow_visibility"
    visibility: RepoVisibility
    repo_url: str | None = None


class WorkflowScopeBaseEvent(AgentEvent):
    """Emitted once per run: the ref every scope comparison is made against.

    A maintainer reading a run's events can confirm which ref the scope
    and no-touch checks compared against, and — on a constructed base —
    which blockers it was built from.  Without this the baseline is only
    visible by inference from a diff, which is exactly how a wrong one
    went unnoticed.
    """

    type: Literal["workflow_scope_base"] = "workflow_scope_base"
    base_branch: str
    base_role: WorkRefRole | None
    inputs: list[BaseInput] = Field(default_factory=list)


class JobAcceptedEvent(AgentEvent):
    """Leading frame of an attached run — carries the reconnect handle.

    A client that drops mid-run reconnects at ``stream_url`` with this
    ``job_id`` instead of losing the run.
    """

    type: Literal["job_accepted"] = "job_accepted"
    job_id: str
    lane: str
    queue_position: int
    status_url: str
    stream_url: str


class WorkflowCriteriaEvent(AgentEvent):
    """Emitted after acceptance criteria are generated from ticket analysis."""

    type: Literal["workflow_criteria"] = "workflow_criteria"
    criteria: list[GeneratedCriterion]
    reasoning: str


class WorkflowCriteriaValidationEvent(AgentEvent):
    """Emitted after every feasibility sweep over a generated criteria set.

    ``correction`` is present only when the validator's first answer was
    refused and the node argued with it under the re-dispatch bound, so
    absent means the first response conformed.
    """

    type: Literal["workflow_criteria_validation"] = "workflow_criteria_validation"
    regeneration_round: int
    validation: CriteriaValidation
    regeneration_targets: list[str]
    correction: ContractCorrection | None = None


class WorkflowTicketDraftEvent(AgentEvent):
    """Emitted after each ticket draft iteration."""

    type: Literal["workflow_ticket_draft"] = "workflow_ticket_draft"
    iteration: int
    draft: TicketDraftOutput


class WorkflowTicketReviewEvent(AgentEvent):
    """Emitted after each ticket review iteration."""

    type: Literal["workflow_ticket_review"] = "workflow_ticket_review"
    iteration: int
    approved: bool
    feedback: str
    suggestions: list[str]


class WorkflowTicketEvent(AgentEvent):
    """Emitted when ticket generation finishes.

    ``approved`` and ``mode`` ride together because either alone is
    ambiguous: ``not_reviewed`` says no reviewer ran and the mode says why
    nobody expected one to.
    """

    type: Literal["workflow_ticket"] = "workflow_ticket"
    ticket: TicketDraftOutput
    review_rounds: int
    approved: TicketApproval
    mode: TicketReviewMode


# Pre-computed WIRE schemas for structured agent output via output_format.
# Each is the model's OWN schema: the contract the model is shown is the
# contract its response is judged against, constraints included.
COMMIT_MESSAGE_SCHEMA: dict[str, object] = CommitMessageOutput.model_json_schema()
# Schema for acceptance criteria evaluation results
ACCEPTANCE_CRITERIA_SCHEMA: dict[str, object] = (
    AcceptanceCriteriaOutput.model_json_schema()
)
# Schema for agent-generated branch name slugs
BRANCH_NAME_SCHEMA: dict[str, object] = BranchNameOutput.model_json_schema()
# Schema for agent-generated acceptance criteria from ticket analysis
GENERATED_CRITERIA_SCHEMA: dict[str, object] = (
    GeneratedCriteriaOutput.model_json_schema()
)
# Schema for the feasibility sweep's per-criterion findings
CRITERIA_VALIDATION_SCHEMA: dict[str, object] = (
    CriteriaValidationOutput.model_json_schema()
)
# Schema for structured ticket draft output
TICKET_DRAFT_SCHEMA: dict[str, object] = TicketDraftOutput.model_json_schema()
# Schema for structured ticket review output
TICKET_REVIEW_SCHEMA: dict[str, object] = TicketReviewOutput.model_json_schema()
PR_DESCRIPTION_SCHEMA: dict[str, object] = PRDescriptionOutput.model_json_schema()
# Schema for the judgment scanner's structured audit verdict
CONTENT_AUDIT_SCHEMA: dict[str, object] = ContentAuditOutput.model_json_schema()
# Schema for the draft-critic lens's verdict on a drafted artifact
DRAFT_CRITIQUE_SCHEMA: dict[str, object] = DraftCritiqueOutput.model_json_schema()

#: Every wire schema this system dispatches, by constant name. The
#: wire-contract tests and the dispatch-site guard both read this rather
#: than keeping their own list.
WIRE_SCHEMAS: dict[str, dict[str, object]] = {
    "COMMIT_MESSAGE_SCHEMA": COMMIT_MESSAGE_SCHEMA,
    "ACCEPTANCE_CRITERIA_SCHEMA": ACCEPTANCE_CRITERIA_SCHEMA,
    "BRANCH_NAME_SCHEMA": BRANCH_NAME_SCHEMA,
    "GENERATED_CRITERIA_SCHEMA": GENERATED_CRITERIA_SCHEMA,
    "CRITERIA_VALIDATION_SCHEMA": CRITERIA_VALIDATION_SCHEMA,
    "TICKET_DRAFT_SCHEMA": TICKET_DRAFT_SCHEMA,
    "TICKET_REVIEW_SCHEMA": TICKET_REVIEW_SCHEMA,
    "PR_DESCRIPTION_SCHEMA": PR_DESCRIPTION_SCHEMA,
    "CONTENT_AUDIT_SCHEMA": CONTENT_AUDIT_SCHEMA,
    "DRAFT_CRITIQUE_SCHEMA": DRAFT_CRITIQUE_SCHEMA,
}
