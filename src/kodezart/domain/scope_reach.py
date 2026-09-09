"""What a scope's container filter cannot reach, over the one arithmetic.

A member's gap is everything its subtree still owes, read through the
subtree and not through the filtered member set. Some of what it owes can
sit outside the filter the walk was addressed with — another project, or
off-milestone under a milestone reference — and stays the member's work
either way. This names those, so the filter changes what the run can reach
and never what the lane owes.
"""

from collections.abc import Mapping, Sequence
from typing import assert_never

from kodezart.types.domain.dispatch import UnreachableCriterion
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadyLane
from kodezart.types.domain.tracker import TrackerIssue


def _container_key(issue: TrackerIssue, kind: ScopeKind) -> str | None:
    """The issue's own value on the dimension the filter selects by."""
    match kind:
        case ScopeKind.PROJECT | ScopeKind.INITIATIVE:
            return issue.project_id
        case ScopeKind.MILESTONE:
            return issue.milestone_key
        case ScopeKind.ISSUE:
            return issue.parent_key
        case _:
            assert_never(kind)


def unreachable_criteria(
    *,
    ref: ScopeRef,
    members: Mapping[str, TrackerIssue],
    lanes: Sequence[ScopeReadyLane],
) -> tuple[UnreachableCriterion, ...]:
    """Every open criterion in a lane's gap the scope's members do not carry.

    Membership is the filter as the walk resolved it, so this asks the one
    question that matters to the run: can the scope address this criterion
    on its own, or only through the lane whose subtree owes it. An issue
    scope carries its whole subtree by construction and answers with
    nothing.
    """
    return tuple(
        UnreachableCriterion(
            issue_key=criterion.issue_key,
            lane_key=lane.issue.issue_key,
            filter_kind=ref.kind,
            filter_key=ref.key,
            container_key=_container_key(criterion, ref.kind),
        )
        for lane in lanes
        for criterion in lane.gap
        if criterion.issue_key not in members
    )
