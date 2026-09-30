"""Pure gap membership over the tracker's criterion sub-issues.

The tracker owns the criterion-state vocabulary. This module preserves
the actual records; it neither decodes Evidence text nor
invents a second state or grading vocabulary. Canceled and Duplicate
criteria are excluded on state alone and named beside the gap (KOD-794).
"""

from collections.abc import Sequence

from kodezart.types.domain.gap import CriterionGap, GapMembership
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind


def state_membership(state_kind: WorkflowStateKind) -> GapMembership:
    """What a record in *state_kind* contributes to the gap, from the kind alone.

    The one reading of what a workflow kind owes. A consumer holding a
    criterion asks it through :func:`gap_membership`, which adds that
    record's own precondition; a consumer holding only the kind a reading
    carried asks it here or through :func:`open_state_kind`. One arithmetic,
    several askings: a second reading of what a kind means could answer a
    lane and a signal differently about the same record.
    """
    match state_kind:
        case WorkflowStateKind.COMPLETED:
            return GapMembership.DISCHARGED
        case WorkflowStateKind.CANCELED:
            return GapMembership.EXCLUDED
        case WorkflowStateKind.DUPLICATE:
            return GapMembership.EXCLUDED
        case WorkflowStateKind.TRIAGE:
            return GapMembership.OWED
        case WorkflowStateKind.BACKLOG:
            return GapMembership.OWED
        case WorkflowStateKind.UNSTARTED:
            return GapMembership.OWED
        case WorkflowStateKind.STARTED:
            return GapMembership.OWED


def gap_membership(criterion: TrackerIssue) -> GapMembership:
    """What one criterion record contributes, from its own state alone."""
    if "criterion" not in criterion.issue_labels:
        raise ValueError("gap membership requires a criterion sub-issue")
    return state_membership(criterion.state_kind)


def open_state_kind(state_kind: WorkflowStateKind) -> bool:
    """Whether a record in *state_kind* still owes the work it names.

    The kind-level asking of :func:`state_membership`, for a consumer that
    holds only the kind a reading carried: owed and nothing else is open.
    """
    return state_membership(state_kind) is GapMembership.OWED


def compute_gap(criteria: Sequence[TrackerIssue]) -> CriterionGap:
    """Retain open criterion records in their supplied order, unchanged."""
    if len({criterion.issue_key for criterion in criteria}) != len(criteria):
        raise ValueError("a criterion identity appears more than once")
    memberships = [(criterion, gap_membership(criterion)) for criterion in criteria]
    return CriterionGap(
        owed=tuple(
            criterion
            for criterion, membership in memberships
            if membership is GapMembership.OWED
        ),
        excluded=tuple(
            criterion.issue_key
            for criterion, membership in memberships
            if membership is GapMembership.EXCLUDED
        ),
    )
