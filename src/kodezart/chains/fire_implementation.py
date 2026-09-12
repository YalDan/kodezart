"""Persist the round artifacts and execute its implementation loop."""

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from kodezart.chains.criteria import current_native_criteria
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.protocols import (
    ArtifactPersister,
    FireCriteriaReader,
    OutboundContentGate,
    PromptSetProvider,
    QualityGate,
)
from kodezart.domain.accept_gate import (
    flagged_items,
)
from kodezart.domain.amendment import NativeAmendmentRefusalError
from kodezart.domain.ticket import format_fire_spec
from kodezart.domain.workflow_state import (
    current_fire_spec,
    current_ticket,
    validated_artifact,
    validated_criteria,
)
from kodezart.types.domain.agent import (
    NativeAmendmentEvent,
    TicketDraftOutput,
    WorkflowArtifactsEvent,
    WorkflowIterationEvent,
)
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.criteria import (
    ExecutionCriterion,
    TrackerCriterion,
    TrackerCriterionSet,
)
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.remediation import RemediationPlan
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.session import AllowedTools, PermissionMode
from kodezart.types.domain.workflow import (
    ExecutionContext,
    WorkflowState,
)


class FireImplementation:
    """Persist the round artifacts and execute its implementation loop."""

    def __init__(
        self,
        *,
        quality_gate: QualityGate,
        prompts: PromptSetProvider,
        artifact_persister: ArtifactPersister | None,
        gate: OutboundContentGate,
        criteria_reader: FireCriteriaReader | None = None,
    ) -> None:
        self._quality_gate = quality_gate
        self._criteria_reader = criteria_reader
        self._prompts = prompts
        self._artifact_persister = artifact_persister
        self._gate = gate
        self._log: BoundLogger = get_logger("kodezart.chains.ralph_workflow")

    @property
    def persists_artifacts(self) -> bool:
        """Whether the graph includes the two artifact writes."""
        return self._artifact_persister is not None

    async def run_quality_gate(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        feature_branch: str,
        ralph_branch: str,
        base_spec: BaseSpec,
        work_base_ref: str,
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        acceptance_criteria: list[ExecutionCriterion],
        tracker_spec: TrackerSpec | None = None,
        cache_key: str,
        run_identity: RunIdentity | None = None,
        repo_visibility: RepoVisibility,
    ) -> WorkflowIterationEvent:
        """Delegate to the quality gate for iterative execution."""
        writer = get_stream_writer()
        last_iteration_event: WorkflowIterationEvent | None = None
        last_amendment: NativeAmendmentEvent | None = None
        async for event in self._quality_gate.run(
            prompt=prompt,
            repo_path=repo_path,
            repo_url=repo_url,
            feature_branch=feature_branch,
            ralph_branch=ralph_branch,
            base_spec=base_spec,
            work_base_ref=work_base_ref,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            acceptance_criteria=acceptance_criteria,
            tracker_spec=tracker_spec,
            cache_key=cache_key,
            run_identity=run_identity,
            repo_visibility=repo_visibility,
        ):
            writer(event)
            if isinstance(event, NativeAmendmentEvent):
                last_amendment = event
            if isinstance(event, WorkflowIterationEvent):
                last_iteration_event = event
                last_amendment = None

        if last_amendment is not None and last_amendment.report.upheld:
            raise NativeAmendmentRefusalError(
                report=last_amendment.report, last_iteration=last_iteration_event
            )

        if last_iteration_event is None:
            msg = "Ralph loop completed without emitting an iteration event."
            raise RuntimeError(msg)

        return last_iteration_event

    async def run_ralph_loop(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Delegate to the quality gate for iterative execution.

        The round's ticket and the ref its branch is cut from arrive the
        same way — off the state a remediation round wrote — so a round
        implementing a fix is built on the tree that fix is about.
        """
        ctx = ExecutionContext.from_configurable(config)

        spec = current_fire_spec(state)
        criterion_set = state["criterion_set"]
        if isinstance(spec, TrackerSpec):
            criterion_set = await current_native_criteria(
                spec=spec,
                reader=self._criteria_reader,
            )
        criteria = validated_criteria({**state, "criterion_set": criterion_set})

        task_md = format_fire_spec(spec)
        remediation = state["remediation_ticket"]
        if isinstance(remediation, RemediationPlan):
            task_md += "\n\n## Remediation\n" + remediation.instructions

        implementation_prompt = self._prompts.template_for(
            PromptKey.IMPLEMENTATION,
        ).render({"task_md": task_md})

        last_iteration_event = await self.run_quality_gate(
            prompt=implementation_prompt,
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            feature_branch=state["feature_branch"],
            ralph_branch=state["ralph_branch"],
            base_spec=ctx.base_spec,
            work_base_ref=state["work_base_ref"],
            permission_mode=ctx.permission_mode,
            allowed_tools=ctx.allowed_tools,
            acceptance_criteria=criteria,
            tracker_spec=spec if isinstance(spec, TrackerSpec) else None,
            cache_key=ctx.cache_key,
            run_identity=ctx.run_identity,
            repo_visibility=state["repo_visibility"],
        )

        if isinstance(spec, TrackerSpec):
            # The gate reconciles result text to its dispatched Check snapshot.
            # Preserve that final iteration's snapshot for failure evidence;
            # later execution/review still rereads the tracker at its barrier.
            criterion_set = TrackerCriterionSet(
                criteria=[
                    TrackerCriterion(id=result.criterion_id, text=result.criterion)
                    for result in last_iteration_event.evaluation.criteria_results
                ],
            )
            criteria = list(criterion_set.criteria)

        # feature_tip_sha is left None here; merge_to_feature sets it
        # from the merger's outcome.feature_tip_sha (canonical, post-push).
        return {
            "criterion_set": criterion_set,
            "accept_verdict": last_iteration_event.verdict,
            "flagged_items": flagged_items(
                criteria,
                last_iteration_event.evaluation.sherlock_flags,
            ),
            # A SUM, not a replacement: a remediation round runs its own
            # loop, and a terminal reporting only the last round's count
            # would understate what the run actually spent.
            "total_iterations": (
                state["total_iterations"] + last_iteration_event.iteration
            ),
            "feature_tip_sha": None,
            "trajectory": last_iteration_event.trajectory,
        }

    async def persist_ticket(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Persist the finished ticket the moment it exists, before criteria.

        The drafter's output — draft plus its review rounds — is the most
        expensive artefact the pipeline produces, and it lived only in
        graph state until a node downstream of criteria generation wrote
        it.  A transient failure at criteria generation therefore
        discarded a finished, reviewed ticket and forced a re-run to
        redraft from scratch.  Writing it here bounds that loss to the
        node that actually failed.

        The criteria are NOT written here — they do not exist yet, which
        is the whole reason the combined write sat downstream.
        """
        ctx = ExecutionContext.from_configurable(config)

        if self._artifact_persister is None:
            msg = "persist_ticket node requires artifact_persister"
            raise RuntimeError(msg)

        ticket: TicketDraftOutput = current_ticket(state)

        await self._artifact_persister.persist(
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            branch=state["ralph_branch"],
            base_branch=state["work_base_ref"],
            artifacts={
                "ticket.json": await gated_write(
                    gate=self._gate,
                    log=self._log,
                    content=ticket.model_dump_json(indent=2, by_alias=True),
                    visibility=state["repo_visibility"],
                    shape=WriterShape.PROSE,
                    destination=OutboundDestination.ARTIFACT_TICKET_JSON,
                    content_class=ContentClass.AUTHORED,
                ),
            },
            cache_key=ctx.cache_key,
        )
        await self._log.ainfo(
            "ticket_persisted",
            branch=state["ralph_branch"],
            title=ticket.title,
        )
        return {}

    async def persist_artifacts(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Persist ticket and criteria to .kodezart/ on the ralph branch.

        Writes the ticket again rather than only the criteria: a
        remediation round replaces the run's working ticket, and this is
        the write that reaches the branch after that happens.  A branch
        carrying the original ticket while the loop implements the
        remediation one would be a document that contradicts the work.

        This node CREATES the round's ralph branch when it runs, before
        the loop's first iteration ever asks for it, so it cuts from the
        run's work base for the same reason the loop does — a branch cut
        here from anywhere else is the base the loop would inherit.
        """
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()

        if self._artifact_persister is None:
            msg = "persist_artifacts node requires artifact_persister"
            raise RuntimeError(msg)

        ticket: TicketDraftOutput = current_ticket(state)

        criteria_artifact = validated_artifact(state)

        # AUTHORED, both: a JSON container does not make its leaves derived,
        # and every leaf here was written by the model.
        artifacts: dict[str, str] = {
            "ticket.json": await gated_write(
                gate=self._gate,
                log=self._log,
                content=ticket.model_dump_json(indent=2, by_alias=True),
                visibility=state["repo_visibility"],
                shape=WriterShape.PROSE,
                destination=OutboundDestination.ARTIFACT_TICKET_JSON,
                content_class=ContentClass.AUTHORED,
            ),
            "criteria.json": await gated_write(
                gate=self._gate,
                log=self._log,
                content=criteria_artifact.model_dump_json(indent=2, by_alias=True),
                visibility=state["repo_visibility"],
                shape=WriterShape.PROSE,
                destination=OutboundDestination.ARTIFACT_CRITERIA_JSON,
                content_class=ContentClass.AUTHORED,
            ),
        }

        status = await self._artifact_persister.persist(
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            branch=state["ralph_branch"],
            base_branch=state["work_base_ref"],
            artifacts=artifacts,
            cache_key=ctx.cache_key,
        )
        writer(
            WorkflowArtifactsEvent(
                status=status,
                branch=state["ralph_branch"],
            )
        )
        # TODO(artifact-resume): On checkpoint resume, check if artifacts
        # already exist on the branch and skip regeneration. Requires the
        # HTTP→handler→engine thread_id plumbing in ralph_workflow.py:130-145.
        return {}
