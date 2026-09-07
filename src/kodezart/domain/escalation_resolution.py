"""Resolve an escalation from addressed comments, without reading their prose."""

from collections.abc import Mapping, Sequence

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import EscalationReadError
from kodezart.types.domain.escalation import (
    EscalationResolution,
    EscalationResolutionState,
)
from kodezart.types.domain.tracker import TrackerComment


def resolution_from_comments(
    *,
    issue_key: str,
    lane_key: str,
    escalation_key: str,
    prefixes: Mapping[str, str],
    comments: Sequence[TrackerComment],
) -> EscalationResolution:
    """Only a direct reply under the exact configured marker answers this key."""
    escalation_marker = compose_comment_marker(
        prefixes=prefixes,
        purpose="escalation",
        lane=lane_key,
        occurrence_key=escalation_key,
    )
    decision_marker = compose_comment_marker(
        prefixes=prefixes,
        purpose="decision",
        lane=lane_key,
        occurrence_key=escalation_key,
    )
    escalations = [
        comment
        for comment in comments
        if comment.issue_key == issue_key
        and comment.body.partition("\n")[0] == escalation_marker
    ]
    if len(escalations) != 1:
        raise EscalationReadError(
            issue_key=issue_key,
            lane_key=lane_key,
            escalation_key=escalation_key,
            reason=f"expected one escalation record; found {len(escalations)}",
        )
    escalation = escalations[0]
    decisions = [
        comment
        for comment in comments
        if comment.issue_key == issue_key
        and comment.reply_to == escalation.comment_key
        and comment.body.partition("\n")[0] == decision_marker
    ]
    if len(decisions) > 1:
        raise EscalationReadError(
            issue_key=issue_key,
            lane_key=lane_key,
            escalation_key=escalation_key,
            reason=f"expected at most one addressed decision; found {len(decisions)}",
        )
    if not decisions:
        return EscalationResolution(
            state=EscalationResolutionState.UNRESOLVED, decision_ref=None
        )
    return EscalationResolution(
        state=EscalationResolutionState.RESOLVED,
        decision_ref=decisions[0].comment_key,
    )
