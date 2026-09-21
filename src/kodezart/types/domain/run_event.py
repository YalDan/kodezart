"""The run-event vocabulary and its notification partition, owned once."""

from collections.abc import Mapping
from enum import StrEnum

from kodezart.types.domain.criterion_lifecycle import UndemonstratedReason


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
    CRITERION_GRADING_UNVERIFIED = "criterion_grading_unverified"


class RunEventEffect(StrEnum):
    """The two effects that do not directly name a workflow state."""

    DERIVED = "DERIVED"
    NO_TRANSITION = "NO_TRANSITION"


class RunEventPublisher(StrEnum):
    """Record-cadence events versus the event's separately owning raiser."""

    LANE = "lane"
    RAISER = "raiser"


RUN_EVENT_PUBLISHERS = {
    RunEventKind.FIRST_PUSH: RunEventPublisher.LANE,
    RunEventKind.PR_OPENED: RunEventPublisher.LANE,
    RunEventKind.GATE_GREEN: RunEventPublisher.LANE,
    RunEventKind.GATE_RED: RunEventPublisher.LANE,
    RunEventKind.EVALUATOR_ACCEPTED: RunEventPublisher.LANE,
    RunEventKind.LANE_PLATEAUED: RunEventPublisher.LANE,
    RunEventKind.ISSUE_CROSSED_OFF: RunEventPublisher.LANE,
    RunEventKind.CRITERION_REFUTED: RunEventPublisher.LANE,
    RunEventKind.ESCALATION_RAISED: RunEventPublisher.LANE,
    RunEventKind.LANE_DISPATCHED: RunEventPublisher.RAISER,
    RunEventKind.CLAIM_LOST: RunEventPublisher.RAISER,
    RunEventKind.CHECKS_GREEN: RunEventPublisher.RAISER,
    RunEventKind.LANDED_AND_VERIFIED: RunEventPublisher.RAISER,
    RunEventKind.CRITERIA_HALT: RunEventPublisher.RAISER,
    RunEventKind.AUDIT_REFUTED: RunEventPublisher.RAISER,
    RunEventKind.RUN_ALARM_RAISED: RunEventPublisher.RAISER,
    RunEventKind.RUN_ALARM_CLEARED: RunEventPublisher.RAISER,
    RunEventKind.NODE_SESSION_STARTED: RunEventPublisher.RAISER,
    RunEventKind.CRITERION_GRADING_UNVERIFIED: RunEventPublisher.LANE,
}

DERIVED_RUN_EVENTS = frozenset(
    {
        RunEventKind.EVALUATOR_ACCEPTED,
        RunEventKind.AUDIT_REFUTED,
        RunEventKind.CRITERION_REFUTED,
        RunEventKind.LANDED_AND_VERIFIED,
    }
)

SILENT_STATE_EVENTS = frozenset(
    {
        RunEventKind.FIRST_PUSH,
        RunEventKind.GATE_GREEN,
        RunEventKind.GATE_RED,
        RunEventKind.ESCALATION_RAISED,
        RunEventKind.ISSUE_CROSSED_OFF,
        RunEventKind.RUN_ALARM_RAISED,
        RunEventKind.RUN_ALARM_CLEARED,
        RunEventKind.NODE_SESSION_STARTED,
        RunEventKind.CRITERION_GRADING_UNVERIFIED,
    }
)


#: The event kind that records each reading a grading failed to take.
#:
#: The kind IS the reason, so the comment says which reading failed with
#: no field on the event to keep in step with the cross-off's.  Total over
#: the reason enum, and each value is its own kind: two reasons sharing a
#: kind would be one reason.
UNDEMONSTRATED_EVENT_KINDS: Mapping[UndemonstratedReason, RunEventKind] = {
    UndemonstratedReason.workspace_not_the_graded_sha: (
        RunEventKind.CRITERION_GRADING_UNVERIFIED
    ),
}


class RunEventTableError(ValueError):
    """A configured deployment cannot account for its complete event set."""

    def __init__(self, failures: tuple[str, ...]) -> None:
        super().__init__("; ".join(failures))
        self.failures = failures
