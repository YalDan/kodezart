"""Compose the existing native fire with lane publication and bounded fixes."""

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from kodezart.chains.criteria import require_current_native_snapshot
from kodezart.chains.lane_delivery import LaneDeliveryCoordinator
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.domain.accept_gate import gate_cleared
from kodezart.domain.outcome import classify_outcome
from kodezart.domain.workflow_state import original_fire_spec, validated_criteria
from kodezart.types.domain.native_delivery import (
    CompletedLaneDelivery,
    LaneDeliveryEvent,
    NativeDeliveryState,
    PendingLaneDelivery,
    SkippedLaneDelivery,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.remediation import RemediationEntry
from kodezart.types.domain.workflow import (
    ExecutionContext,
    RemediationRequest,
    WorkflowState,
)


class NativeLaneWorkflow:
    """Use the real prepared fire state/config and retain the typed lane result."""

    def __init__(
        self, *, fire: RalphWorkflowEngine, delivery: LaneDeliveryCoordinator | None
    ) -> None:
        if fire.native_graph is None:
            raise ValueError(
                "Native delivery requires the configured native fire graph"
            )
        self.fire = fire
        self._delivery = delivery
        graph: StateGraph[
            NativeDeliveryState, None, NativeDeliveryState, NativeDeliveryState
        ] = StateGraph(NativeDeliveryState)
        graph.add_node("native_fire", fire.native_graph)
        graph.add_node("deliver", fire.floor(self._deliver), retry_policy=fire.retry)
        graph.add_node(
            "remediate", fire.floor(self._remediate), retry_policy=fire.retry
        )
        graph.add_node("complete", self._complete)
        graph.add_edge(START, "native_fire")
        graph.add_edge("native_fire", "deliver")
        graph.add_conditional_edges(
            "deliver",
            self._after_delivery,
            {"remediate": "remediate", "complete": "complete"},
        )
        graph.add_edge("remediate", "native_fire")
        graph.add_edge("complete", END)
        self.graph: CompiledStateGraph[
            NativeDeliveryState, None, NativeDeliveryState, NativeDeliveryState
        ] = graph.compile(checkpointer=fire.checkpointer)

    @staticmethod
    def prepare(state: WorkflowState) -> NativeDeliveryState:
        """Initialize the outer phase while preserving every prepared fire field."""
        return NativeDeliveryState(**state, delivery=PendingLaneDelivery())

    async def _deliver(
        self, state: NativeDeliveryState, config: RunnableConfig
    ) -> dict[str, object]:
        await require_current_native_snapshot(state, reader=self.fire.criteria)
        outcome = classify_outcome(state)
        stalled = (
            not gate_cleared(state["accept_verdict"])
            and not state["merged"]
            and state["merge_error"] is None
            and state["best_iteration_sha"] is not None
            and state["trajectory"] is not None
            and state["feature_tip_sha"] is not None
        )
        if outcome is not WorkflowOutcome.handed_off_for_delivery and not stalled:
            return {
                "delivery": SkippedLaneDelivery(
                    outcome=outcome,
                    reason=f"The fire stopped before delivery: {outcome.value}",
                )
            }
        if self._delivery is None:
            return {
                "delivery": SkippedLaneDelivery(
                    outcome=WorkflowOutcome.review_passed_no_pr_adapter
                    if not stalled
                    else outcome,
                    reason="The selected origin has no PR/check adapter",
                )
            }
        result = await self._delivery.deliver(
            state=state,
            context=ExecutionContext.from_configurable(config),
            stalled=stalled,
            remediation_available=self.fire.remediation.rounds_remain(state),
        )
        return {"delivery": CompletedLaneDelivery(result=result)}

    @staticmethod
    def _after_delivery(state: NativeDeliveryState) -> str:
        phase = state["delivery"]
        if isinstance(phase, PendingLaneDelivery):
            raise ValueError("Delivery cannot finish with a pending result")
        if (
            isinstance(phase, CompletedLaneDelivery)
            and phase.result.remediation_pending
        ):
            return "remediate"
        return "complete"

    async def _remediate(
        self, state: NativeDeliveryState, config: RunnableConfig
    ) -> dict[str, object]:
        phase = state["delivery"]
        if (
            not isinstance(phase, CompletedLaneDelivery)
            or not phase.result.remediation_pending
            or not self.fire.remediation.rounds_remain(state)
        ):
            raise ValueError(
                "Native delivery remediation requires an authorized work-defect round"
            )
        await require_current_native_snapshot(state, reader=self.fire.criteria)
        result = phase.result
        request = RemediationRequest(
            entry=RemediationEntry.ci_failure,
            round_index=state["remediation_rounds_used"],
            original_spec=original_fire_spec(state),
            work_branch=state["feature_branch"],
            work_base_ref=state["work_base_ref"],
            pr_url=result.pr.url,
            total_iterations=state["total_iterations"],
            trajectory=state["trajectory"],
            criteria=validated_criteria(state),
            failure_evidence=result.checks_summary
            or "CI reported a work defect with no summary.",
        )
        return await self.fire.remediation.draft_remediation(state, request, config)

    async def _complete(
        self, state: NativeDeliveryState, config: RunnableConfig
    ) -> dict[str, object]:
        phase = state["delivery"]
        if isinstance(phase, PendingLaneDelivery):
            raise ValueError(
                "A native lane cannot terminate without a delivery disposition"
            )
        if isinstance(phase, CompletedLaneDelivery):
            if self._delivery is None:
                raise ValueError("A completed delivery requires its coordinator")
            await self._delivery._require_current(
                state, ExecutionContext.from_configurable(config), phase.result.pr
            )
        else:
            await require_current_native_snapshot(state, reader=self.fire.criteria)
        get_stream_writer()(LaneDeliveryEvent(delivery=phase))
        return {}
