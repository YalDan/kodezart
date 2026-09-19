"""Assemble and run the shared fire graph from its concrete phases."""

from collections.abc import AsyncIterator
from functools import partial
from typing import assert_never

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy

from kodezart.chains.criteria import (
    require_current_native_snapshot,
    revalidate_criteria,
)
from kodezart.chains.fire_consolidation import FireConsolidation
from kodezart.chains.fire_implementation import FireImplementation
from kodezart.chains.fire_remediation import FireRemediation
from kodezart.chains.fire_review import FireReview
from kodezart.chains.fire_specification import FireSpecification
from kodezart.core.protocols import FireCriteriaSource
from kodezart.core.retry import DelayFloor, RetryFloor, should_retry
from kodezart.domain.accept_gate import (
    gate_cleared,
)
from kodezart.domain.agent import mint_lane_branches
from kodezart.domain.base_scope import scope_base
from kodezart.domain.criteria_feasibility import (
    demands_regeneration,
)
from kodezart.domain.errors import (
    ScopedExecutionUnavailableError,
)
from kodezart.domain.git_url import resolve_repo_url
from kodezart.domain.outcome import classify_outcome
from kodezart.domain.thread_id import workflow_thread_id
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    AgentEvent,
    WorkflowCompleteEvent,
)
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.gating import (
    RepoVisibility,
)
from kodezart.types.domain.lane_entry import (
    DeliverOnlyLane,
    LaneEntry,
    NewLane,
    ResumedLane,
)
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import AllowedTools, PermissionMode
from kodezart.types.domain.workflow import (
    ExecutionContext,
    WorkflowState,
)

#: The compiled shape both compositions produce.
FireGraph = CompiledStateGraph[WorkflowState, None, WorkflowState, WorkflowState]


class RalphWorkflowEngine:
    """One node set, two compositions, over one shared run state.

    The arms differ only in where the fire's subject and criteria come
    from.  The authored arm generates a ticket and a criteria set in the
    graph.  The tracker-native arm is EXECUTION-ONLY: it generates
    neither, and re-validates at head the criterion sub-issues an
    organize pass already staged, before it reaches the loop.
    """

    def __init__(
        self,
        *,
        specification: FireSpecification,
        implementation: FireImplementation,
        consolidation: FireConsolidation,
        review: FireReview,
        remediation: FireRemediation,
        git_base_url: str,
        checkpointer: BaseCheckpointSaver[str] | None = None,
        retry_max_attempts: int,
        retry_initial_interval: float,
        delay_floor_for: DelayFloor,
        criteria: FireCriteriaSource | None = None,
    ) -> None:
        self.specification = specification
        self.implementation = implementation
        self.consolidation = consolidation
        self.review = review
        self.remediation = remediation
        self.criteria = criteria
        self._git_base_url = git_base_url
        self.checkpointer = checkpointer
        self.retry = RetryPolicy(
            max_attempts=retry_max_attempts,
            initial_interval=retry_initial_interval,
            retry_on=should_retry,
        )
        self.floor = RetryFloor(delay_floor_for)
        self.graph: FireGraph = self._build_graph(criteria=None).compile(
            checkpointer=self.checkpointer
        )
        self.native_graph: FireGraph | None = (
            None
            if criteria is None
            else self._build_graph(criteria=criteria).compile(
                checkpointer=self.checkpointer
            )
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
        """Execute a fire and expose its delivery-free terminal."""
        initial_state, config = self.prepare(
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
            surface_holder=cache_key if scope is not None else None,
            run_identity=run_identity,
        )
        terminal: WorkflowCompleteEvent | None = None
        async for event in self._composition(scope).astream(
            initial_state,
            config=config,
            stream_mode="custom",
        ):
            if not isinstance(event, AgentEvent):
                raise TypeError(f"Expected AgentEvent, got {type(event).__name__}")
            if isinstance(event, WorkflowCompleteEvent):
                terminal = event
            yield event
        if terminal is None:
            raise RuntimeError("Fire graph emitted no terminal")
        await self.consolidation.cleanup_backups(terminal, config)

    def _composition(self, scope: ScopeRef | None) -> FireGraph:
        """The composition this run's addressing selects, or its refusal.

        An addressed run is a tracker-native fire: the subject is the
        issue the scope names, and the criteria are its own sub-issues.
        A deployment with no tracker-native criteria stage wired cannot
        serve one, and says so before any node runs.
        """
        if scope is None:
            return self.graph
        if self.native_graph is None:
            msg = "Scoped execution requires the scope entry pipeline"
            raise ScopedExecutionUnavailableError(msg, ref=scope)
        if scope.kind is not ScopeKind.ISSUE:
            msg = "A tracker-native fire is addressed to one issue"
            raise ScopedExecutionUnavailableError(msg, ref=scope)
        return self.native_graph

    def _build_graph(
        self,
        *,
        criteria: FireCriteriaSource | None,
    ) -> StateGraph[WorkflowState, None, WorkflowState, WorkflowState]:
        """One node set; *criteria* selects the tracker-native composition.

        Everything from the loop onward is the same nodes and the same
        routes on both arms.  The arms differ in exactly one place — the
        criteria gate the entry, the validation route and a remediation
        round all converge on — so the native arm HOLDS no ticket-,
        criteria- or branch-generation node rather than merely skipping
        one.  A native lane's branch names are arithmetic over its issue
        key, drawn in ``prepare``; only the authored arm asks a model for
        a name, because only there is the name a summary of prose.
        """
        graph: StateGraph[WorkflowState, None, WorkflowState, WorkflowState] = (
            StateGraph(WorkflowState)
        )
        graph.add_node(
            "resolve_visibility",
            self.floor(self.specification.resolve_visibility),
            retry_policy=self.retry,
        )
        if criteria is None:
            graph.add_node(
                "generate_branch",
                self.floor(self.specification.generate_branch),
                retry_policy=self.retry,
            )
            graph.add_node(
                "generate_ticket",
                self.floor(self.specification.generate_ticket),
                retry_policy=self.retry,
            )
            graph.add_node(
                "generate_criteria",
                self.floor(self.specification.generate_criteria),
                retry_policy=self.retry,
            )
            graph.add_node(
                "validate_criteria",
                self.floor(self.specification.validate_criteria),
                retry_policy=self.retry,
            )
        else:
            graph.add_node(
                "revalidate_criteria",
                self.floor(partial(revalidate_criteria, source=criteria)),
                retry_policy=self.retry,
            )
        graph.add_node(
            "run_ralph_loop",
            self.floor(self.implementation.run_ralph_loop),
            retry_policy=self.retry,
        )
        graph.add_node(
            "merge_to_feature",
            self.floor(self._merge_to_feature),
            retry_policy=self.retry,
        )
        graph.add_node(
            "land_best_iteration",
            self.floor(self._land_best_iteration),
            retry_policy=self.retry,
        )
        graph.add_node(
            "review_against_ticket",
            self.floor(self.review.review_against_ticket),
            retry_policy=self.retry,
        )
        graph.add_node(
            "remediate",
            self.floor(self.remediation.remediate),
            retry_policy=self.retry,
        )
        graph.add_node("complete", self._complete_node)

        # The two artifact writes are the authored arm's: both are keyed
        # off a generated ticket, which the native arm has not got.
        persists = criteria is None and self.implementation.persists_artifacts
        if persists:
            graph.add_node(
                "persist_ticket",
                self.floor(self.implementation.persist_ticket),
                retry_policy=self.retry,
            )
            graph.add_node(
                "persist_artifacts",
                self.floor(self.implementation.persist_artifacts),
                retry_policy=self.retry,
            )

        # The one node the arms disagree about. The entry router and a
        # remediation round both name the criteria gate; which node that
        # is, is the whole difference between the compositions.
        gate = "generate_criteria" if criteria is None else "revalidate_criteria"
        graph.add_conditional_edges(
            START,
            self._route_entry,
            {"resolve_visibility": "resolve_visibility", "generate_criteria": gate},
        )
        if criteria is None:
            graph.add_edge("resolve_visibility", "generate_branch")
            graph.add_edge("generate_branch", "generate_ticket")
            if persists:
                graph.add_edge("generate_ticket", "persist_ticket")
                graph.add_edge("persist_ticket", "generate_criteria")
            else:
                graph.add_edge("generate_ticket", "generate_criteria")
            graph.add_edge("generate_criteria", "validate_criteria")
            proceed = "persist_artifacts" if persists else "run_ralph_loop"
            graph.add_conditional_edges(
                "validate_criteria",
                self._route_after_validation,
                {
                    "generate_criteria": "generate_criteria",
                    proceed: proceed,
                    "complete": "complete",
                },
            )
            if persists:
                graph.add_edge("persist_artifacts", "run_ralph_loop")
        else:
            graph.add_edge("resolve_visibility", "revalidate_criteria")
            graph.add_conditional_edges(
                "revalidate_criteria",
                self._route_after_revalidation,
                {
                    "run_ralph_loop": "run_ralph_loop",
                    "merge_to_feature": "merge_to_feature",
                },
            )
        graph.add_edge("run_ralph_loop", "merge_to_feature")
        graph.add_conditional_edges(
            "merge_to_feature",
            self._route_after_merge,
            {
                "review_against_ticket": "review_against_ticket",
                "remediate": "remediate",
                "land_best_iteration": "land_best_iteration",
                "complete": "complete",
            },
        )
        graph.add_edge("land_best_iteration", "complete")
        graph.add_conditional_edges(
            "review_against_ticket",
            self._route_after_review,
            {"remediate": "remediate", "complete": "complete"},
        )
        graph.add_edge("remediate", gate)
        graph.add_edge("complete", END)
        return graph

    def _route_after_validation(self, state: WorkflowState) -> str:
        """Halt, regenerate, or proceed — computed from the sweep alone."""
        if state["criteria_infeasible"]:
            return "complete"
        validation = state["criteria_validation"]
        if validation is not None and demands_regeneration(validation):
            return "generate_criteria"
        return (
            "persist_artifacts"
            if self.implementation.persists_artifacts
            else "run_ralph_loop"
        )

    def _route_after_revalidation(self, state: WorkflowState) -> str:
        """A lane entered to deliver goes to consolidation, never to the loop.

        What that lane is missing is not work: its record shows every
        criterion crossed off and no pull request, so what it never reached
        is the consolidation, review and delivery that follow the loop, and
        an iteration would open a session to close an obligation nobody
        holds it to.

        A remediation round is the exception, and is not a special case of
        this entry: the round is a fresh obligation drafted against what the
        review rejected, so it takes the loop exactly as it does for any
        other entry, and the loop continues the recorded branch rather than
        cutting a new one.
        """
        if (
            isinstance(state["lane_entry"], DeliverOnlyLane)
            and state["remediation_ticket"] is None
        ):
            return "merge_to_feature"
        return "run_ralph_loop"

    def _route_after_merge(self, state: WorkflowState) -> str:
        """Only review merged code; land what a loop exit produced.

        The three arms are the three ways the consolidation node can end.
        Unmerged WITH an error is a divergent accepted run — its work is
        already on the feature branch and the terminal reports the
        divergence.  Unmerged with NO error is the loop exit, which is
        the run this lane exists to stop stranding.
        """
        if state["merged"]:
            return "review_against_ticket"
        if state["merge_error"] is not None:
            return "complete"
        if self.remediation.rounds_remain(state):
            return "remediate"
        return "land_best_iteration"

    def _route_after_review(self, state: WorkflowState) -> str:
        if state["review_passed"]:
            return "complete"
        if self.remediation.rounds_remain(state):
            return "remediate"
        return "complete"

    async def _merge_to_feature(
        self, state: WorkflowState, config: RunnableConfig
    ) -> dict[str, object]:
        """Recheck the judgment before consolidation, including checkpoint replay."""
        await require_current_native_snapshot(state, reader=self.criteria)
        return await self.consolidation.merge_to_feature(state, config)

    async def _land_best_iteration(
        self, state: WorkflowState, config: RunnableConfig
    ) -> dict[str, object]:
        """Keep best-iteration publication tied to the judged obligations."""
        await require_current_native_snapshot(state, reader=self.criteria)
        return await self.consolidation.land_best_iteration(state, config)

    async def _complete_node(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Emit the fire terminal only under current criterion authority."""
        await require_current_native_snapshot(state, reader=self.criteria)
        _ = config
        writer = get_stream_writer()
        writer(
            WorkflowCompleteEvent(
                feature_branch=state["feature_branch"],
                ralph_branch=state["ralph_branch"],
                total_iterations=state["total_iterations"],
                accepted=gate_cleared(state["accept_verdict"]),
                outcome=classify_outcome(state),
                merged=state["merged"],
                final_commit_sha=state["feature_tip_sha"],
                merge_error=state["merge_error"],
                trajectory=state["trajectory"],
                criteria_validation=state["criteria_validation"],
            )
        )

        return {}

    def prepare(
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
        surface_holder: str | None = None,
        entry: LaneEntry | None = None,
    ) -> tuple[WorkflowState, RunnableConfig]:
        """Execute the full workflow pipeline.

        *base_spec* is the lane's recorded base and *implied_base* is the
        base its blockers imply now; the run refuses before any node when
        they differ, because a criterion graded against a base that has
        moved is about a tree that no longer exists.

        *entry* is how the walker decided this lane enters.  On the native
        arm ``None`` is read as a new lane, so a native fire started without
        a walker still names its branches with the plain function; on the
        authored arm it is not read at all, because that arm's own node
        names the branch.

        ``cache_key`` IS the LangGraph thread id: the caller's job id
        addresses this run's checkpoints.
        """
        if scope is not None:
            # Refuses here, before any node, when no composition serves
            # the address. An addressed run's subject IS the issue the
            # scope names, so a second, disagreeing identity is refused
            # rather than silently preferred either way.
            self._composition(scope)
            if issue_key is not None and issue_key != scope.key:
                msg = "The addressed issue and the run's issue key disagree"
                raise ScopedExecutionUnavailableError(msg, ref=scope)
            issue_key = scope.key
        # Public HTTP resubmission still needs an explicit existing-job resume
        # contract. Native parent None-resume preserves the nested loop/phase
        # checkpoints; authored ticket-generation resume remains separate.
        # Refuses here, before any node: a stale baseline produces no
        # scope verdict at all rather than one graded against the wrong tree.
        scope_base(base_spec, implied_base)

        resolved_url = (
            resolve_repo_url(repo_url, self._git_base_url)
            if repo_url is not None
            else None
        )

        ctx = ExecutionContext(
            prompt=prompt,
            repo_path=repo_path,
            repo_url=resolved_url,
            cache_key=cache_key,
            run_identity=run_identity,
            surface_holder=surface_holder,
            base_spec=base_spec,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
        )
        configurable: dict[str, object] = ctx.model_dump()
        if self.checkpointer is not None:
            configurable["thread_id"] = workflow_thread_id(cache_key)

        config: RunnableConfig = {"configurable": configurable}

        # The native arm holds no branch-generation node, so the lane's two
        # names come from its entry. A new lane draws them from its issue
        # key, and a key that could not stand inside a ref refuses here,
        # before any node runs. A recorded lane continues the branches its
        # record named and cuts nothing: work_base_ref IS the loop branch,
        # which is how the loop is told the branch already exists.
        feature_branch, ralph_branch = "", ""
        work_base_ref = base_spec.base_branch
        accept_verdict = AcceptVerdict.rejected
        if scope is not None:
            entered: LaneEntry = entry if entry is not None else NewLane()
            match entered:
                case NewLane():
                    feature_branch, ralph_branch = mint_lane_branches(scope.key)
                case ResumedLane():
                    feature_branch = entered.deliverable_branch
                    ralph_branch = entered.loop_branch
                    work_base_ref = entered.loop_branch
                case DeliverOnlyLane():
                    # The same two recorded branches a resumed lane
                    # continues, plus the one thing this entry asserts and
                    # a resumed one does not: every criterion of the
                    # subtree is finished, which is what acceptance means
                    # once the lane's own evaluation crossed them off. The
                    # gate consolidation opens with reads that verdict, and
                    # the step before it reads this entry's kind off the
                    # state to route there at all.
                    feature_branch = entered.deliverable_branch
                    ralph_branch = entered.loop_branch
                    work_base_ref = entered.loop_branch
                    accept_verdict = AcceptVerdict.accepted
                case _:
                    assert_never(entered)

        initial_state: WorkflowState = {
            "issue_key": issue_key,
            "lane_entry": entry,
            "feature_branch": feature_branch,
            "ralph_branch": ralph_branch,
            "work_base_ref": work_base_ref,
            "fire_spec": None,
            "acceptance_criteria": [],
            "criterion_set": None,
            "criteria_validation": None,
            "criteria_regeneration_rounds": 0,
            "criteria_infeasible": False,
            "accept_verdict": accept_verdict,
            "flagged_items": [],
            "total_iterations": 0,
            "feature_tip_sha": None,
            "review_base_sha": None,
            "review_head_sha": None,
            "merged": False,
            "merge_error": None,
            "review_passed": False,
            "review_feedback": None,
            "remediation_rounds_used": 0,
            "remediation_ticket": None,
            "remediation_entry": None,
            "best_iteration_sha": None,
            "repo_url": resolved_url,
            "repo_visibility": RepoVisibility.UNKNOWN,
            "trajectory": None,
        }

        return initial_state, config

    def _route_entry(self, state: WorkflowState) -> str:
        """An explicit drafted remediation resumes at the shared criteria gate."""
        if state["remediation_ticket"] is not None:
            return "generate_criteria"
        return "resolve_visibility"
