"""Pure gap membership over the tracker's criterion sub-issues.

The tracker owns the criterion-state vocabulary. This module preserves
the actual records; it neither decodes Evidence text nor
invents a second state or grading vocabulary. Canceled and Duplicate
criteria are excluded on state alone and named beside the gap (KOD-794).
"""

from collections.abc import Sequence

from kodezart.types.domain.gap import CriterionGap, GapMembership
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind


def gap_membership(criterion: TrackerIssue) -> GapMembership:
    """What one criterion record contributes, from its own state alone."""
    if "criterion" not in criterion.issue_labels:
        raise ValueError("gap membership requires a criterion sub-issue")
    match criterion.state_kind:
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
