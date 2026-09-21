"""An explicitly declared event table for tracker-enabled fixtures."""

RUN_EVENT_STATES = {
    "first_push": "NO_TRANSITION",
    "pr_opened": "in_review",
    "gate_green": "NO_TRANSITION",
    "gate_red": "NO_TRANSITION",
    "evaluator_accepted": "DERIVED",
    "lane_plateaued": "in_review",
    "issue_crossed_off": "NO_TRANSITION",
    "criterion_refuted": "DERIVED",
    "escalation_raised": "NO_TRANSITION",
    "lane_dispatched": "in_progress",
    "claim_lost": "in_progress",
    "checks_green": "in_review",
    "landed_and_verified": "DERIVED",
    "criteria_halt": "in_progress",
    "audit_refuted": "DERIVED",
    "run_alarm_raised": "NO_TRANSITION",
    "run_alarm_cleared": "NO_TRANSITION",
    "node_session_started": "NO_TRANSITION",
}

RUN_EVENT_TOML = "[run_event_states]\n" + "\n".join(
    f'{key} = "{value}"' for key, value in RUN_EVENT_STATES.items()
)
