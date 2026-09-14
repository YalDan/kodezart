"""Pure gap membership over the tracker's criterion sub-issues.

The tracker owns the criterion-state vocabulary. This module preserves
the actual records; it neither decodes Evidence text nor
invents a second state or grading vocabulary. Supersession references must
already have been established by the owning tracker/lifecycle reader.
"""

from collections.abc import Mapping, Sequence

from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind


def in_gap(criterion: TrackerIssue, *, supersession_ref: str | None) -> bool:
    """Completion closes work; cancellation needs a supersession."""
    if "criterion" not in criterion.issue_labels:
        raise ValueError("gap membership requires a criterion sub-issue")
    if supersession_ref is not None and not supersession_ref.strip():
        raise ValueError("a supersession reference must be nonempty")
    match criterion.state_kind:
        case WorkflowStateKind.COMPLETED:
            return False
        case WorkflowStateKind.CANCELED:
            return supersession_ref is None
        case WorkflowStateKind.DUPLICATE:
            return supersession_ref is None
        case WorkflowStateKind.TRIAGE:
            return True
        case WorkflowStateKind.BACKLOG:
            return True
        case WorkflowStateKind.UNSTARTED:
            return True
        case WorkflowStateKind.STARTED:
            return True


def compute_gap(
    criteria: Sequence[TrackerIssue], *, supersession_refs: Mapping[str, str]
) -> tuple[TrackerIssue, ...]:
    """Retain open criterion records in their supplied order, unchanged."""
    if len({criterion.issue_key for criterion in criteria}) != len(criteria):
        raise ValueError("a criterion identity appears more than once")
    return tuple(
        criterion
        for criterion in criteria
        if in_gap(
            criterion, supersession_ref=supersession_refs.get(criterion.issue_key)
        )
    )
