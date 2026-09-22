"""Ralph quality-gating loop — execute + evaluate until accepted or exhausted."""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import patch_config
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from pydantic import TypeAdapter, ValidationError

from kodezart.chains.criteria import current_native_criteria, held_roster
from kodezart.core.constants import EVAL_PERMISSION_MODE
from kodezart.core.errors import NoStructuredOutputError, soft_failure
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.node_sessions import NodeSessionObserver
from kodezart.core.protocols import (
    AfterPublish,
    AgentRunner,
    FireCriteriaReader,
    GitService,
    GitSourceReader,
    LaneLapseEscalator,
    LaneStateWriter,
    PromptSetProvider,
    RepoCache,
    WorkspaceProvider,
)
from kodezart.core.redispatch import until_permutation
from kodezart.core.retry import DelayFloor, RetryFloor, should_retry
from kodezart.core.stream_drain import drain
from kodezart.domain.accept_gate import gate_cleared
from kodezart.domain.amendment import NativeWriteRefusalError, repeated_upheld
from kodezart.domain.criteria_grading import grade_iteration
from kodezart.domain.criterion_cross_off import (
    base_answers,
    base_reasons,
    cross_offs_for,
    evaluation_observation,
    iteration_output,
    passed_ids,
    undemonstrated_reasons,
)
from kodezart.domain.errors import (
    BaseReadingUnavailableError,
    GitSourceReadError,
    WorkspaceError,
)
from kodezart.domain.fan_in import fan_in_report, require_permutation
from kodezart.domain.fire_spec import body_digest
from kodezart.domain.lapse import GradedState, HeldStanding, held_standing
from kodezart.domain.prompt_variables import (
    changeset_variables,
    execution_criteria_variables,
    tracker_checks_section,
)
from kodezart.domain.thread_id import ralph_thread_id
from kodezart.domain.trajectory import fold_trajectory
from kodezart.services.audit_sessions import judge_in_workspace
from kodezart.services.git_observations import read_workspace_head
from kodezart.services.mutation_survival import MutationSurvivalReader
from kodezart.services.native_amendments import NativeAmendments
from kodezart.services.owned_workspace import owned_workspace
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    ACCEPTANCE_CRITERIA_SCHEMA,
    BASE_CHECK_SCHEMA,
    AcceptanceCriteriaOutput,
    AgentEvent,
    BaseCheckOutput,
    NativeAmendmentEvent,
    ResultEvent,
    WorkflowIterationEvent,
)
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.criteria import (
    CriterionId,
    ExecutionCriterion,
    FanInReport,
    TrackerCriterionSet,
)
from kodezart.types.domain.criterion_lifecycle import (
    PATH_BOUND_CLASSES,
    CriterionCrossOff,
    CrossOffState,
    UndemonstratedReason,
)
from kodezart.types.domain.criterion_ref import CriterionRef
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.grading import IterationGrade
from kodezart.types.domain.node_session import NodeInvocation
from kodezart.types.domain.persist import PersistResult
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.ralph_outcome import (
    EvaluatedRalphOutcome,
    NativeEvaluatedRalphOutcome,
    PendingRalphOutcome,
    RalphOutcome,
    RefusedRalphOutcome,
)
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.run_state import LaneBinding
from kodezart.types.domain.session import (
    AllowedTools,
    PermissionMode,
    SessionType,
    ToolPreset,
)
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS
from kodezart.types.domain.trajectory import IterationRecord
from kodezart.types.domain.workflow import RalphLoopContext, RalphLoopState


class RalphLoop:
    """Iterates agent work until acceptance criteria pass or max iterations.

    Graph: START -> execute -> evaluate -> [conditional: execute or END]
    """

    def __init__(
        self,
        service: AgentRunner,
        *,
        max_iterations: int,
        plateau_window: int,
        git: GitService,
        cache: RepoCache,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        checkpointer: BaseCheckpointSaver[str] | None = None,
        retry_max_attempts: int,
        retry_initial_interval: float,
        delay_floor_for: DelayFloor,
        fan_in_max_attempts: int,
        criteria_reader: FireCriteriaReader | None = None,
        amendments: NativeAmendments | None = None,
        source: GitSourceReader | None = None,
        lane_state: LaneStateWriter | None = None,
        workspace: WorkspaceProvider | None = None,
        lapse_escalations: LaneLapseEscalator | None = None,
        mutation: MutationSurvivalReader | None = None,
    ) -> None:
        self._service = service
        self._criteria_reader = criteria_reader
        self._amendments = amendments
        self._source = source
        self._lane_state = lane_state
        self._workspace = workspace
        self._lapse_escalations = lapse_escalations
        self._mutation = mutation
        self._max_iterations = max_iterations
        self._plateau_window = plateau_window
        self._fan_in_max_attempts = fan_in_max_attempts
        # git/cache are injected solely so _evaluate_node can pre-compute
        # the ChangesetDigest passed to evaluation.build_prompt — the loop
        # does NOT take on canonical-tip bookkeeping (the outer engine's
        # merger does that internally).
        self._git: GitService = git
        self._cache: RepoCache = cache
        self._prompts: PromptSetProvider = prompts
        self._skills: SkillsSelection = skills
        self._retry = RetryPolicy(
            max_attempts=retry_max_attempts,
            initial_interval=retry_initial_interval,
            retry_on=should_retry,
        )
        self._floor: RetryFloor = RetryFloor(delay_floor_for)
        self._log: BoundLogger = get_logger(__name__)
        self._checkpointer = checkpointer
        self._compiled = self._build_graph().compile(
            checkpointer=self._checkpointer,
        )

    async def run(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        feature_branch: str,
        ralph_branch: str,
        base_spec: BaseSpec,
        work_base_ref: str,
        resumed_head_sha: str | None = None,
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        acceptance_criteria: list[ExecutionCriterion],
        tracker_spec: TrackerSpec | None = None,
        cache_key: str,
        run_identity: RunIdentity | None = None,
        surface_holder: str | None = None,
        repo_visibility: RepoVisibility,
    ) -> AsyncIterator[AgentEvent]:
        """Execute the quality-gating loop.

        Build ``RalphLoopContext`` from parameters, configure thread ID for
        checkpointing, and stream events from the compiled LangGraph graph.
        """
        ctx = RalphLoopContext(
            prompt=prompt,
            repo_path=repo_path,
            repo_url=repo_url,
            cache_key=cache_key,
            run_identity=run_identity,
            surface_holder=surface_holder,
            base_spec=base_spec,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            feature_branch=feature_branch,
            ralph_branch=ralph_branch,
            work_base_ref=work_base_ref,
            resumed_head_sha=resumed_head_sha,
            acceptance_criteria=acceptance_criteria,
            tracker_spec=tracker_spec,
            repo_visibility=repo_visibility,
        )
        configurable: dict[str, object] = ctx.model_dump()
        if self._checkpointer is not None:
            configurable["thread_id"] = ralph_thread_id(cache_key)

        config: RunnableConfig = (
            {"configurable": configurable}
            if tracker_spec is None
            else patch_config(None, configurable=configurable)
        )

        initial_state: RalphLoopState = {
            "iteration": 0,
            "verdict": AcceptVerdict.rejected,
            "pending_failures": [],
            "iteration_records": [],
            "outcome": PendingRalphOutcome(),
            # Nothing stands at entry: the scope arm persists no graph state,
            # so a killed run re-enters from the board with every criterion
            # its roster owes to be graded again.
            "standing": (),
        }

        # Native nested invocation retains the framework's parent-task namespace
        # and resume signal. A new authored invocation keeps its existing config.
        # Values carry the actual completed consumer receipt when a saved child
        # has no nodes left to run (and consequently emits no fresh custom event).
        outcome: RalphOutcome = PendingRalphOutcome()
        forwarded: WorkflowIterationEvent | NativeAmendmentEvent | None = None
        async for mode, value in self._compiled.astream(
            initial_state,
            config=config,
            stream_mode=["custom", "values"],
        ):
            if mode == "custom":
                if not isinstance(value, AgentEvent):
                    raise TypeError("Ralph custom output requires an actual AgentEvent")
                if isinstance(value, (WorkflowIterationEvent, NativeAmendmentEvent)):
                    forwarded = value
                yield value
            elif tracker_spec is not None:
                if not isinstance(value, dict):
                    raise TypeError("Ralph values output requires a state mapping")
                try:
                    outcome = TypeAdapter(RalphOutcome).validate_python(
                        value.get("outcome")
                    )
                except ValidationError as exc:
                    raise NativeWriteRefusalError(
                        "The native checkpoint has no valid completed-loop receipt"
                    ) from exc
        if tracker_spec is not None:
            current = await current_native_criteria(
                spec=tracker_spec,
                reader=self._criteria_reader,
                held=self._held(criteria=acceptance_criteria, outcome=outcome),
            )
            if isinstance(outcome, (PendingRalphOutcome, EvaluatedRalphOutcome)):
                raise NativeWriteRefusalError(
                    "The native loop has no completed outcome"
                )
            if isinstance(outcome, NativeEvaluatedRalphOutcome):
                if tuple(current.criteria) != outcome.criteria:
                    raise NativeWriteRefusalError(
                        "Current Checks differ from the saved native evaluation"
                    )
                cwd = (
                    repo_path
                    if repo_path is not None
                    else await self._cache.ensure_available(repo_url or "", cache_key)
                )
                if await self._resolve(cwd=cwd, ref=ralph_branch) != outcome.head_sha:
                    raise NativeWriteRefusalError("The evaluated native branch changed")
                if outcome.event.branch != ralph_branch:
                    raise NativeWriteRefusalError(
                        "The saved native evaluation belongs to another branch"
                    )
            if forwarded != outcome.event:
                if (
                    isinstance(outcome, RefusedRalphOutcome)
                    and outcome.last_iteration is not None
                ):
                    yield outcome.last_iteration
                yield outcome.event

    def _build_graph(
        self,
    ) -> StateGraph[RalphLoopState, None, RalphLoopState, RalphLoopState]:
        graph: StateGraph[RalphLoopState, None, RalphLoopState, RalphLoopState] = (
            StateGraph(RalphLoopState)
        )
        graph.add_node(
            "execute",
            self._floor(self._execute_node),
            retry_policy=self._retry,
        )
        graph.add_node(
            "evaluate",
            self._floor(self._evaluate_node),
            retry_policy=self._retry,
        )
        graph.add_edge(START, "execute")
        graph.add_conditional_edges(
            "execute", self._route_after_execute, ["evaluate", "execute", END]
        )
        graph.add_conditional_edges(
            "evaluate",
            self._should_continue,
            ["execute", END],
        )
        return graph

    async def _execute_node(
        self,
        state: RalphLoopState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        ctx = RalphLoopContext.from_configurable(config)
        # Everything a native iteration needs from its own wiring is settled
        # here, before the first backend read of the node: each of these is
        # knowable from the context and the collaborators alone, so none of
        # them is worth a criteria read, a session or a commit first.
        after_publish: AfterPublish | None = None
        amendments: NativeAmendments | None = None
        if ctx.tracker_spec is not None:
            if self._source is None:
                raise NativeWriteRefusalError(
                    "Native execution requires its Git source reader"
                )
            if self._amendments is None:
                raise NativeWriteRefusalError(
                    "Native execution requires the precommit amendment owner"
                )
            self._evaluation_workspace()
            amendments = self._amendments
            after_publish = self._record_commit(
                self._lane_writer(), self._lane_binding(ctx)
            )
        native_criteria = (
            None
            if ctx.tracker_spec is None
            else await current_native_criteria(
                spec=ctx.tracker_spec,
                reader=self._criteria_reader,
                held=self._held(
                    criteria=ctx.acceptance_criteria, outcome=state["outcome"]
                ),
            )
        )
        writer = get_stream_writer()
        iteration = state["iteration"] + 1
        is_first = iteration == 1
        # A first iteration whose work base IS the loop branch is continuing a
        # branch that already exists, so it checks that branch out instead of
        # cutting it again; iterations 2..n have always done exactly this.
        cut = is_first and ctx.work_base_ref != ctx.ralph_branch
        if is_first and not cut:
            await self._require_resumed_head(ctx)

        prompt = ctx.prompt
        if not is_first:
            prompt = self._prompts.template_for(PromptKey.ITERATION_FEEDBACK).render(
                {
                    "prior_prompt": prompt,
                    "pending_failures": state["pending_failures"],
                },
            )

        if native_criteria is not None:
            prompt += "\n\n" + tracker_checks_section(native_criteria)

        native_guard = None
        reports = state.get("amendment_reports", [])
        blocked = False
        refusal: NativeAmendmentEvent | None = None
        if (
            ctx.tracker_spec is not None
            and amendments is not None
            and native_criteria is not None
        ):
            native_guard = amendments.for_writer(
                spec=ctx.tracker_spec,
                criteria=native_criteria,
                base_ref=ctx.base_branch,
                repo_url=ctx.repo_url,
                holder=ctx.surface_holder,
                visibility=ctx.repo_visibility,
                stage=PromptKey.IMPLEMENTATION,
            )
            if reports:
                prompt += "\n\nPrior independent amendment reports:\n" + "\n".join(
                    report.model_dump_json() for report in reports
                )

        commit_sha: str | None = None
        async for event in self._service.stream_workflow(
            prompt=prompt,
            repo_path=ctx.repo_path,
            repo_url=ctx.repo_url,
            base_branch=(ctx.work_base_ref if cut else ctx.ralph_branch),
            branch_name=ctx.feature_branch,
            ralph_branch=ctx.ralph_branch,
            permission_mode=ctx.permission_mode,
            allowed_tools=ctx.allowed_tools,
            skills=self._prompts.session_skills(PromptKey.IMPLEMENTATION, self._skills),
            session_type=SessionType.TICKET_FIRE,
            run_identity=ctx.run_identity,
            session_policy=self._prompts.session_policy(PromptKey.IMPLEMENTATION),
            visibility=ctx.repo_visibility,
            create_branch=cut,
            cache_key=ctx.cache_key,
            native_guard=native_guard,
            after_publish=after_publish,
        ):
            if isinstance(event, NativeAmendmentEvent):
                reports = [*reports, event.report]
                blocked = bool(event.report.upheld)
                event = NativeAmendmentEvent(
                    report=event.report,
                    repeated=repeated_upheld(reports),
                )
                if blocked:
                    refusal = event
            writer(event)
            if isinstance(event, ResultEvent) and event.commit_sha:
                commit_sha = event.commit_sha

        update: dict[str, object] = {
            "iteration": iteration,
            "iteration_commit_sha": commit_sha,
        }
        if native_guard is not None:
            update.update(amendment_reports=reports, amendment_blocked=blocked)
            if refusal is not None:
                previous = state["outcome"]
                last = (
                    previous.event
                    if isinstance(
                        previous, (EvaluatedRalphOutcome, NativeEvaluatedRalphOutcome)
                    )
                    else previous.last_iteration
                    if isinstance(previous, RefusedRalphOutcome)
                    else None
                )
                update["outcome"] = RefusedRalphOutcome(
                    event=refusal,
                    last_iteration=last,
                    # This round graded nothing, so what the loop holds is
                    # still what the last genuine grading graded: dropped
                    # here, every barrier after this round would read the
                    # entry roster alone and a criterion this loop finished
                    # mid-run would fall out of the set it is judged against.
                    criteria=(
                        ()
                        if isinstance(previous, PendingRalphOutcome)
                        else previous.criteria
                    ),
                )
        return update

    def _held(
        self, *, criteria: Sequence[ExecutionCriterion], outcome: RalphOutcome
    ) -> TrackerCriterionSet | None:
        """The entry roster plus what this loop's last evaluation graded.

        A criterion added to the subtree mid-run and then graded by this
        loop is held, because the cross-off moved it out of Todo and a
        barrier reading Todo alone would no longer see it. A criterion
        added AFTER that last grading is in neither half, so the set the
        post-loop check reads has changed and it refuses — which is exactly
        what the check meant before cross-offs existed: it detects a set
        change since the last grading and nothing else. A criterion
        finished before this fire entered is in no roster at all.

        Nothing is ever removed from the roster this way: it only admits a
        criterion this loop itself graded. A round that ended in an upheld
        amendment graded nothing and carries the last grading's roster
        forward, so the rule reads the same on either side of one: the last
        evaluation is the last that evaluated, not the last round that ran.
        """
        graded: Sequence[ExecutionCriterion] = (
            () if isinstance(outcome, PendingRalphOutcome) else outcome.criteria
        )
        return held_roster([*criteria, *graded])

    async def _require_resumed_head(self, ctx: RalphLoopContext) -> None:
        """Refuse a continued branch whose local copy is not the head entered on.

        A first iteration that continues an existing branch gets a tree cut
        from the CLONE's copy of it, while the head it must stand at is the
        head the lane's record names; the entry continued the branch only
        after seeing the remote hold it there. A lane whose branch is cut
        from that head never reaches this check. A clone behind that head
        hands the session commits the criteria this lane owes were already
        graded against, and the lane would then record a head it never
        worked at. Both readings are one git read, made here: before the
        session, before any commit, and before the record write that commit
        carries.
        """
        expected = ctx.resumed_head_sha
        if expected is None:
            raise NativeWriteRefusalError(
                "A continued native branch names no head to continue from"
            )
        cwd = (
            ctx.repo_path
            if ctx.repo_path is not None
            else await self._cache.ensure_available(ctx.repo_url or "", ctx.cache_key)
        )
        if await self._resolve(cwd=cwd, ref=ctx.ralph_branch) != expected:
            raise NativeWriteRefusalError(
                "The continued native branch is not at the head the lane entered on"
            )

    def _lane_binding(self, ctx: RalphLoopContext) -> LaneBinding:
        """The lane this node commits for, as the record write needs it."""
        if ctx.tracker_spec is None or ctx.surface_holder is None:
            raise NativeWriteRefusalError(
                "The lane state record has no lane and holder to name"
            )
        return LaneBinding(
            lane_key=ctx.tracker_spec.subject,
            body_digest=body_digest(ctx.tracker_spec.body),
            loop_branch=ctx.ralph_branch,
            deliverable_branch=ctx.feature_branch,
            base_ref=ctx.base_branch,
            repo_url=ctx.repo_url,
            repo_path=ctx.repo_path,
            run_id=ctx.surface_holder,
            visibility=ctx.repo_visibility,
        )

    def _record_commit(
        self, lane_state: LaneStateWriter, lane: LaneBinding
    ) -> AfterPublish:
        """The record write this node's commit act completes with.

        Handed to the persisting phase rather than performed after it, so a
        commit whose record write fails never completes that phase or yields
        its sha; such a commit is on the branch, and the divergence is read
        by comparing the recorded head with the branch head. What the write
        needs from configuration is resolved here, before the implementation
        session opens: a lane that could not record its commit refuses
        before it makes one.
        """
        lane_state.require_writable(lane=lane)

        async def record(workspace_path: str, receipt: PersistResult) -> None:
            await lane_state.record_commit(
                lane=lane, workspace_path=workspace_path, receipt=receipt
            )

        return record

    async def _evaluate_node(
        self,
        state: RalphLoopState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        ctx = RalphLoopContext.from_configurable(config)
        writer = get_stream_writer()
        cwd = (
            ctx.repo_path
            if ctx.repo_path is not None
            else await self._cache.ensure_available(
                ctx.repo_url or "",
                ctx.cache_key,
            )
        )
        native_ref = (
            await self._resolve(cwd=cwd, ref=ctx.ralph_branch)
            if ctx.tracker_spec is not None
            else None
        )
        evaluation_ref = native_ref if native_ref is not None else ctx.ralph_branch
        changeset = await self._git.diff_summary(
            cwd=cwd,
            base_ref=ctx.base_branch,
            head_ref=evaluation_ref,
        )

        # The graph can retry this node after it already opened a session.
        # Give that execution a fresh invocation component; iteration and
        # correction ordinals alone repeat on a graph-level retry.
        node_execution = uuid4().hex
        evaluation_attempt = 0

        # What the last grading of this loop left standing, and what each of
        # those gradings is still worth at the head this iteration grades. The
        # digests are read once per distinct standing sha, before the session,
        # because the reading decides which criteria the session is asked
        # about at all.
        prior: Sequence[CriterionCrossOff] = state.get("standing", ())
        standing = HeldStanding(carried=(), rederive=(), lapsed=(), newly_lapsed=())
        reading: dict[CriterionRef, GradedState] = {}
        if native_ref is not None and prior:
            standing = held_standing(
                prior=prior,
                head_sha=native_ref,
                changesets=await self._moved_since(
                    cwd=cwd, prior=prior, head_sha=native_ref
                ),
            )
            reading = {
                **{
                    cross_off.criterion: GradedState.counted
                    for cross_off in standing.carried
                },
                **{
                    cross_off.criterion: GradedState.lapsed
                    for cross_off in standing.lapsed
                },
            }
        # Resolved before the session, not at the write: a lapse the loop
        # cannot re-derive owes a question, and a lane that could not ask one
        # refuses before it takes a criterion back rather than after. Held on
        # the transition and not on the whole lapsed set, so a deployment that
        # configures no escalation writer is not refused at every iteration.
        escalator = self._lapse_escalator() if standing.newly_lapsed else None

        dispatched = tuple(ctx.acceptance_criteria)
        # The session the standing grade came from, carried out of the
        # retried closure: the Evidence row points back at the grading that
        # produced the verdict, and on a re-dispatch that is the last one.
        # Every path out of the dispatch below either sets it or raises, so
        # nothing downstream reads this initial value.
        graded_in: str = ""
        # The output the standing grade was reconciled from, carried out for
        # the same reason: a withheld verdict is decided by grading that same
        # input again, so every correspondence fact comes back identical.
        graded_output: AcceptanceCriteriaOutput | None = None
        # The question the standing grade answered, carried out so the mutant
        # tree is asked that question and no paraphrase of it.
        graded_prompt: str = ""
        # Whether the tree the standing grade was read from was the one the
        # graded sha names. The authored arm has no sha to stand for, so its
        # readings are of the ref it asked for and nothing else is claimed —
        # and for the same reason it takes no reading at the lane's base: it
        # owns no tree, stamps no sha and writes no cross-off, so there is no
        # claim about a branch for a base reading to qualify.
        workspace_stood = True

        async def evaluate() -> IterationGrade:
            nonlocal dispatched, graded_in, graded_output, graded_prompt
            nonlocal workspace_stood
            criteria: list[ExecutionCriterion] = ctx.acceptance_criteria
            roster: TrackerCriterionSet | None = None
            if ctx.tracker_spec is not None:
                snapshot = await current_native_criteria(
                    spec=ctx.tracker_spec,
                    reader=self._criteria_reader,
                    held=self._held(
                        criteria=ctx.acceptance_criteria, outcome=state["outcome"]
                    ),
                )
                roster = snapshot
                criteria = list(snapshot.criteria)
            # The session is asked only about what this iteration may grade.
            # A criterion whose grading still stands would be re-derived for
            # nothing, and one that has lapsed on a performed observation
            # cannot be re-derived at all.
            withheld = frozenset(
                str(cross_off.criterion)
                for cross_off in (*standing.carried, *standing.lapsed)
            )
            for_session = [
                criterion for criterion in criteria if str(criterion.id) not in withheld
            ]
            if not for_session:
                # Only a STANDING grading is withheld, so an iteration that
                # ran at all has something failing or owed. If that is ever
                # wrong the loop says so before it opens a session, rather
                # than grading an empty roster and writing a partial verdict.
                raise NativeWriteRefusalError(
                    "The native evaluation has no criterion left to grade"
                )
            eval_prompt = self._prompts.template_for(PromptKey.EVALUATION).render(
                {
                    **execution_criteria_variables(for_session),
                    **changeset_variables(changeset),
                },
            )
            nonlocal evaluation_attempt
            evaluation_attempt += 1
            observer = None
            if ctx.run_identity is not None:
                observer = NodeSessionObserver(
                    invocation=NodeInvocation(
                        run=ctx.run_identity,
                        node_key=PromptKey.EVALUATION.value,
                        invocation_key=json.dumps(
                            [
                                ctx.ralph_branch,
                                state["iteration"],
                                evaluation_attempt,
                                node_execution,
                            ],
                            separators=(",", ":"),
                        ),
                        declared_sessions=1,
                    ),
                    emit=writer,
                )
            skills = self._prompts.session_skills(PromptKey.EVALUATION, self._skills)
            policy = self._prompts.session_policy(PromptKey.EVALUATION)
            observe = None if observer is None else observer.observe
            if native_ref is None:
                result_event, rate_limit_rejected = await drain(
                    self._service.stream(
                        prompt=eval_prompt,
                        repo_path=ctx.repo_path,
                        repo_url=ctx.repo_url,
                        branch=evaluation_ref,
                        permission_mode=EVAL_PERMISSION_MODE,
                        allowed_tools=ToolPreset.EVALUATION,
                        skills=skills,
                        session_type=SessionType.TICKET_FIRE,
                        run_identity=ctx.run_identity,
                        # Evaluative: no lens is dispatched from here. Asking a
                        # template not to fan out is a request; an empty
                        # definition list is a guarantee.
                        agents=NO_SUBAGENTS,
                        session_policy=policy,
                        output_format={
                            "type": "json_schema",
                            "schema": ACCEPTANCE_CRITERIA_SCHEMA,
                        },
                        cache_key=ctx.cache_key,
                    ),
                    site="ralph_evaluator",
                    observe=observe,
                )
            else:
                # The loop owns the tree the verdict will be stamped for, so
                # the two facts that make the stamp true are read off that
                # tree before it is released: a workspace holding changes the
                # sha does not, or standing at another head, was graded as
                # somebody's working copy and not as the branch.
                async with owned_workspace(
                    self._evaluation_workspace(),
                    ref=native_ref,
                    repo_path=cwd,
                    cache_key=ctx.cache_key,
                ) as graded_in_path:
                    result_event, rate_limit_rejected = await drain(
                        self._service.stream_in_workspace(
                            prompt=eval_prompt,
                            workspace_path=graded_in_path,
                            permission_mode=EVAL_PERMISSION_MODE,
                            allowed_tools=ToolPreset.EVALUATION,
                            skills=skills,
                            session_type=SessionType.TICKET_FIRE,
                            run_identity=ctx.run_identity,
                            agents=NO_SUBAGENTS,
                            session_policy=policy,
                            output_format={
                                "type": "json_schema",
                                "schema": ACCEPTANCE_CRITERIA_SCHEMA,
                            },
                        ),
                        site="ralph_evaluator",
                        observe=observe,
                    )
                    workspace_stood = await read_workspace_head(
                        git=self._git, workspace=graded_in_path
                    ) == (native_ref, False)
            if observer is not None:
                observer.require_valid()

            if result_event is None or result_event.structured_output is None:
                msg = "Evaluator produced no structured output."
                raise soft_failure(
                    msg,
                    raise_site="ralph_evaluator",
                    result_event=result_event,
                    rate_limit_rejected=rate_limit_rejected,
                )

            output = AcceptanceCriteriaOutput.model_validate(
                result_event.structured_output,
            )
            graded_in = result_event.session_id
            graded_prompt = eval_prompt
            if (
                native_ref is not None
                and await self._resolve(cwd=cwd, ref=ctx.ralph_branch) != native_ref
            ):
                raise NativeWriteRefusalError(
                    "The native branch changed during evaluation"
                )
            dispatched = tuple(criteria)
            if reading and roster is not None:
                # An attempt that proves nothing is a missing reading of the
                # tree its sha names, which is no reason to un-carry an
                # earlier grading — that one's reading is arithmetic and
                # stands on its own, so the withholding after the dispatch
                # leaves these rows alone.
                output = iteration_output(
                    criteria=roster.criteria,
                    standing=prior,
                    reading=reading,
                    graded=output,
                )
            graded_output = output
            return grade_iteration(criteria, output)

        grade, unresolved, attempts = await until_permutation(
            dispatch=evaluate,
            check=require_permutation,
            max_attempts=self._fan_in_max_attempts,
            site="ralph_evaluator",
            log=self._log,
        )
        fan_in: FanInReport | None = None
        if unresolved is not None:
            # The bound is spent: grade what came back against the
            # DISPATCHED set anyway — a missing id fails and the
            # denominator stands — and put the holes on the wire so the
            # fail-closed verdict is legible as one.
            fan_in = fan_in_report(grade, attempts=attempts)
            await self._log.awarning(
                "fan_in_exhausted",
                site="ralph_evaluator",
                iteration=state["iteration"],
                attempts=attempts,
                dispatched_count=grade.dispatched_count,
                missing_ids=grade.missing_ids,
                unknown_ids=grade.unknown_ids,
                duplicate_ids=grade.duplicate_ids,
            )
        # One fold of every reading this attempt took, once per attempt rather
        # than once per fan-in round, and one withholding site: the attempt's
        # own output is graded again with the readings in hand, so a withheld
        # criterion grades failed carrying the reading that failed while every
        # correspondence fact of the grade is the value it already was. Grading
        # ``grade.results`` instead would empty ``unknown_ids`` and
        # ``duplicate_ids`` silently, because those rows are already gone.
        surviving: frozenset[CriterionId] = frozenset()
        if native_ref is not None and workspace_stood and self._mutation is not None:
            surviving = await self._mutation.survivors(
                criteria=dispatched,
                grade=grade,
                evaluation_prompt=graded_prompt,
                graded_sha=native_ref,
                repo_path=cwd,
                cache_key=ctx.cache_key,
            )
            # The removing session held write permission in a worktree at the
            # graded sha, and the node's own branch-moved refusal ran before
            # that session: a push made by it is caught here or nowhere.
            if await self._resolve(cwd=cwd, ref=ctx.ralph_branch) != native_ref:
                raise NativeWriteRefusalError(
                    "The native branch changed during the mutation reading"
                )
        # A criterion the reading carried or found lapsed was not read by this
        # attempt at all — its row is the reading's — so no reading this
        # attempt took can have failed for it, and it is left out here.
        held = {str(criterion) for criterion in reading}
        reasons = undemonstrated_reasons(
            results=[
                result
                for result in grade.results
                if str(result.criterion_id) not in held
            ],
            workspace_stood=workspace_stood,
            surviving_checks=surviving,
        )
        # Every path out of the dispatch that returned a grade set the output,
        # so the narrowing is the type's and not a second condition: no
        # reading is withheld from an attempt that never graded anything.
        if reasons and graded_output is not None:
            await self._log.awarning(
                "evaluation_undemonstrated",
                site="ralph_evaluator",
                iteration=state["iteration"],
                graded_sha=native_ref,
                reasons=sorted(
                    (str(key), reason.value) for key, reason in reasons.items()
                ),
            )
            grade = grade_iteration(dispatched, graded_output, undemonstrated=reasons)
        verdict = grade.verdict
        pending_failures = grade.failures
        reconciled = AcceptanceCriteriaOutput(
            criteria_results=grade.results,
            sherlock_flags=grade.sherlock_flags,
        )
        records = [
            *state["iteration_records"],
            IterationRecord(
                iteration=state["iteration"],
                passed_count=grade.passed_count,
                failing_criterion_ids=[f.criterion_id for f in pending_failures],
                commit_sha=state.get("iteration_commit_sha"),
            ),
        ]
        trajectory = fold_trajectory(records, plateau_window=self._plateau_window)
        event = WorkflowIterationEvent(
            iteration=state["iteration"],
            branch=ctx.ralph_branch,
            commit_sha=state.get("iteration_commit_sha"),
            verdict=verdict,
            evaluation=reconciled,
            trajectory=trajectory,
            fan_in=fan_in,
        )
        next_standing: tuple[CriterionCrossOff, ...] = ()
        # The base reading is taken once the grade has settled, and only for
        # the criteria this attempt read as passing: a fail claims nothing
        # about the branch, so it needs no base reading to be unproven. Here
        # rather than inside the dispatch above because the base tree does not
        # move between fan-in attempts and the passing set is only known once
        # the grade stands — one base session per iteration at most, which
        # ``max_iterations`` already bounds. A criterion the reading carried
        # or found lapsed was not graded by this attempt, so it is not read
        # at the base either: it is withheld from every session of this
        # iteration, and its cross-off is decided by the reading alone. A
        # grading that did not stand needs no guard of its own here: the
        # withholding above has already graded every result this attempt
        # graded failed, and the carried rows ``iteration_output`` puts back
        # are the reading's, subtracted below, so it presents no passing id
        # and the empty ``passing`` skips the reading. The same holds for a
        # check that survived the mutation reading: it is already failed, so
        # each criterion names the first reading that came back empty for it.
        #
        # What the base reading finds qualifies the cross-off and not the
        # verdict: the evaluator did read the changeset, so its verdict still
        # reaches the wire, and the pass it read of the base is recorded as
        # undemonstrated, naming the base reading, beside the readings above.
        at_base: Mapping[CriterionId, bool] = {}
        withheld: Mapping[CriterionId, UndemonstratedReason] = reasons
        if native_ref is not None:
            passing = frozenset(
                criterion
                for criterion in passed_ids(grade.results)
                if str(criterion) not in held
            )
            if passing:
                at_base = await self._base_reading(
                    ctx=ctx,
                    cwd=cwd,
                    criteria=[
                        criterion for criterion in dispatched if criterion.id in passing
                    ],
                    graded_sha=native_ref,
                    iteration=state["iteration"],
                )
            withheld = {**reasons, **base_reasons(passing=passing, at_base=at_base)}
        if native_ref is not None:
            # Before the event, so a consumer that sees iteration n can read
            # the board and find iteration n's cross-offs already on it. The
            # cadence is the evaluator's: this is the step that judged, and
            # nothing after the loop writes a cross-off.
            next_standing = await self._cross_off(
                ctx=ctx,
                grade=grade,
                dispatched=dispatched,
                graded_sha=native_ref,
                graded_in=graded_in,
                reasons=withheld,
                iteration=state["iteration"],
                standing=prior,
                reading=reading,
            )
            if escalator is not None:
                # After the move back, so a question never names a criterion
                # the board still shows as satisfied.
                #
                # The two fields are different sets, and the difference is
                # load-bearing. `lapsed` carries every lapsed grading,
                # including the ones that lapsed at an earlier head:
                # cross_offs_for re-emits a lapsed cross-off with
                # state=lapsed into the next standing, and held_standing
                # puts it straight back into `lapsed`, which is what keeps
                # it withheld from every later session. `newly_lapsed`
                # carries only the gradings that crossed over at this head.
                #
                # So the transition field is the guard, not merely the
                # honest name: handing the whole lapsed set over here would
                # ask the same question again at every iteration after the
                # one that lapsed. The three-iteration lapse case in
                # tests/chains/test_lane_state_loop.py reds on that second
                # raise if either the guard above or this argument is moved
                # to `standing.lapsed`.
                await escalator.raise_lapses(
                    lane=self._lane_binding(ctx),
                    lapsed=standing.newly_lapsed,
                    head_sha=native_ref,
                )
        writer(event)
        if (
            trajectory.plateaued
            and not gate_cleared(verdict)
            and state["iteration"] < self._max_iterations
        ):
            await self._log.awarning(
                "loop_plateau_stop",
                iterations_used=state["iteration"],
                iterations_remaining=self._max_iterations - state["iteration"],
                never_passed_ids=trajectory.never_passed_ids,
            )
        return {
            "verdict": verdict,
            "pending_failures": pending_failures,
            "iteration_records": records,
            # The loop's own memory of what it has graded, kept as graph state
            # rather than on the receipt: the receipt is what the post-loop
            # roster check compares, and this is not part of that comparison.
            "standing": next_standing,
            "outcome": (
                EvaluatedRalphOutcome(event=event, criteria=dispatched)
                if native_ref is None
                else NativeEvaluatedRalphOutcome(
                    event=event, criteria=dispatched, head_sha=native_ref
                )
            ),
        }

    async def _cross_off(
        self,
        *,
        ctx: RalphLoopContext,
        grade: IterationGrade,
        dispatched: Sequence[ExecutionCriterion],
        graded_sha: str,
        graded_in: str,
        reasons: Mapping[CriterionId, UndemonstratedReason],
        iteration: int,
        standing: Sequence[CriterionCrossOff],
        reading: Mapping[CriterionRef, GradedState],
    ) -> tuple[CriterionCrossOff, ...]:
        """Put what this attempt decided on the criteria it decided about.

        The whole roster the attempt dispatched is read against the whole
        grade, because a verdict is a reading of the roster and a partial one
        is no reading of it. What is WRITTEN is narrower: a criterion whose
        earlier grading still stands is already finished with the row it was
        graded with, so nothing is written for it at all — neither its
        Evidence row nor its state — and a second tick per iteration would be
        a second chance for a third party's amendment to refuse the whole act.
        The freshly graded ones and the lapsed ones are the pairs this attempt
        decided, and they go over in roster order.

        The whole tuple comes back as the next iteration's standing, carried
        gradings included: what stands is what the loop has graded, not what
        it last wrote.

        The refusal below only narrows the closure's typed value: the set is
        read before any evaluation on this arm and an empty one is refused
        there, so no grade reaches here without a roster behind it.
        """
        # The execute node resolved this writer at its entry, before the
        # session this grade is; this is the same resolver, not a second
        # refusal site.
        lane_state = self._lane_writer()
        roster = held_roster(dispatched)
        if roster is None:
            raise NativeWriteRefusalError(
                "The native evaluation graded no tracker criterion in a session"
            )
        cross_offs = cross_offs_for(
            results=grade.results,
            graded_sha=graded_sha,
            observation=evaluation_observation(
                session_id=graded_in, iteration=iteration
            ),
            reasons=reasons,
            standing=standing,
            reading=reading,
        )
        decided = tuple(
            pair
            for pair in zip(roster.criteria, cross_offs, strict=True)
            if reading.get(pair[1].criterion) is not GradedState.counted
        )
        await lane_state.write_cross_offs(
            lane=self._lane_binding(ctx),
            dispatched=tuple(criterion for criterion, _ in decided),
            cross_offs=tuple(cross_off for _, cross_off in decided),
        )
        return cross_offs

    async def _moved_since(
        self,
        *,
        cwd: str,
        prior: Sequence[CriterionCrossOff],
        head_sha: str,
    ) -> dict[str, ChangesetDigest]:
        """The changed paths since each standing path-bound grading's own sha.

        One read per distinct graded sha, in a settled order. A cheap grading
        needs no digest and gets none: it is owed again on any head move, so
        asking what moved would buy an answer nobody reads.

        The interval is that grading's own sha to *head_sha*, which is the
        only interval that answers whether what the grading examined has
        changed since it was taken. The lane's own base-to-head digest cannot:
        it would call a criterion's prefixes moved because of the very commit
        that graded it.

        Each digest is the commit record's own reading of that interval,
        obtained through the Git source port, and never a diff of a working
        tree: the paths it names are the ones the commits between those two
        revisions changed, so nothing uncommitted in any workspace can move a
        grading, and the same two revisions read the same way whoever asks.
        """
        return {
            sha: await self._git.diff_summary(cwd=cwd, base_ref=sha, head_ref=head_sha)
            for sha in sorted(
                {
                    cross_off.evidence.graded_sha
                    for cross_off in prior
                    if cross_off.state is CrossOffState.passed
                    and cross_off.rederivation_class in PATH_BOUND_CLASSES
                }
            )
        }

    async def _base_reading(
        self,
        *,
        ctx: RalphLoopContext,
        cwd: str,
        criteria: Sequence[ExecutionCriterion],
        graded_sha: str,
        iteration: int,
    ) -> Mapping[CriterionId, bool]:
        """What the lane's base already satisfies of *criteria*, if anything.

        Resolves rather than raises: a reading that could not be taken is the
        empty mapping, which the fold reads as no reading of any of these
        criteria and therefore as no pass of the branch. An absent id and an
        id answered ``False`` are two different facts to the fold, so this
        returns a mapping and never a verdict of its own.

        Every row here goes to the run's log and nowhere else: the session's
        ``command`` is its own text, and no surface of this run carries it.
        """
        try:
            base_sha, output = await self._checks_at_base(
                ctx=ctx, cwd=cwd, criteria=criteria
            )
        except BaseReadingUnavailableError as refusal:
            await self._log.awarning(
                "base_reading_unavailable",
                site="ralph_evaluator",
                iteration=iteration,
                base_ref=refusal.base_ref,
                reason=refusal.reason,
                criterion_ids=[criterion.id for criterion in criteria],
            )
            return {}
        answers = base_answers(output)
        for result in output.base_check_results:
            # Keyed to the criterion, because the fact is the criterion's: a
            # row of sorted ids would have to be taken apart again by anything
            # that wanted to say which criterion this happened to.
            if answers.get(result.criterion_id) is True:
                await self._log.awarning(
                    "criterion_satisfied_at_base",
                    criterion=result.criterion_id,
                    graded_sha=graded_sha,
                    base_sha=base_sha,
                    command=result.command,
                )
        return answers

    async def _checks_at_base(
        self,
        *,
        ctx: RalphLoopContext,
        cwd: str,
        criteria: Sequence[ExecutionCriterion],
    ) -> tuple[str, BaseCheckOutput]:
        """Run *criteria*'s own checks in a tree this loop owns at the base.

        The recorded base's ref resolved to a commit, never the name: a tree
        acquired at a name follows the name, and what this reads has to be the
        commit the base resolved to. Acquired off the clone rather than off the
        grading tree, so the base tree's lifetime does not nest inside another
        worktree's, and only after the grading tree was released, so the lane
        owns one tree at a time.

        The tree is asked what the grading tree is asked — its head and whether
        it holds changes — before the session and again after it, because a
        tree that moved under the session answers for no commit.
        """
        try:
            base_sha = await self._resolve(cwd=cwd, ref=ctx.base_branch)
        except NativeWriteRefusalError as unreadable:
            raise BaseReadingUnavailableError(
                base_ref=ctx.base_branch,
                reason="the lane's base ref cannot be read",
            ) from unreadable
        prompt = self._prompts.template_for(PromptKey.BASE_CHECK).render(
            {
                **execution_criteria_variables(criteria),
                "base_sha": base_sha,
            },
        )
        try:
            async with owned_workspace(
                self._evaluation_workspace(),
                ref=base_sha,
                repo_path=cwd,
                cache_key=ctx.cache_key,
            ) as base_path:

                async def require_base() -> None:
                    if await read_workspace_head(
                        git=self._git, workspace=base_path
                    ) != (base_sha, False):
                        raise BaseReadingUnavailableError(
                            base_ref=ctx.base_branch,
                            reason="the tree is not the base commit",
                        )

                await require_base()
                try:
                    output = BaseCheckOutput.model_validate(
                        await judge_in_workspace(
                            runner=self._service,
                            prompts=self._prompts,
                            skills=self._skills,
                            workspace=base_path,
                            key=PromptKey.BASE_CHECK,
                            prompt=prompt,
                            output_schema=BASE_CHECK_SCHEMA,
                            site="ralph_evaluator",
                            session_type=SessionType.TICKET_FIRE,
                            failure_message=(
                                "The checks at the base produced no structured answer."
                            ),
                        )
                    )
                except (NoStructuredOutputError, ValidationError) as unreadable:
                    raise BaseReadingUnavailableError(
                        base_ref=ctx.base_branch,
                        reason="the checks at the base returned no usable answer",
                    ) from unreadable
                await require_base()
        except WorkspaceError as refused:
            raise BaseReadingUnavailableError(
                base_ref=ctx.base_branch,
                reason="a tree at the base was refused",
            ) from refused
        return base_sha, output

    async def _resolve(self, *, cwd: str, ref: str) -> str:
        """The complete sha *ref* names in *cwd*, or this loop's own refusal.

        Called for the branch the iteration is graded on and for the head of
        the workspace it was graded in, because both answers are the same
        question asked of two trees.
        """
        if self._source is None:
            raise NativeWriteRefusalError(
                "Native execution requires its Git source reader"
            )
        try:
            return await self._source.resolve_commit(cwd=cwd, ref=ref)
        except GitSourceReadError as exc:
            raise NativeWriteRefusalError(
                "The evaluated native ref cannot be read"
            ) from exc

    def _evaluation_workspace(self) -> WorkspaceProvider:
        """The provider the tree an evaluation is graded in is acquired from."""
        if self._workspace is None:
            raise NativeWriteRefusalError(
                "Native evaluation requires its workspace provider"
            )
        return self._workspace

    def _lane_writer(self) -> LaneStateWriter:
        """The writer both of this loop's board writes go through."""
        if self._lane_state is None:
            raise NativeWriteRefusalError(
                "Native execution requires the lane state writer"
            )
        return self._lane_state

    def _lapse_escalator(self) -> LaneLapseEscalator:
        """The role a lapsed observation's question is raised through."""
        if self._lapse_escalations is None:
            raise NativeWriteRefusalError(
                "Native execution requires the lapse escalation writer"
            )
        return self._lapse_escalations

    def _route_after_execute(self, state: RalphLoopState) -> str:
        if state.get("amendment_blocked", False):
            # No code/evaluation observation was produced. Keep the actual
            # prior failures and trajectory; only the attempt budget advances.
            return self._should_continue(state)
        return "evaluate"

    def _should_continue(
        self,
        state: RalphLoopState,
    ) -> str:
        if gate_cleared(state["verdict"]) and not state.get("amendment_blocked", False):
            return END
        if state["iteration"] >= self._max_iterations:
            return END
        trajectory = fold_trajectory(
            state["iteration_records"],
            plateau_window=self._plateau_window,
        )
        if trajectory.plateaued:
            return END
        return "execute"
