"""Ralph quality-gating loop — execute + evaluate until accepted or exhausted."""

import json
from collections.abc import AsyncIterator, Sequence
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
from kodezart.core.errors import soft_failure
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.node_sessions import NodeSessionObserver
from kodezart.core.protocols import (
    AfterPublish,
    AgentRunner,
    FireCriteriaReader,
    GitService,
    GitSourceReader,
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
    cross_offs_for,
    evaluation_observation,
    undemonstrated_output,
)
from kodezart.domain.errors import GitSourceReadError
from kodezart.domain.fan_in import fan_in_report, require_permutation
from kodezart.domain.prompt_variables import (
    changeset_variables,
    execution_criteria_variables,
    tracker_checks_section,
)
from kodezart.domain.thread_id import ralph_thread_id
from kodezart.domain.trajectory import fold_trajectory
from kodezart.services.native_amendments import NativeAmendments
from kodezart.services.owned_workspace import owned_workspace
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    ACCEPTANCE_CRITERIA_SCHEMA,
    AcceptanceCriteriaOutput,
    AgentEvent,
    NativeAmendmentEvent,
    ResultEvent,
    WorkflowIterationEvent,
)
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.criteria import (
    ExecutionCriterion,
    FanInReport,
    TrackerCriterionSet,
)
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
    ) -> None:
        self._service = service
        self._criteria_reader = criteria_reader
        self._amendments = amendments
        self._source = source
        self._lane_state = lane_state
        self._workspace = workspace
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
            if self._lane_state is None:
                raise NativeWriteRefusalError(
                    "Native execution requires the lane state writer"
                )
            amendments = self._amendments
            after_publish = self._record_commit(
                self._lane_state, self._lane_binding(ctx)
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
            base_branch=(ctx.work_base_ref if is_first else ctx.ralph_branch),
            branch_name=ctx.feature_branch,
            ralph_branch=ctx.ralph_branch,
            permission_mode=ctx.permission_mode,
            allowed_tools=ctx.allowed_tools,
            skills=self._prompts.session_skills(PromptKey.IMPLEMENTATION, self._skills),
            session_type=SessionType.TICKET_FIRE,
            run_identity=ctx.run_identity,
            session_policy=self._prompts.session_policy(PromptKey.IMPLEMENTATION),
            visibility=ctx.repo_visibility,
            create_branch=is_first,
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
                    event=refusal, last_iteration=last
                )
        return update

    def _held(
        self, *, criteria: Sequence[ExecutionCriterion], outcome: RalphOutcome
    ) -> TrackerCriterionSet | None:
        """Everything this fire took on: its entry roster and what it graded.

        The obligation can grow mid-run: the amendment write-back puts an
        amended criterion back in Todo and the next iteration owes it. Such
        a criterion is part of what this fire took on, so once the fire's own
        evaluation finishes it, the roster that keeps it inside the set has
        to hold it too. Nothing is ever removed from the roster this way —
        it only admits a criterion this loop itself graded.
        """
        graded: Sequence[ExecutionCriterion] = (
            outcome.criteria
            if isinstance(outcome, (EvaluatedRalphOutcome, NativeEvaluatedRalphOutcome))
            else ()
        )
        return held_roster([*criteria, *graded])

    def _lane_binding(self, ctx: RalphLoopContext) -> LaneBinding:
        """The lane this node commits for, as the record write needs it."""
        if ctx.tracker_spec is None or ctx.surface_holder is None:
            raise NativeWriteRefusalError(
                "The lane state record has no lane and holder to name"
            )
        return LaneBinding(
            lane_key=ctx.tracker_spec.subject,
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
        if ctx.tracker_spec is not None:
            # The tree a native verdict is about is this node's own, so the
            # provider it comes from is settled before the first read: a loop
            # that cannot own that tree refuses without opening a session.
            self._evaluation_workspace()
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

        dispatched = tuple(ctx.acceptance_criteria)
        # The session the standing grade came from, carried out of the
        # retried closure: the Evidence row points back at the grading that
        # produced the verdict, and on a re-dispatch that is the last one.
        graded_in: str | None = None
        # Whether the tree the standing grade was read from was the one the
        # graded sha names. The authored arm has no sha to stand for, so its
        # readings are of the ref it asked for and nothing else is claimed.
        demonstrated = True

        async def evaluate() -> IterationGrade:
            nonlocal dispatched, graded_in, demonstrated
            criteria: list[ExecutionCriterion] = ctx.acceptance_criteria
            if ctx.tracker_spec is not None:
                snapshot = await current_native_criteria(
                    spec=ctx.tracker_spec,
                    reader=self._criteria_reader,
                    held=self._held(
                        criteria=ctx.acceptance_criteria, outcome=state["outcome"]
                    ),
                )
                criteria = list(snapshot.criteria)
            eval_prompt = self._prompts.template_for(PromptKey.EVALUATION).render(
                {
                    **execution_criteria_variables(criteria),
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
                    demonstrated = not await self._git.has_changes(graded_in_path) and (
                        await self._resolve(cwd=graded_in_path, ref="HEAD")
                        == native_ref
                    )
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
            if (
                native_ref is not None
                and await self._resolve(cwd=cwd, ref=ctx.ralph_branch) != native_ref
            ):
                raise NativeWriteRefusalError(
                    "The native branch changed during evaluation"
                )
            dispatched = tuple(criteria)
            if not demonstrated:
                await self._log.awarning(
                    "evaluation_undemonstrated",
                    site="ralph_evaluator",
                    iteration=state["iteration"],
                    graded_sha=native_ref,
                )
                output = undemonstrated_output(output)
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
        if native_ref is not None:
            # Before the event, so a consumer that sees iteration n can read
            # the board and find iteration n's cross-offs already on it. The
            # cadence is the evaluator's: this is the step that judged, and
            # nothing after the loop writes a cross-off.
            await self._cross_off(
                ctx=ctx,
                grade=grade,
                dispatched=dispatched,
                graded_sha=native_ref,
                graded_in=graded_in,
                demonstrated=demonstrated,
                iteration=state["iteration"],
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
        graded_in: str | None,
        demonstrated: bool,
        iteration: int,
    ) -> None:
        """Put this attempt's verdict on the criteria it was graded against.

        The whole roster the attempt dispatched is handed over with the
        whole grade, because a verdict is a reading of the roster and a
        partial one is no reading of it. A grade with no session behind it
        is not one this loop produced, so it is refused rather than stamped
        with a pointer that leads nowhere.
        """
        if self._lane_state is None:
            raise NativeWriteRefusalError(
                "Native evaluation requires the lane state writer"
            )
        roster = held_roster(dispatched)
        if roster is None or graded_in is None:
            raise NativeWriteRefusalError(
                "The native evaluation graded no tracker criterion in a session"
            )
        await self._lane_state.write_cross_offs(
            lane=self._lane_binding(ctx),
            dispatched=roster.criteria,
            cross_offs=cross_offs_for(
                results=grade.results,
                graded_sha=graded_sha,
                observation=evaluation_observation(
                    session_id=graded_in, iteration=iteration
                ),
                demonstrated=demonstrated,
            ),
        )

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
