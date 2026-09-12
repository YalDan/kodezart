"""Configured native reader with only the external MCP server doubled."""

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.core.backoff import RetryPolicy
from kodezart.types.domain.dispatch import SelfWriteLedger
from tests.tracker.conftest import (
    QUEUE_STATE_LABELS,
    TEAM_IDENTIFIERS,
    WORKFLOW_STATE_NAMES,
)
from tests.tracker.marker_config import MARKER_PREFIXES

LABELS = {
    "criterion": "acceptance-condition",
    "decision": "recorded-question",
    "tracker": "execution-history",
}


def tracker_over(server, *, issue_labels=None):
    return LinearMcpTracker(
        criteria_stage_label_key=None,
        scope_labels={},
        caller=server,
        issue_labels=LABELS if issue_labels is None else issue_labels,
        marker_prefixes=MARKER_PREFIXES,
        queue_state_labels=QUEUE_STATE_LABELS,
        workflow_state_names=WORKFLOW_STATE_NAMES,
        team_identifiers=TEAM_IDENTIFIERS,
        retry=RetryPolicy(attempts=1, initial_delay=0),
        ledger=SelfWriteLedger(),
    )


def native_tracker(server, labels):
    return tracker_over(server, issue_labels=labels)
