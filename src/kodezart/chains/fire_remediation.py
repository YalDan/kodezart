"""Share one remediation budget and drafted round transition across graphs."""

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    Remediator,
)
from kodezart.domain.agent import generate_ralph_branch_name
from kodezart.domain.workflow_state import (
    original_ticket,
    validated_criteria,
)
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    WorkflowRemediationEvent,
)
from kodezart.types.domain.remediation import RemediationEntry
from kodezart.types.domain.workflow import (
    ExecutionContext,
    RemediationRequest,
    WorkflowState,
)


class FireRemediation:
    """Share one remediation budget and drafted round transition across graphs."""

    def __init__(
        self, *, remediator: Remediator | None, remediation_max_rounds: int
    ) -> None:
        self._remediator = remediator
        self._remediation_max_rounds = remediation_max_rounds
        self._log: BoundLogger = get_logger("kodezart.chains.ralph_workflow")

    def rounds_remain(self, state: WorkflowState) -> bool:
        """Whether the run may spend another remediation round.

        ONE counter, read by all three routes.  Two counters would make
        the worst case twice the budget, and the routes are not
        independent — a remediation loop that ends unaccepted and then
        opens a request whose CI fails is one run failing twice, not two
        separate failures.
        """
        return (
            self._remediator is not None
            and state["remediation_rounds_used"] < self._remediation_max_rounds
        )

    async def remediate(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        entry = self.remediation_entry(state)
        work_base_ref = state["work_base_ref"]
        request = RemediationRequest(
            entry=entry,
            round_index=state["remediation_rounds_used"],
            original_ticket=original_ticket(state),
            work_branch=state["feature_branch"],
            work_base_ref=work_base_ref,
            total_iterations=state["total_iterations"],
            trajectory=state["trajectory"],
            criteria=validated_criteria(state),
            failure_evidence=self.failure_evidence(state, entry),
        )

        return await self.draft_remediation(state, request, config)

    def remediation_entry(self, state: WorkflowState) -> RemediationEntry:
        """Which failure opened this round — computed from state, not routing.

        The three routes share a join point, so the node a run arrived
        from carries less information than the state it arrived with —
        the same reason the terminal outcome is computed rather than
        judged from routing provenance.
        """
        if state["merged"] and state["review_passed"] is False:
            return RemediationEntry.review_failure
        return RemediationEntry.loop_not_accepted

    def failure_evidence(
        self,
        state: WorkflowState,
        entry: RemediationEntry,
    ) -> str:
        """The evidence for the entry that fired, never a generic summary."""
        if entry is RemediationEntry.review_failure:
            return (
                state["review_feedback"]
                or "The post-merge review rejected the work with no feedback."
            )
        trajectory = state["trajectory"]
        if trajectory is None:
            return "The loop ended without acceptance and recorded no iterations."
        never_passed = ", ".join(trajectory.never_passed_ids) or "none"
        plateau = "; the run plateaued" if trajectory.plateaued else ""
        return (
            "The loop ended without acceptance after "
            f"{state['total_iterations']} iterations. Best pass count "
            f"{trajectory.best_passed_count} at iteration "
            f"{trajectory.best_iteration}{plateau}. "
            f"Criteria that passed in no iteration: {never_passed}."
        )

    async def draft_remediation(
        self,
        state: WorkflowState,
        request: RemediationRequest,
        config: RunnableConfig,
    ) -> dict[str, object]:
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()
        remediator = self._remediator
        if remediator is None:
            raise RuntimeError("Remediation requires a configured remediator")
        entry = request.entry
        work_base_ref = request.work_base_ref
        remediation_event: WorkflowRemediationEvent | None = None
        async for event in remediator.run(
            request,
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            cache_key=ctx.cache_key,
            run_identity=ctx.run_identity,
        ):
            writer(event)
            if isinstance(event, WorkflowRemediationEvent):
                remediation_event = event

        if remediation_event is None:
            msg = "Remediator did not emit a WorkflowRemediationEvent."
            raise RuntimeError(msg)

        await self._log.ainfo(
            "remediation_round_opened",
            entry=entry.value,
            round_index=state["remediation_rounds_used"],
            base_ref=work_base_ref,
            rounds_remaining=(
                self._remediation_max_rounds - state["remediation_rounds_used"] - 1
            ),
        )
        return {
            "remediation_rounds_used": state["remediation_rounds_used"] + 1,
            "remediation_ticket": remediation_event.ticket,
            "remediation_entry": entry,
            "ralph_branch": generate_ralph_branch_name(state["feature_branch"]),
            "acceptance_criteria": [],
            "criteria_artifact": None,
            "criteria_validation": None,
            "criteria_regeneration_rounds": 0,
            "accept_verdict": AcceptVerdict.rejected,
            "review_passed": False,
            "merged": False,
            "merge_error": None,
        }
