"""The pure scope-terminal arithmetic: the bound, the residual, the outcome.

An organize halt records the bound it exhausted as a settings PATH —
``organize.max_admission_rounds`` — which is how the application reads it,
not how an operator fixed it.  A declared stop is data about the
deployment, so the terminal names the environment variable that carried
the value instead: the same path spelled the way ``AppConfig`` loads it,
with its ``KODEZART_`` prefix and its ``__`` nesting delimiter.

The mapping is total over the settings the halt can name and nothing is
derived by string surgery at the terminal, so a settings field that is
renamed or added shows up as a missing entry here rather than as a
plausible environment name that was never read.

A lane counts toward a converged scope when its own terminal act is
complete: an open pull request whose checks the run actually watched.
What a person later does with that pull request is a human act outside
the loop and is no input to any machine outcome here, which is why this
module reads a lane's pull-request lifecycle and its watched checks and
nothing else about it.
"""

from collections.abc import Mapping, Sequence

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import ScopeTerminalDerivationError
from kodezart.domain.lane_record import LANE_RECORD_PURPOSE
from kodezart.types.domain.ci import CIStatus
from kodezart.types.domain.organize_owner import OrganizeBoundEvidence
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.pr_state import PRLifecycle
from kodezart.types.domain.scope_terminal import (
    LaneReportState,
    ScopeLaneEntry,
    ScopeRecordRef,
    ScopeResidual,
    ScopeResidualClass,
    ScopeResidualItem,
    ScopeResidualOwner,
    ScopeResidualOwnerKind,
    ScopeStoppingRule,
)
from kodezart.types.domain.surface import SurfaceKind

#: Every bound an organize halt can name, against the environment variable
#: ``AppConfig`` loads it from (prefix ``KODEZART_``, nesting ``__``).
BOUND_CONFIG_FIELD: Mapping[str, str] = {
    "organize.max_admission_rounds": "KODEZART_ORGANIZE__MAX_ADMISSION_ROUNDS",
    "organize.max_convergence_rounds": "KODEZART_ORGANIZE__MAX_CONVERGENCE_ROUNDS",
    "write_back.max_verify_rounds": "KODEZART_WRITE_BACK__MAX_VERIFY_ROUNDS",
}


def stopping_rule_of(bound: OrganizeBoundEvidence | None) -> ScopeStoppingRule | None:
    """The stopping rule a halt's bound declares, or nothing for no bound.

    The value and the rounds travel unchanged: the halt already established
    that the run exhausted the bound exactly, and the terminal restates that
    fact rather than recomputing it.
    """
    if bound is None:
        return None
    return ScopeStoppingRule(
        config_field=BOUND_CONFIG_FIELD[bound.setting],
        configured_value=bound.value,
        rounds_used=bound.rounds_used,
    )


#: The act a lane still owes when its terminal act is not complete. The
#: run opens the pull request and watches its checks; what happens to the
#: pull request afterwards is a human act the loop never waits on.
LANE_OPEN_PR_ACT = "Open a pull request for this lane and watch its checks."


def lane_act_complete(entry: ScopeLaneEntry) -> bool:
    """Whether this lane's own terminal act is complete.

    The act is an open pull request whose checks the run watched, so a
    lane carrying no pull request, one no longer open, or one whose checks
    were never monitored still owes work.  A lane's recorded outcome is
    not consulted: what the fire ended as is a separate fact from whether
    the lane left its work open for review.
    """
    return (
        entry.pr is not None
        and entry.pr.state == PRLifecycle.OPEN
        and entry.checks is not CIStatus.not_monitored
    )


def lane_residual(
    lanes: Sequence[ScopeLaneEntry], *, marker_prefixes: Mapping[str, str]
) -> tuple[ScopeResidualItem, ...]:
    """One ``LANE_WITHOUT_OPEN_PR`` item per reported lane still owing its act.

    A silent lane is not one of these: silence is its own class, recorded
    by the caller that knows the roster.  Each item is recorded against
    the lane's own branch-state record — the marker-keyed comment the lane
    already wrote on its issue — because a lane that never opened a pull
    request has no deliverable ref for the item to point at.
    """
    return tuple(
        ScopeResidualItem(
            issue_id=entry.issue_id,
            residual_class=ScopeResidualClass.LANE_WITHOUT_OPEN_PR,
            record=ScopeRecordRef(
                kind=SurfaceKind.MARKER_COMMENT,
                issue_key=entry.issue_id,
                marker=compose_comment_marker(
                    prefixes=marker_prefixes,
                    purpose=LANE_RECORD_PURPOSE,
                    lane=entry.lane_key,
                ),
            ),
            detail=entry.lane_key,
            act=LANE_OPEN_PR_ACT,
            owner=ScopeResidualOwner(
                kind=ScopeResidualOwnerKind.THIS_LANE, key=entry.lane_key
            ),
        )
        for entry in lanes
        if entry.report_state is not LaneReportState.UNREPORTED
        and not lane_act_complete(entry)
    )


#: The reported states that say a lane stopped before finishing its work.
_UNFINISHED_REPORT_STATES: frozenset[LaneReportState] = frozenset(
    {LaneReportState.IN_GAP, LaneReportState.HALTED},
)


def derive_scope_outcome(
    *,
    lanes: Sequence[ScopeLaneEntry],
    residual: ScopeResidual,
    stopping_rule: ScopeStoppingRule | None,
) -> WorkflowOutcome:
    """The one terminal judgment of a scope, in a fixed order of questions.

    Silence is read first, because a scope that never heard from a lane
    knows nothing about it; then a declared stop, which is by construction
    a scope that finished owing the work the bound cut off; then a lane
    that stopped inside its own loop; then a record the terminal could not
    read; and only then the residual itself.

    A declared stop owing nothing is not a terminal this arithmetic can
    state, so it raises rather than inventing a convergence the bound
    contradicts.
    """
    if any(entry.report_state is LaneReportState.UNREPORTED for entry in lanes):
        return WorkflowOutcome.scope_stopped_short
    if stopping_rule is not None:
        if not residual.items:
            raise ScopeTerminalDerivationError(
                config_field=stopping_rule.config_field,
                reason="a declared stop leaves work owed, and none is recorded",
            )
        return WorkflowOutcome.scope_converged_with_residual
    if any(entry.report_state in _UNFINISHED_REPORT_STATES for entry in lanes):
        return WorkflowOutcome.scope_stopped_short
    if residual.blocking:
        return WorkflowOutcome.scope_stopped_short
    if not residual.items:
        return WorkflowOutcome.scope_converged
    return WorkflowOutcome.scope_converged_with_residual
