"""Pure source checks for a native criterion's authorized amendment."""

from kodezart.domain.errors import CriterionReadError
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind


def require_criterion_source(
    *, expected: TrackerIssue, current: TrackerIssue, pending_replay: bool = False
) -> None:
    """Preserve every source fact except comment churn and an allowed reset replay."""
    if "criterion" not in current.issue_labels or current.parent_key is None:
        raise CriterionReadError(
            issue_key=expected.issue_key,
            reason="the native criterion lost its classification or parent",
        )
    ignored = {"updated_at"}
    if pending_replay and current.state_kind is WorkflowStateKind.UNSTARTED:
        ignored |= {"state_kind", "state_name"}
    if expected.model_dump(exclude=ignored) != current.model_dump(exclude=ignored):
        raise CriterionReadError(
            issue_key=expected.issue_key,
            reason="native criterion facts changed before amendment",
        )
