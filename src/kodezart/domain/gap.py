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
    return open_state_kind(criterion.state_kind, supersession_ref=supersession_ref)


def open_state_kind(
    state_kind: WorkflowStateKind, *, supersession_ref: str | None
) -> bool:
    """Whether a record in *state_kind* still owes the work it names.

    The whole of what a workflow kind says about owing, asked of the kind
    alone. A consumer holding a criterion asks it through :func:`in_gap`,
    which adds that record's own preconditions; a consumer holding only the
    kind a reading carried asks it here. One arithmetic, two askings: a
    second reading of what a kind means could answer a lane and a signal
    differently about the same record.
    """
    if supersession_ref is not None and not supersession_ref.strip():
        raise ValueError("a supersession reference must be nonempty")
    match state_kind:
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
