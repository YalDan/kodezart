"""The shared run-event vocabulary, owned once."""

from enum import StrEnum


class RunEventKind(StrEnum):
    FIRST_PUSH = "first_push"
    PR_OPENED = "pr_opened"
    GATE_GREEN = "gate_green"
    GATE_RED = "gate_red"
    EVALUATOR_ACCEPTED = "evaluator_accepted"
    LANE_PLATEAUED = "lane_plateaued"
    ISSUE_CROSSED_OFF = "issue_crossed_off"
    CRITERION_REFUTED = "criterion_refuted"
    ESCALATION_RAISED = "escalation_raised"
    LANE_DISPATCHED = "lane_dispatched"
    CLAIM_LOST = "claim_lost"
    CHECKS_GREEN = "checks_green"
    LANDED_AND_VERIFIED = "landed_and_verified"
    CRITERIA_HALT = "criteria_halt"
    AUDIT_REFUTED = "audit_refuted"
    RUN_ALARM_RAISED = "run_alarm_raised"
    RUN_ALARM_CLEARED = "run_alarm_cleared"
    NODE_SESSION_STARTED = "node_session_started"
