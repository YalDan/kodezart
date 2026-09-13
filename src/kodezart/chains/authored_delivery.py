"""Authored HTTP delivery around the same shared fire graph."""

from collections.abc import AsyncIterator

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from kodezart.chains.authored_checks import AuthoredChecks
from kodezart.chains.authored_publication import AuthoredPublication
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.domain.accept_gate import (
    gate_cleared,
)
from kodezart.domain.authored_outcome import classify_authored_outcome
from kodezart.domain.workflow_state import (
    original_fire_spec,
    validated_criteria,
)
from kodezart.types.domain.agent import (
    AgentEvent,
    AuthoredWorkflowCompleteEvent,
    WorkflowCompleteEvent,
)
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.ci import CIStatus
from kodezart.types.domain.delivery import CheckRedClass
from kodezart.types.domain.remediation import RemediationEntry
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.session import AllowedTools, PermissionMode
from kodezart.types.domain.workflow import (
    AuthoredWorkflowState,
    RemediationRequest,
)


class AuthoredDeliveryCoordinator:
    """Compose fire, publication and checks without inheriting their collaborators."""

    def __init__(
        self,
        *,
        fire: RalphWorkflowEngine,
        publication: AuthoredPublication,
        checks: AuthoredChecks,
    ) -> None:
        self.fire = fire
        self.publication = publication
        self.checks = checks
        self._delivery_compiled = self._build_delivery_graph().compile(
            checkpointer=fire.checkpointer
        )

    async def run(
        self,
        *,
        prompt: str,
        issue_key: str | None = None,
        repo_path: str | None,
        repo_url: str | None,
        base_spec: BaseSpec,
        scope: ScopeRef | None,
        implied_base: BaseSpec | None = None,
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        cache_key: str,
        run_identity: RunIdentity | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Preserve the authored HTTP stream around the shared fire graph."""
        fire_state, config = self.fire.prepare(
            prompt=prompt,
            issue_key=issue_key,
            repo_path=repo_path,
            repo_url=repo_url,
            base_spec=base_spec,
            scope=scope,
            implied_base=implied_base,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            cache_key=cache_key,
            run_identity=run_identity,
        )
        initial_state = AuthoredWorkflowState(
            **fire_state,
            pr_url=None,
            pr_number=None,
            ci_status=CIStatus.not_monitored,
            ci_summary=None,
            ci_red_class=None,
            ci_run_absent=False,
        )
        terminal: AuthoredWorkflowCompleteEvent | None = None
        async for event in self._delivery_compiled.astream(
            initial_state,
            config=config,
            stream_mode="custom",
            subgraphs=True,
        ):
            _, payload = event
            if not isinstance(payload, AgentEvent):
                raise TypeError(f"Expected AgentEvent, got {type(payload).__name__}")
            if isinstance(payload, AuthoredWorkflowCompleteEvent):
                terminal = payload
            elif isinstance(payload, WorkflowCompleteEvent):
                continue
            yield payload
        if terminal is None:
            raise RuntimeError("Authored delivery emitted no terminal")
        await self.fire.consolidation.cleanup_backups(terminal, config)

    def _build_delivery_graph(
        self,
    ) -> StateGraph[
        AuthoredWorkflowState, None, AuthoredWorkflowState, AuthoredWorkflowState
    ]:
        graph: StateGraph[
            AuthoredWorkflowState, None, AuthoredWorkflowState, AuthoredWorkflowState
        ] = StateGraph(AuthoredWorkflowState)
        graph.add_node("fire", self.fire.graph)
        graph.add_node(
            "open_pr",
            self.fire.floor(self.publication.open_pr),
            retry_policy=self.fire.retry,
        )
        graph.add_node(
            "open_stalled_pr",
            self.fire.floor(self.publication.open_stalled_pr),
            retry_policy=self.fire.retry,
        )
        graph.add_node(
            "monitor_ci",
            self.fire.floor(self.checks.monitor_ci),
            retry_policy=self.fire.retry,
        )
        graph.add_node(
            "comment_failure",
            self.fire.floor(self.publication.comment_failure),
            retry_policy=self.fire.retry,
        )
        graph.add_node(
            "delivery_remediation",
            self.fire.floor(self._delivery_remediation_node),
            retry_policy=self.fire.retry,
        )
        graph.add_node("complete", self._authored_complete_node)
        graph.add_edge(START, "fire")
        graph.add_conditional_edges(
            "fire",
            self._route_after_fire,
            {
                name: name
                for name in (
                    "open_pr",
                    "open_stalled_pr",
                    "monitor_ci",
                    "comment_failure",
                    "complete",
                )
            },
        )
        graph.add_conditional_edges(
            "open_pr",
            self._route_after_pr,
            {"monitor_ci": "monitor_ci", "complete": "complete"},
        )
        graph.add_conditional_edges(
            "open_stalled_pr",
            self._route_after_pr,
            {"monitor_ci": "monitor_ci", "complete": "complete"},
        )
        graph.add_conditional_edges(
            "monitor_ci",
            self._route_after_ci,
            {
                "remediate": "delivery_remediation",
                "comment_failure": "comment_failure",
                "complete": "complete",
            },
        )
        graph.add_edge("delivery_remediation", "fire")
        graph.add_edge("comment_failure", "complete")
        graph.add_edge("complete", END)
        return graph

    def _route_after_fire(self, state: AuthoredWorkflowState) -> str:
        can_pr = self.publication.available and state["repo_url"] is not None
        if state["criteria_infeasible"] or state["merge_error"] is not None:
            return "complete"
        if not gate_cleared(state["accept_verdict"]):
            if (
                can_pr
                and state["best_iteration_sha"] is not None
                and state["trajectory"] is not None
            ):
                return "open_stalled_pr"
            return "complete"
        if not state["review_passed"]:
            if can_pr and state["pr_url"] is not None:
                return "comment_failure"
            return "complete"
        if state["pr_url"] is not None:
            if self.checks.available and state["repo_url"] is not None:
                return "monitor_ci"
            return "complete"
        return "open_pr" if can_pr else "complete"

    async def _delivery_remediation_node(
        self,
        state: AuthoredWorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        request = RemediationRequest(
            entry=RemediationEntry.ci_failure,
            round_index=state["remediation_rounds_used"],
            original_spec=original_fire_spec(state),
            work_branch=state["feature_branch"],
            work_base_ref=state["work_base_ref"],
            pr_url=state["pr_url"],
            total_iterations=state["total_iterations"],
            trajectory=state["trajectory"],
            criteria=validated_criteria(state),
            failure_evidence=state["ci_summary"]
            or "CI reported a failure with no summary.",
        )
        return await self.fire.remediation.draft_remediation(state, request, config)

    def _route_after_ci(self, state: AuthoredWorkflowState) -> str:
        """Route based on CI result, fix budget, and adapter preconditions."""
        if state["ci_status"] is not CIStatus.failed:
            return "complete"
        if state.get("ci_red_class") is not CheckRedClass.WORK_DEFECT:
            return "complete"
        if self.fire.remediation.rounds_remain(state):
            return "remediate"
        can_comment = (
            state["pr_number"] is not None
            and self.publication.available
            and state.get("repo_url") is not None
        )
        if can_comment:
            return "comment_failure"
        return "complete"

    def _route_after_pr(self, state: AuthoredWorkflowState) -> str:
        """Route after PR creation: monitor CI only if adapter is configured."""
        if self.checks.available and state.get("repo_url") is not None:
            return "monitor_ci"
        return "complete"

    async def _authored_complete_node(
        self,
        state: AuthoredWorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Emit the authored terminal after delivery."""
        _ = config
        writer = get_stream_writer()
        writer(
            AuthoredWorkflowCompleteEvent(
                feature_branch=state["feature_branch"],
                ralph_branch=state["ralph_branch"],
                total_iterations=state["total_iterations"],
                accepted=gate_cleared(state["accept_verdict"]),
                outcome=classify_authored_outcome(state),
                merged=state["merged"],
                final_commit_sha=state["feature_tip_sha"],
                merge_error=state["merge_error"],
                pr_url=state["pr_url"],
                pr_number=state["pr_number"],
                ci_status=state["ci_status"],
                trajectory=state["trajectory"],
                criteria_validation=state["criteria_validation"],
            )
        )

        return {}
