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
    CRITERION_PASSED = "criterion_passed"
    CRITERION_LAPSED = "criterion_lapsed"
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
    CRITERION_CHECK_SURVIVED_MUTATION = "criterion_check_survived_mutation"
    CRITERION_SATISFIED_AT_BASE = "criterion_satisfied_at_base"
    CRITERION_BASE_READING_UNSETTLED = "criterion_base_reading_unsettled"


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
    RunEventKind.CRITERION_PASSED: RunEventPublisher.LANE,
    RunEventKind.CRITERION_LAPSED: RunEventPublisher.LANE,
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
    RunEventKind.CRITERION_CHECK_SURVIVED_MUTATION: RunEventPublisher.LANE,
    RunEventKind.CRITERION_SATISFIED_AT_BASE: RunEventPublisher.LANE,
    RunEventKind.CRITERION_BASE_READING_UNSETTLED: RunEventPublisher.LANE,
}

DERIVED_RUN_EVENTS = frozenset(
    {
        RunEventKind.EVALUATOR_ACCEPTED,
        RunEventKind.AUDIT_REFUTED,
        RunEventKind.CRITERION_REFUTED,
        RunEventKind.CRITERION_PASSED,
        RunEventKind.CRITERION_LAPSED,
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
        RunEventKind.CRITERION_CHECK_SURVIVED_MUTATION,
        RunEventKind.CRITERION_SATISFIED_AT_BASE,
        RunEventKind.CRITERION_BASE_READING_UNSETTLED,
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
    UndemonstratedReason.check_survived_mutation: (
        RunEventKind.CRITERION_CHECK_SURVIVED_MUTATION
    ),
    UndemonstratedReason.satisfied_at_base: RunEventKind.CRITERION_SATISFIED_AT_BASE,
    UndemonstratedReason.base_reading_unsettled: (
        RunEventKind.CRITERION_BASE_READING_UNSETTLED
    ),
}


#: The event kinds that record a write of a criterion's Evidence row.
#:
#: A passing cross-off restamps the row and posts ``criterion_passed``; a
#: take-back restamps it and posts ``criterion_refuted``.  An undemonstrated
#: reading is keyed to the same criterion at a sha too, but it writes no row,
#: so it is no entry in that row's write history: read as one, a criterion
#: finished at one commit and read as undemonstrated at the next would carry
#: a row the history does not end at (KOD-506, KOD-610).
EVIDENCE_ROW_WRITES = frozenset(
    {RunEventKind.CRITERION_PASSED, RunEventKind.CRITERION_REFUTED}
)


class RunEventTableError(ValueError):
    """A configured deployment cannot account for its complete event set."""

    def __init__(self, failures: tuple[str, ...]) -> None:
        super().__init__("; ".join(failures))
        self.failures = failures
