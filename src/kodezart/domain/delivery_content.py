"""Factual publication inputs for the connected delivery dispositions.

These functions select presentation and the retained disposition. PR writes,
check watching and rerun routing remain the coordinator's one common path.
"""

from kodezart.domain.errors import DeliveryContextError, DeliveryRouteUnavailableError
from kodezart.domain.stall_report import (
    DO_NOT_MERGE_PREFIX,
    NOT_CONVERGED_HEADING,
    stall_pr_body,
    stall_pr_title,
)
from kodezart.domain.trajectory import landable_commit
from kodezart.types.domain.agent import PRDescriptionOutput
from kodezart.types.domain.criteria import ValidatedCriterion
from kodezart.types.domain.delivery import DeliveryContext, LaneDispatch
from kodezart.types.domain.fire_spec import AuthoredSpec
from kodezart.types.domain.outcome import WorkflowOutcome


def require_deliverable_context(
    *, dispatch: LaneDispatch, context: DeliveryContext
) -> None:
    """Require the existing facts needed by the selected publication route."""
    if context.fire_outcome is WorkflowOutcome.handed_off_for_delivery:
        return
    if context.fire_outcome is WorkflowOutcome.stalled_pr_opened:
        if not isinstance(context.spec, AuthoredSpec):
            reason = "tracker stalled delivery requires its own criterion trajectory"
        elif context.trajectory is None:
            reason = "stalled delivery requires the original terminal trajectory"
        else:
            trajectory = context.trajectory
            rows = context.criteria
            if any(not isinstance(row, ValidatedCriterion) for row in rows):
                reason = "stalled authored delivery requires authored criteria"
            elif not trajectory.records or landable_commit(trajectory) is None:
                reason = "stalled delivery requires the recorded best iteration commit"
            else:
                keys = [row.id for row in rows if isinstance(row, ValidatedCriterion)]
                known = set(keys)
                observed = set(trajectory.never_passed_ids) | {
                    key
                    for record in trajectory.records
                    for key in record.failing_criterion_ids
                }
                if (
                    not known
                    or len(known) != len(keys)
                    or not observed <= known
                    or context.total_iterations != len(trajectory.records)
                    or any(
                        record.passed_count > len(keys) for record in trajectory.records
                    )
                    or trajectory.best_passed_count > len(keys)
                ):
                    raise DeliveryContextError(
                        lane_key=dispatch.lane_key,
                        issue_id=dispatch.issue_id,
                        reason="stalled trajectory disagrees with authored criteria",
                    )
                return
    else:
        reason = "this fire outcome has no connected delivery route"
    raise DeliveryRouteUnavailableError(
        lane_key=dispatch.lane_key,
        issue_id=dispatch.issue_id,
        reason=reason,
        pr_url=None,
        pr_number=None,
        checks_passed=None,
        checks_summary=None,
    )


def prepared_delivery_description(
    *, context: DeliveryContext, published_sha: str
) -> PRDescriptionOutput | None:
    """Render observed stalled facts; ordinary descriptions use their session."""
    if context.fire_outcome is WorkflowOutcome.handed_off_for_delivery:
        return None
    if (
        context.fire_outcome is WorkflowOutcome.stalled_pr_opened
        and isinstance(context.spec, AuthoredSpec)
        and context.trajectory is not None
    ):
        return PRDescriptionOutput(
            title=stall_pr_title(context.spec.ticket.title),
            description=stall_pr_body(
                context.trajectory,
                [
                    row
                    for row in context.criteria
                    if isinstance(row, ValidatedCriterion)
                ],
                landed_commit=published_sha,
            ),
        )
    raise ValueError("unclassifiable delivery presentation")


def require_delivery_presentation(
    *, context: DeliveryContext, dispatch: LaneDispatch, title: str, body: str
) -> None:
    """A gate may refuse required presentation; no content is appended after it."""
    if context.fire_outcome is WorkflowOutcome.stalled_pr_opened and (
        not title.startswith(DO_NOT_MERGE_PREFIX + " ")
        or NOT_CONVERGED_HEADING not in body.splitlines()
    ):
        raise DeliveryContextError(
            lane_key=dispatch.lane_key,
            issue_id=dispatch.issue_id,
            reason="gated stalled presentation lost its required disposition",
        )


def delivered_outcome(
    *, fire_outcome: WorkflowOutcome, check_outcome: WorkflowOutcome
) -> WorkflowOutcome:
    """Retain incompleteness after the ordinary check route completes."""
    if check_outcome not in {
        WorkflowOutcome.ci_passed,
        WorkflowOutcome.ci_not_configured,
    }:
        raise ValueError("unclassifiable completed check route")
    if fire_outcome is WorkflowOutcome.handed_off_for_delivery:
        return check_outcome
    if fire_outcome is WorkflowOutcome.stalled_pr_opened:
        return WorkflowOutcome.stalled_pr_opened
    raise ValueError("unclassifiable completed fire route")
