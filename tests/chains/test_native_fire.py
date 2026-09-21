"""The tracker-native fire: an execution-only graph over staged criteria.

Two contracts live here.  The graph HOLDS no ticket- or criteria-generation
node and reaches its loop only through the pre-loop re-validation step; the
step admits the subject through the tracker's own read and measures what the
fire owes over that subject's whole subtree, in one reading, and nothing
carried alongside that read stands in for it.
"""

import inspect
import re
from unittest.mock import Mock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from kodezart.adapters.job_registry import InMemoryJobRegistry
from kodezart.chains.criteria import (
    TrackerCriteria,
    require_current_native_snapshot,
    revalidate_criteria,
)
from kodezart.chains.fire_consolidation import FireConsolidation
from kodezart.chains.fire_implementation import FireImplementation
from kodezart.chains.fire_remediation import FireRemediation
from kodezart.chains.fire_review import FireReview
from kodezart.chains.fire_specification import FireSpecification
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.chains.remediation import RemediationChain
from kodezart.config.write_back import WriteBackSettings
from kodezart.core.protocols import QualityGate
from kodezart.domain.errors import (
    EmptyFireCriteriaError,
    FireSpecEntryError,
    InvalidFireCriterionError,
    LaneEntryError,
    PersistedCriterionSetError,
    ScopedExecutionUnavailableError,
    ScopeReadError,
)
from kodezart.domain.thread_id import workflow_thread_id
from kodezart.domain.workflow_state import validated_criteria
from kodezart.services.agent_service import AgentService
from kodezart.services.fire_time_rulings import FireTimeRulings
from kodezart.services.lane_lapse_escalation import LaneLapseEscalations
from kodezart.services.lane_state_writer import TrackerLaneStateWriter
from kodezart.services.native_amendments import NativeAmendments
from kodezart.services.scope_membership import read_scope_members
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    BRANCH_NAME_SCHEMA,
    AcceptanceCriteriaOutput,
    RateLimitWarningEvent,
    ResultEvent,
    WorkflowCompleteEvent,
    WorkflowIterationEvent,
    WorkflowRemediationEvent,
    WorkflowReviewEvent,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.criteria import (
    ConjunctionVerdict,
    CriteriaArtifact,
    TrackerCriterion,
    TrackerCriterionSet,
)
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.lane_entry import DeliverOnlyLane, NewLane, ResumedLane
from kodezart.types.domain.operation import OperationConfig, ScopeLabel
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.ralph_outcome import PendingRalphOutcome
from kodezart.types.domain.remediation import RemediationPlan
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.ticket_review import TicketReviewMode
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeChangePersister,
    FakeGitService,
    FakeQualityGate,
    FakeRepoCache,
    FakeScopeStatusWriter,
    FakeTicketGenerator,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_criteria,
    make_prompt_provider,
    make_tracker_issue,
    no_delay_floor,
    unsatisfied_base_answer,
)

#: The operation's own key for "the criteria stage finished on this issue".
STAGE_KEY = "criteria-staged"

SUBJECT = "fire/subject"
#: Two direct criteria the fire owes, one direct criterion already Done,
#: a deliverable child, and the criterion that sits under THAT child —
#: the offending shape the 2026-09-09 subtree ruling is about.
DIRECT_OWED = "fire/owed-direct"
DIRECT_DONE = "fire/done-direct"
DELIVERABLE_CHILD = "fire/deliverable-child"
NESTED_OWED = "fire/owed-nested"
DIRECT_OWED_TOO = "fire/owed-direct-2"
NESTED_DONE = "fire/done-nested"

GENERATION_NODES = frozenset(
    {
        "generate_ticket",
        "generate_criteria",
        "validate_criteria",
        "persist_ticket",
        "persist_artifacts",
    }
)


def criterion_body(key: str) -> str:
    """A criterion sub-issue on the board's own criterion template."""
    return (
        f"**Check:** the check {key} states\n"
        f"**Do:** the build {key} names\n"
        f"**Evidence:** the evidence {key} recorded"
    )


def check_of(key: str) -> str:
    return f"the check {key} states"


def board(issues, *, approved: bool = True) -> FakeTrackerPort:
    """The subject's board around *issues*: markers, stage label, admission.

    The board reads its markers under the same operation the engine writes
    them under; a port with no prefixes could answer for no lane.
    """
    return FakeTrackerPort(
        issues=issues,
        criteria_stage_label_key=STAGE_KEY,
        marker_prefixes=native_operation().marker_prefixes,
        scope_label_members={
            ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT): (
                frozenset({ScopeLabel.APPROVED}) if approved else frozenset()
            )
        },
    )


def tracker(
    *,
    staged: bool = True,
    approved: bool = True,
    bodies: dict[str, str] | None = None,
) -> FakeTrackerPort:
    """The subject, its subtree, and the two facts the spec read requires."""
    overrides = bodies or {}
    labels = frozenset({STAGE_KEY}) if staged else frozenset()
    issues = [
        make_tracker_issue(
            SUBJECT,
            issue_labels=labels,
            body=overrides.get(SUBJECT, "the subject's own text"),
        ),
        make_tracker_issue(
            DIRECT_OWED,
            parent_key=SUBJECT,
            issue_labels=frozenset({"criterion"}),
            body=overrides.get(DIRECT_OWED, criterion_body(DIRECT_OWED)),
        ),
        make_tracker_issue(
            DIRECT_DONE,
            parent_key=SUBJECT,
            issue_labels=frozenset({"criterion"}),
            state_name="Done",
            state_kind=WorkflowStateKind.COMPLETED,
            body=overrides.get(DIRECT_DONE, criterion_body(DIRECT_DONE)),
        ),
        make_tracker_issue(DELIVERABLE_CHILD, parent_key=SUBJECT),
        make_tracker_issue(
            NESTED_OWED,
            parent_key=DELIVERABLE_CHILD,
            issue_labels=frozenset({"criterion"}),
            body=overrides.get(NESTED_OWED, criterion_body(NESTED_OWED)),
        ),
        make_tracker_issue(
            DIRECT_OWED_TOO,
            parent_key=SUBJECT,
            issue_labels=frozenset({"criterion"}),
            body=overrides.get(DIRECT_OWED_TOO, criterion_body(DIRECT_OWED_TOO)),
        ),
        make_tracker_issue(
            NESTED_DONE,
            parent_key=DELIVERABLE_CHILD,
            issue_labels=frozenset({"criterion"}),
            state_name="Done",
            state_kind=WorkflowStateKind.COMPLETED,
            body=overrides.get(NESTED_DONE, criterion_body(NESTED_DONE)),
        ),
    ]
    return board(issues, approved=approved)


#: The sentinel that tells ``engine`` to build the question step itself,
#: so a test that withholds it passes ``None`` and is not mistaken for one
#: that said nothing.
DEFAULT = object()


def engine(
    *,
    criteria: TrackerCriteria | None,
    quality_gate: QualityGate | None = None,
    executor: FakeAgentExecutor | None = None,
    real_loop: bool = False,
    remediation_rounds: int = 0,
    max_iterations: int = 1,
    fan_in_max_attempts: int = 1,
    checkpointer=None,
    persister=None,
    git=None,
    source=None,
    forge=None,
    workspace=None,
    lane_operation=None,
    writes_lane_state: bool = True,
    raises_lapse_questions: bool = True,
    owns_workspace: bool = True,
    rulings=DEFAULT,
) -> RalphWorkflowEngine:
    """The fire engine, wired the way composition wires it, plus the stage.

    The Git, source and persister doubles default to today's no-commit ones;
    a test about what a commit leaves behind supplies its own repository, and
    a test about WHICH tree a lane opened supplies the workspace provider so
    it can read the acquisitions back.
    *writes_lane_state*, *raises_lapse_questions* and *owns_workspace* are the
    collaborators a test withholds on purpose: a native loop without one of
    them is the wiring a node refuses at, before it opens a session.
    """
    git = (
        git
        if git is not None
        else FakeGitService(remote_branch_shas={"main": "b" * 40})
    )
    workspace = FakeWorkspaceProvider(git=git) if workspace is None else workspace
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor or FakeAgentExecutor(events=[]),
        workspace=workspace,
        persister=persister if persister is not None else FakeChangePersister(),
    )
    prompts = make_prompt_provider()
    gate = PassThroughGate()
    if real_loop:
        source = source if source is not None else NativeSourceReader()
        quality_gate = RalphLoop(
            source=source,
            amendments=(
                NativeAmendments(
                    tracker=criteria._tracker,
                    operation=native_operation(),
                    criteria=criteria,
                    git=git,
                    source=source,
                    workspace=workspace,
                    runner=service,
                    prompts=prompts,
                    skills=SUPPRESS_ALL_SKILLS,
                    repositories=(),
                    gate=gate,
                    max_verify_rounds=2,
                    lease_seconds=900,
                )
                if criteria is not None
                else None
            ),
            lane_state=(
                TrackerLaneStateWriter(
                    tracker=criteria._tracker,
                    operation=lane_operation or native_operation(),
                    git=git,
                    git_remote="origin",
                    forge=forge,
                    gate=gate,
                )
                if criteria is not None and writes_lane_state
                else None
            ),
            lapse_escalations=(
                LaneLapseEscalations(
                    tracker=criteria._tracker,
                    operation=lane_operation or native_operation(),
                    runner=service,
                    workspace=workspace,
                    git=git,
                    prompts=prompts,
                    skills=SUPPRESS_ALL_SKILLS,
                    gate=gate,
                    lease_seconds=900,
                )
                if criteria is not None and raises_lapse_questions
                else None
            ),
            service=service,
            workspace=workspace if owns_workspace else None,
            max_iterations=max_iterations,
            criteria_reader=criteria,
            plateau_window=2,
            git=git,
            cache=FakeRepoCache(),
            prompts=prompts,
            skills=SUPPRESS_ALL_SKILLS,
            retry_max_attempts=1,
            retry_initial_interval=0,
            delay_floor_for=no_delay_floor,
            fan_in_max_attempts=fan_in_max_attempts,
        )
    return RalphWorkflowEngine(
        specification=FireSpecification(
            service=service,
            ticket_generator=FakeTicketGenerator(),
            prompts=prompts,
            skills=SUPPRESS_ALL_SKILLS,
            gate=gate,
            visibility_resolver=None,
            criteria_max_regeneration_rounds=1,
            fan_in_max_attempts=2,
        ),
        implementation=FireImplementation(
            criteria_reader=criteria,
            quality_gate=quality_gate
            or FakeQualityGate(
                events=[],
                evaluation=AcceptanceCriteriaOutput.model_validate(
                    native_evaluation(reconciled=True)
                ),
                last_commit_sha="a" * 40,
            ),
            prompts=prompts,
            # Wired so the authored arm carries both artifact nodes and the
            # native arm's absence of them is a difference, not an accident.
            artifact_persister=FakeArtifactPersister(),
            gate=gate,
        ),
        consolidation=FireConsolidation(
            merger=FakeBranchMerger(),
            git=git,
            cache=FakeRepoCache(),
            git_remote="origin",
            ref_publisher=None,
        ),
        review=FireReview(
            criteria_reader=criteria,
            service=service,
            prompts=prompts,
            skills=SUPPRESS_ALL_SKILLS,
            git=git,
            cache=FakeRepoCache(),
            fan_in_max_attempts=2,
        ),
        remediation=FireRemediation(
            remediator=(
                RemediationChain(
                    criteria_reader=criteria,
                    service=service,
                    prompts=prompts,
                    skills=SUPPRESS_ALL_SKILLS,
                )
                if remediation_rounds
                else None
            ),
            remediation_max_rounds=remediation_rounds,
        ),
        checkpointer=checkpointer,
        git_base_url="https://github.com",
        retry_max_attempts=1,
        retry_initial_interval=0,
        delay_floor_for=no_delay_floor,
        criteria=criteria,
        rulings=(
            FireTimeRulings(
                tracker=criteria._tracker,
                operation=native_operation(),
                runner=service,
                workspace=workspace,
                git=git,
                prompts=prompts,
                skills=SUPPRESS_ALL_SKILLS,
                gate=gate,
                lease_seconds=900,
            )
            if rulings is DEFAULT and criteria is not None
            else (None if rulings is DEFAULT else rulings)
        ),
    )


async def drive(fire: RalphWorkflowEngine, *, scope: ScopeRef | None):
    events = []
    async for event in fire.run(
        prompt="Implement the requested behavior",
        issue_key=None,
        repo_path="/tmp/fire",
        repo_url="https://github.com/owner/repo",
        base_spec=trunk_base("main"),
        scope=scope,
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=["Bash"],
        cache_key="native-fire",
    ):
        events.append(event)
    return events


def reachable(fire_graph, *, without: str) -> set[str]:
    """Every node reachable from the entry with *without* deleted.

    A node's PLACE is what the criterion is about, and a place is not
    established by the node existing: the question is whether anything
    can arrive at the loop without passing through it first.
    """
    edges = [
        edge
        for edge in fire_graph.get_graph().edges
        if edge.source != without and edge.target != without
    ]
    seen = {"__start__"}
    pending = ["__start__"]
    while pending:
        current = pending.pop()
        for edge in edges:
            if edge.source == current and edge.target not in seen:
                seen.add(edge.target)
                pending.append(edge.target)
    return seen


# ---------------------------------------------------------------------------
# The graph holds no generation node, and the pre-loop step stands between
# the entry and the loop.
# ---------------------------------------------------------------------------


def test_the_native_fire_graph_holds_no_ticket_or_criteria_generation_node() -> None:
    fire = engine(criteria=TrackerCriteria(tracker=tracker()))
    assert fire.native_graph is not None
    native = fire.native_graph.get_graph()
    authored = fire.graph.get_graph()

    assert not GENERATION_NODES & set(native.nodes)
    assert not [
        edge
        for edge in native.edges
        if edge.source in GENERATION_NODES or edge.target in GENERATION_NODES
    ]
    # Not vacuous: every one of those names is a node of the authored arm,
    # so the native arm dropped them rather than never having had them.
    assert GENERATION_NODES <= set(authored.nodes)
    assert "revalidate_criteria" in set(native.nodes)
    assert "revalidate_criteria" not in set(authored.nodes)


def test_the_native_fire_graph_holds_no_branch_generation_node() -> None:
    """The node sets of the two compiled graphs, compared (KOD-839).

    Not vacuous: the authored graph still holds the node, so the native arm
    dropped it rather than never having had one.
    """
    fire = engine(criteria=TrackerCriteria(tracker=tracker()))
    assert fire.native_graph is not None
    native = fire.native_graph.get_graph()
    authored = fire.graph.get_graph()

    assert "generate_branch" in set(authored.nodes)
    assert "generate_branch" not in set(native.nodes)
    assert not [
        edge for edge in native.edges if "generate_branch" in (edge.source, edge.target)
    ]
    # And the node the entry used to reach through it is reached directly.
    assert ("resolve_visibility", "revalidate_criteria") in {
        (edge.source, edge.target) for edge in native.edges
    }


async def test_a_native_fire_opens_no_branch_name_session() -> None:
    """The lane's names come from its issue key, with no session at all.

    ``prepare`` draws them, so the terminal reports a deliverable branch of
    the documented shape while the executor recorded no branch-name schema
    call — the schema is what a branch-name session is asked for.
    """
    executor = NativeExecutor([native_evaluation(), native_evaluation()])
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
    workspace = FakeWorkspaceProvider(git=git)
    fire = engine(
        criteria=TrackerCriteria(tracker=tracker()),
        executor=executor,
        real_loop=True,
        git=git,
        workspace=workspace,
    )

    events = await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))

    terminal = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert re.fullmatch(
        rf"kodezart/{re.escape(SUBJECT)}-[0-9a-f]{{8}}", terminal.feature_branch
    )
    assert terminal.ralph_branch.startswith(f"{terminal.feature_branch}-ralph-")
    # The recorder is live before the absence is read off it: these are the
    # sessions this fire DID open, so "no branch-name call" is a statement
    # about what was asked and not about an empty list.
    assert any("criteriaResults" in properties for properties in executor.schema_calls)
    assert executor.evaluations == []
    assert not any("slug" in properties for properties in executor.schema_calls)
    # The authored arm still asks for one, so the absence is this arm's.
    assert "slug" in BRANCH_NAME_SCHEMA["properties"]
    # And a new lane cuts the branch it just named, from the base that
    # resolved: work_base_ref is the base on this path, never the loop branch,
    # so the first tree of a new lane is the only one that is created.
    first = next(
        call for call in workspace.acquisitions if call.get("branch_name") is not None
    )
    assert first["branch_name"] == terminal.ralph_branch
    assert first["ref"] == "main"
    assert first["create_branch"] is True


async def test_a_key_that_cannot_be_a_ref_refuses_before_any_git_call() -> None:
    """The typed refusal happens at the fire's entry, not at a git error.

    The domain function refuses the key; this drives the whole fire with one,
    so the refusal is shown where it matters: ``prepare`` raises before the
    graph is streamed, and the two collaborators a lane would touch first —
    the repository and the workspace it would be cut in — were never asked.
    """
    unusable = "fire subject"
    executor = NativeExecutor([native_evaluation()])
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
    workspace = FakeWorkspaceProvider(git=git)
    fire = engine(
        criteria=TrackerCriteria(tracker=tracker()),
        executor=executor,
        real_loop=True,
        git=git,
        workspace=workspace,
    )

    with pytest.raises(LaneEntryError, match="cannot stand inside a branch ref"):
        await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=unusable))

    assert git.calls == []
    assert workspace.acquisitions == []
    assert executor.schema_calls == []


def test_an_unwired_deployment_composes_no_native_arm_at_all() -> None:
    fire = engine(criteria=None)
    assert fire.native_graph is None
    assert GENERATION_NODES <= set(fire.graph.get_graph().nodes)


def test_the_loop_is_unreachable_without_the_pre_loop_step() -> None:
    fire = engine(criteria=TrackerCriteria(tracker=tracker()))
    assert fire.native_graph is not None

    assert "run_ralph_loop" in reachable(fire.native_graph, without="")
    assert "run_ralph_loop" not in reachable(
        fire.native_graph, without="revalidate_criteria"
    )
    # The authored arm's own gate is load-bearing the same way, so the
    # check above is a statement about placement, not about one node name.
    assert "run_ralph_loop" not in reachable(fire.graph, without="generate_criteria")


async def test_a_criterion_that_lost_its_check_stops_the_fire_before_the_loop() -> None:
    gate = FakeQualityGate(
        events=[],
        evaluation=AcceptanceCriteriaOutput.model_validate(
            native_evaluation(reconciled=True)
        ),
        last_commit_sha="a" * 40,
    )
    port = tracker(bodies={NESTED_OWED: "**Do:** a body that states no check"})
    fire = engine(criteria=TrackerCriteria(tracker=port), quality_gate=gate)

    with pytest.raises(InvalidFireCriterionError) as caught:
        await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))

    assert caught.value.criterion_key == NESTED_OWED
    assert caught.value.issue_key == SUBJECT
    # The loop is what the step precedes: it never ran.
    assert gate.calls == []


async def test_valid_native_fire_reaches_shared_execution_with_tracker_checks() -> None:
    gate = FakeQualityGate(
        events=[],
        evaluation=AcceptanceCriteriaOutput.model_validate(
            native_evaluation(reconciled=True)
        ),
        last_commit_sha="a" * 40,
    )
    fire = engine(
        criteria=TrackerCriteria(tracker=tracker()),
        quality_gate=gate,
        executor=NativeExecutor([native_evaluation(reconciled=True)]),
    )

    await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))

    assert len(gate.calls) == 1
    assert {c.id: c.text for c in gate.calls[0]["acceptance_criteria"]} == {
        DIRECT_OWED: check_of(DIRECT_OWED),
        NESTED_OWED: check_of(NESTED_OWED),
        DIRECT_OWED_TOO: check_of(DIRECT_OWED_TOO),
    }


async def test_an_unwired_engine_still_refuses_an_addressed_run() -> None:
    fire = engine(criteria=None)
    with pytest.raises(ScopedExecutionUnavailableError, match="scope entry pipeline"):
        await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))


async def test_a_container_address_is_not_a_fire() -> None:
    fire = engine(criteria=TrackerCriteria(tracker=tracker()))
    with pytest.raises(ScopedExecutionUnavailableError, match="addressed to one issue"):
        await drive(fire, scope=ScopeRef(kind=ScopeKind.PROJECT, key="a-project"))


def test_the_addressed_issue_is_the_subject_the_run_carries() -> None:
    fire = engine(criteria=TrackerCriteria(tracker=tracker()))
    state, _ = fire.prepare(
        prompt="Implement the requested behavior",
        issue_key=None,
        repo_path="/tmp/fire",
        repo_url="https://github.com/owner/repo",
        base_spec=trunk_base("main"),
        scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT),
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=["Bash"],
        cache_key="native-fire",
        surface_holder="native-fire",
    )
    assert state["issue_key"] == SUBJECT

    with pytest.raises(ScopedExecutionUnavailableError, match="disagree"):
        fire.prepare(
            prompt="Implement the requested behavior",
            issue_key="fire/other-subject",
            repo_path="/tmp/fire",
            repo_url="https://github.com/owner/repo",
            base_spec=trunk_base("main"),
            scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT),
            permission_mode=PermissionMode.UNATTENDED,
            allowed_tools=["Bash"],
            cache_key="native-fire",
        )


# ---------------------------------------------------------------------------
# The entry is the source; the subtree is the extent it reads over.
# ---------------------------------------------------------------------------


RETIRED_SOURCE_CLAIM = "The spec read IS the source"


def test_the_criteria_docstring_names_the_gate_and_the_shrink_guard() -> None:
    """The class says what the read does, not that it is the source.

    ``read_fire_spec`` admits the subject and refuses a subtree that lost a
    criterion the captured spec names; the roster itself comes from the
    subtree read. A docstring naming the read as the source contradicts the
    code below it and the paragraph two sentences down, and it has drifted
    that way once already (KOD-398).
    """
    docstring = TrackerCriteria.__doc__
    assert docstring is not None
    assert RETIRED_SOURCE_CLAIM not in docstring
    assert "The subtree IS the source" in docstring
    assert "admission" in docstring
    assert "shrinking" in docstring


async def test_the_criteria_a_fire_owes_are_its_subtrees_todo_criteria() -> None:
    stage = TrackerCriteria(tracker=tracker())

    owed = await stage.read_owed_criteria(issue_key=SUBJECT)

    assert owed == {
        DIRECT_OWED: check_of(DIRECT_OWED),
        NESTED_OWED: check_of(NESTED_OWED),
        DIRECT_OWED_TOO: check_of(DIRECT_OWED_TOO),
    }


async def test_only_the_check_reaches_the_fire_never_the_recorded_evidence() -> None:
    stage = TrackerCriteria(tracker=tracker())

    owed = await stage.read_owed_criteria(issue_key=SUBJECT)

    for key, check in owed.items():
        assert check == check_of(key)
        assert "evidence" not in check
        assert "the build" not in check


@pytest.mark.parametrize("absent", ["stage", "approval"])
async def test_a_refused_spec_read_refuses_the_fire_though_the_subtree_reads(
    absent: str,
) -> None:
    port = tracker(staged=absent != "stage", approved=absent != "approval")
    stage = TrackerCriteria(tracker=port)

    with pytest.raises(FireSpecEntryError) as caught:
        await stage.read_owed_criteria(issue_key=SUBJECT)

    assert caught.value.issue_key == SUBJECT
    # The side channel is intact: the very criteria the fire would have
    # owed are readable off the subtree, and are not an answer.
    subtree = await port.scope_issues(ref=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))
    assert {
        issue.issue_key for issue in subtree if "criterion" in issue.issue_labels
    } == {DIRECT_OWED, DIRECT_DONE, NESTED_OWED, DIRECT_OWED_TOO, NESTED_DONE}


async def test_a_criterion_the_spec_names_that_the_subtree_lost_refuses() -> None:
    """A criterion archived after the entry refuses at the next barrier.

    The entry composes its spec from the same reading it selects its roster
    from, so inside it there is no interval for a criterion to disappear in.
    The interval is between the entry and the next barrier, and that is where
    the cross-check earns its keep: the captured spec still names a criterion
    the subtree no longer holds, and the barrier refuses rather than quietly
    grading the fire against a smaller obligation.
    """
    port = tracker()
    source = TrackerCriteria(tracker=port)
    spec, _ = await source.read_entry(issue_key=SUBJECT)
    del port.issues[DIRECT_OWED_TOO]

    with pytest.raises(InvalidFireCriterionError) as caught:
        await source.read_current(spec=spec)

    assert caught.value.criterion_key == DIRECT_OWED_TOO


async def test_a_criterion_the_fire_does_not_owe_is_not_revalidated() -> None:
    """A finished criterion is outside the obligation, so its body is too.

    The entry validates the Check of every criterion the subtree holds,
    whole, as it validated the direct family's whole: a malformed Check
    refuses there wherever under the subject it sits.  What a barrier does
    not re-read is a FINISHED criterion's Check — there the state is what
    decides, so a Done criterion under a deliverable child is not re-read
    for a Check it no longer owes.
    """
    port = tracker()
    stage = TrackerCriteria(tracker=port)
    spec, _ = await stage.read_entry(issue_key=SUBJECT)
    port.issues[NESTED_DONE] = port.issues[NESTED_DONE].model_copy(
        update={"body": "no template rows at all"}
    )

    current = await stage.read_current(spec=spec)

    assert {criterion.id for criterion in current.criteria} == {
        DIRECT_OWED,
        NESTED_OWED,
        DIRECT_OWED_TOO,
    }


async def test_a_finished_nested_criterion_with_no_legible_check_refuses():
    """A finished criterion is still parsed, because the entry reads the roster.

    What the entry captures is the subtree's roster entire and not the part of
    it the fire owes, so every criterion under the subject has its Check read
    there — one already Done under a deliverable child included. A board whose
    finished nested criterion carries no template row therefore refuses at the
    entry, where the same subject with a malformed OWED criterion already
    refused, and the refusal names the criterion rather than the subject alone.

    The case above makes the opposite reading, at a barrier, where state is
    what decides: this one is about the entry, which has no roster to go on yet.
    """
    source = TrackerCriteria(
        tracker=tracker(bodies={NESTED_DONE: "no template rows at all"})
    )

    with pytest.raises(InvalidFireCriterionError) as caught:
        await source.read_entry(issue_key=SUBJECT)

    assert caught.value.issue_key == SUBJECT
    assert caught.value.criterion_key == NESTED_DONE


def nested_only_board(*, nested: bool = True) -> FakeTrackerPort:
    """The subject with no criterion of its own and one deliverable child.

    With *nested*, one criterion sits under that child and nowhere else —
    the shape the subtree extent is about, which this module's main board
    does not isolate because it also carries direct criterion children.
    Without it, the subtree holds no criterion at all.
    """
    issues = [
        make_tracker_issue(
            SUBJECT, issue_labels=frozenset({STAGE_KEY}), body="the subject's own text"
        ),
        make_tracker_issue(DELIVERABLE_CHILD, parent_key=SUBJECT),
    ]
    if nested:
        issues.append(
            make_tracker_issue(
                NESTED_OWED,
                parent_key=DELIVERABLE_CHILD,
                issue_labels=frozenset({"criterion"}),
                body=criterion_body(NESTED_OWED),
            )
        )
    return board(issues)


def unlisted_spec() -> TrackerSpec:
    """A captured subject naming no criterion, for the barrier readings alone.

    Built by hand, the way ``test_a_subtree_holding_no_criterion_has_nothing
    _to_deliver`` builds one: the entry's spec composition over the subtree
    refuses an empty one, so a barrier reading is reached here with the spec
    the entry would have captured had the subtree held nothing to refuse it.
    """
    return TrackerSpec(
        subject=SUBJECT,
        body="the subject's own text",
        criteria=(),
        read_at_version="1",
    )


async def test_a_nested_only_owner_reads_non_empty_and_is_not_refused():
    """One criterion under a deliverable child is the owner's obligation.

    The extent is asserted first — the subtree answers the nested criterion,
    the direct family answers nothing — and then the REAL entry is driven
    over the same board: the fire is admitted, its captured spec names the
    nested criterion, and the loop is entered with it.  A reading that
    measured emptiness over the direct family again would refuse this board
    here, before any session opens.
    """
    port = nested_only_board()

    members = await read_scope_members(
        tracker=port, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT)
    )
    current = await TrackerCriteria(tracker=port).read_current(spec=unlisted_spec())

    assert {
        key for key, issue in members.items() if "criterion" in issue.issue_labels
    } == {NESTED_OWED}
    assert [(criterion.id, criterion.text) for criterion in current.criteria] == [
        (NESTED_OWED, check_of(NESTED_OWED))
    ]
    # The direct family is empty while the subtree is not, which is the
    # whole of the difference the subtree extent turns on.
    assert tuple(await port.read_criteria(issue_key=SUBJECT)) == ()

    nested_check = {NESTED_OWED: check_of(NESTED_OWED)}
    executor = NativeExecutor(
        [
            native_evaluation(checks=nested_check),
            native_evaluation(checks=nested_check),
        ]
    )
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        checkpointer=InMemorySaver(),
    )

    events = await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))

    iteration = next(e for e in events if isinstance(e, WorkflowIterationEvent))
    assert {
        result.criterion_id: result.criterion
        for result in iteration.evaluation.criteria_results
    } == nested_check
    saved = fire.native_graph.get_state(
        {"configurable": {"thread_id": workflow_thread_id("native-fire")}}
    ).values
    assert saved["fire_spec"].criteria == (NESTED_OWED,)
    assert executor.evaluation_prompts != []


@pytest.mark.parametrize("history", ["never minted", "minted then removed"])
async def test_a_zero_criterion_subtree_is_one_successful_empty_reading(history: str):
    """An empty subtree is an answer, and the two histories answer alike.

    Asserted at ``TrackerCriteria._read_subtree_criteria``, the one surface
    in the module that answers an empty set rather than refusing, and at
    ``TrackerPort.read_criteria`` for each member.  Reading it through
    ``read_scope_members`` plus a label filter would re-apply in the test
    the production line under test, so the subtree read itself is where
    this is asserted.  Cited and not repeated: the port-level empty read
    over both registered tracker implementations
    (``tests/tracker/test_criterion_reader.py``,
    ``test_successful_empty_is_distinct_from_a_failed_parent_read``), and
    the typed errors a failing subtree read keeps rather than answering
    empty — a transport failure inside it becomes the entry error with its
    cause kept (``tests/chains/test_native_port_failures.py``,
    ``test_native_entry_preserves_tracker_port_failure``), and a scope read
    that refuses keeps its own type
    (``test_a_refused_scope_read_inside_the_subtree_read_stays_a_typed_error``
    below).
    """
    port = nested_only_board(nested=history == "minted then removed")
    if history == "minted then removed":
        assert tuple(
            criterion.issue_key
            for criterion in await port.read_criteria(issue_key=DELIVERABLE_CHILD)
        ) == (NESTED_OWED,)
        del port.issues[NESTED_OWED]

    source = TrackerCriteria(tracker=port)
    subtree = await source._read_subtree_criteria(unlisted_spec())

    assert subtree == {}
    assert tuple(await port.read_criteria(issue_key=SUBJECT)) == ()
    assert tuple(await port.read_criteria(issue_key=DELIVERABLE_CHILD)) == ()
    # A reading spends no writes: neither history leaves a mark behind.
    assert port.issue_writes == []
    assert port.comment_writes == []
    assert port.issue_creations == []


@pytest.mark.parametrize(
    "reason", ["issue parent cycle", "scope criterion membership changed"]
)
async def test_a_refused_scope_read_inside_the_subtree_read_stays_a_typed_error(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    """A refused subtree read keeps its own type, never an empty reading.

    The case above reads a subtree that holds no criterion and answers
    ``{}``.  Here the read itself refuses: the subject's parent chain
    closes on itself, or a criterion's parent moved between the family read
    and the membership check.  ``ScopeReadError`` is none of the transport
    failures ``read_current`` converts, so it leaves the stage with its own
    type and its own reason rather than emptying the roster and arriving as
    the "no Todo criteria to execute" refusal an honestly empty subtree
    earns.  Nothing is written on the way out.
    """
    port = nested_only_board()
    if reason == "issue parent cycle":
        port.issues[SUBJECT] = port.issues[SUBJECT].model_copy(
            update={"parent_key": DELIVERABLE_CHILD}
        )
    else:
        family = port.read_criteria

        async def moved(*, issue_key: str) -> tuple[TrackerIssue, ...]:
            """Every criterion answers with the subject as its parent."""
            return tuple(
                criterion.model_copy(update={"parent_key": SUBJECT})
                for criterion in await family(issue_key=issue_key)
            )

        monkeypatch.setattr(port, "read_criteria", moved)

    with pytest.raises(ScopeReadError, match=reason):
        await TrackerCriteria(tracker=port).read_current(spec=unlisted_spec())

    assert port.issue_writes == []
    assert port.issue_creations == []


async def test_the_actual_native_entry_refuses_a_zero_criterion_subtree_before_the_loop(
    monkeypatch,
):
    """The real entry refuses an empty subtree before the loop's graph runs.

    The wall the entry meets is ``tracker_spec_from_issues``, reached from
    ``revalidate_criteria`` through ``read_entry``'s spec composition over
    the subtree reading, which does not convert it because
    ``EmptyFireCriteriaError`` is not one of the transport failures that step
    turns into an entry error.  The loop is
    the real one the engine composes, and its compiled graph is shown never
    to be streamed rather than assumed so; that nothing reaches the loop
    except through the pre-loop step is pinned by
    ``test_the_loop_is_unreachable_without_the_pre_loop_step``.
    """
    port = nested_only_board(nested=False)
    executor = FakeAgentExecutor(events=[])
    fire = engine(
        criteria=TrackerCriteria(tracker=port), executor=executor, real_loop=True
    )
    loop = fire.implementation._quality_gate
    assert isinstance(loop, RalphLoop)
    dispatch = Mock(side_effect=AssertionError("the loop must not start"))
    monkeypatch.setattr(loop._compiled, "astream", dispatch)

    with pytest.raises(EmptyFireCriteriaError) as caught:
        await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))

    assert caught.value.issue_key == SUBJECT
    assert executor.calls == []
    dispatch.assert_not_called()


async def test_the_pre_loop_step_refuses_an_empty_owed_reading_by_name():
    """The owed reading is the wall standing behind the admission read.

    The three walls the real entry meets, in the order it meets them: the
    spec composition over the subtree, pinned by the case above; the
    owed reading in ``TrackerCriteria.read_current``, pinned here by
    calling the step with a spec already captured, and whose deliver-only
    twin is pinned by
    ``test_a_subtree_holding_no_criterion_has_nothing_to_deliver``; and the
    loop's own arity floor on ``acceptance_criteria``, pinned by
    ``tests/tracker/test_empty_fire_entry.py``'s
    ``test_successful_empty_membership_cannot_dispatch_the_actual_loop``,
    with the floor on ``TrackerCriterionSet.criteria`` beneath it.
    """
    state = {
        "issue_key": SUBJECT,
        "fire_spec": unlisted_spec(),
        "lane_entry": NewLane(),
        "criterion_set": None,
    }

    source = TrackerCriteria(tracker=nested_only_board(nested=False))

    with pytest.raises(
        FireSpecEntryError, match="no Todo criteria to execute"
    ) as caught:
        await revalidate_criteria(state, {}, source=source)

    assert caught.value.issue_key == SUBJECT


async def test_the_entry_reads_the_subtree_once_for_the_spec_and_the_roster():
    """One walk answers both the captured extent and the entry roster.

    The extent emptiness is measured over and the roster the loop starts on
    were two readings of two different extents, which is how a subject whose
    criteria all sit on its deliverables could be admitted by one and refused
    by the other.  Counted rather than argued: one subject read, one resolve
    of the subject's scope, five criteria named, three of them owed.
    """
    port = CountingTracker()

    spec, roster = await TrackerCriteria(tracker=port).read_entry(issue_key=SUBJECT)

    assert port.spec_reads == 1
    assert port.subtree_reads == 1
    assert set(spec.criteria) == {
        DIRECT_OWED,
        DIRECT_DONE,
        NESTED_OWED,
        DIRECT_OWED_TOO,
        NESTED_DONE,
    }
    assert len(spec.criteria) == 5
    assert {criterion.id for criterion in roster.criteria} == {
        DIRECT_OWED,
        DIRECT_OWED_TOO,
        NESTED_OWED,
    }


async def test_the_delivering_entry_reads_the_subtree_once_as_well():
    """The counted claim holds on the delivering branch, not only the owed one.

    The sibling above counts the reading the loop enters on.  This entry has a
    second branch, and one roster read is the property the whole entry exists
    for, so the branch that answers the delivering roster is counted too: a
    second walk taken only when delivering would otherwise be nobody's
    business.  Same double, the board a delivering lane actually stands on:
    the roster is read after the lane's own evaluation finished what it owed,
    because the delivering branch refuses a subtree that still owes.  Finished
    directly rather than through an entry read, so the count below is this
    call's alone.
    """
    port = CountingTracker()
    for key in OWED_KEYS:
        finished(port, key)

    spec, roster = await TrackerCriteria(tracker=port).read_entry(
        issue_key=SUBJECT, delivering=True
    )

    assert port.spec_reads == 1
    assert port.subtree_reads == 1
    assert set(spec.criteria) == {
        DIRECT_OWED,
        DIRECT_DONE,
        NESTED_OWED,
        DIRECT_OWED_TOO,
        NESTED_DONE,
    }
    # The delivering roster is the whole subtree, not the owed selection the
    # sibling case gets, which is what makes this a different branch rather
    # than the same one with a flag.
    assert {criterion.id for criterion in roster.criteria} == set(spec.criteria)


OWED_KEYS = (DIRECT_OWED, DIRECT_OWED_TOO, NESTED_OWED)


def native_evaluation(*, failed: bool = False, checks=None, reconciled=False):
    """Script raw agent echoes or the quality gate's reconciled source text."""
    selected = checks or {key: check_of(key) for key in OWED_KEYS}
    return {
        "criteriaResults": [
            {
                "criterionId": key,
                "criterion": selected[key] if reconciled else "an evaluator echo",
                "passed": not failed,
                "reasoning": "Observed the selected check.",
            }
            for key in selected
        ]
    }


def native_operation():
    return OperationConfig(
        operation_name="native-fixture",
        workspace="fixture",
        marker_prefixes={
            "ruling": "native-fixture-ruling",
            "amendment": "native-amendment",
            "escalation": "native-escalation",
            "run_state": "native-run-state",
            "run_event": "native-run-event",
        },
        issue_labels={"decision": "decision"},
    )


#: The sha every trunk-shaped ref resolves to, and the names that shape.
TRUNK_SHA = "b" * 40
TRUNK_BRANCHES = ("trunk", "main")
#: The sha the fake workspaces stand at, which ``FakeGitService`` reports as
#: their HEAD: a ref read and a HEAD read of one tree answer one repository,
#: so a verdict graded in it stands at the sha it is stamped with.
WORK_SHA = "a" * 40


class NativeSourceReader:
    """The immutable Git-read double paired with these fake Git workspaces.

    These freshness tests report no departure claims and open no semantic judge.
    Actual source/citation behavior is exercised with real Git in amendment tests.
    """

    async def resolve_commit(self, *, cwd, ref):
        if ref in TRUNK_BRANCHES:
            return TRUNK_SHA
        return ref if len(ref) == 40 else WORK_SHA

    async def read_source(self, *, cwd, commit_sha, path):
        raise AssertionError("A no-claim writer must not read semantic citations")

    async def find_source(self, *, cwd, commit_sha, path):
        raise AssertionError("A no-claim writer must not search semantic citations")


#: The session every scripted agent answer reports itself as having run in.
#:
#: What a cross-off's Evidence row points back to is derived from it, so a
#: reader of that row can be asserted against the session the double named.
NATIVE_SESSION = "native-session"

#: Scripted into ``base_readings`` to make one base-check stream carry the
#: provider's rate-limit rejection beside an otherwise complete answer: the
#: session did answer, and the drain refuses the answer anyway.
RATE_LIMITED_BASE_READING = "rate-limited-base-reading"


class NativeExecutor(FakeAgentExecutor):
    """Only the agent boundary is scripted; all execution consumers are real."""

    def __init__(self, evaluations, *, on_remediation=None):
        super().__init__(events=[])
        self.evaluations = list(evaluations)
        self.on_remediation = on_remediation
        self.schema_calls = []
        self.execution_prompts = []
        self.evaluation_prompts = []
        #: The tree each evaluation session was streamed in, in order: what
        #: an evaluator leaves behind, it leaves in the tree it ran in.
        self.evaluation_workspaces = []
        self.remediation_prompts = []
        #: One scripted base reading per pass through the base-check step, and
        #: the prompt plus the tree of each pass it opened. A pass nothing was
        #: scripted for answers that the base satisfies none of the criteria it
        #: was given, which is the answer that leaves a head pass standing.
        self.base_readings = []
        self.base_prompts = []
        self.base_workspaces = []
        self.on_evaluation = None
        #: Called with the count of execution sessions opened so far, at the
        #: moment each one opens — the first thing an iteration does, before
        #: it can have committed anything. A hook here is a kill BETWEEN two
        #: iterations; the evaluation hook above is a kill inside one.
        self.on_execution = None
        #: One answer set per pass through the question step, in order, and
        #: the prompt plus the whole call of each pass it opened.
        self.question_answers = []
        self.question_prompts = []
        self.question_sessions = []
        #: One judgement per landed record; the default upholds what landed,
        #: and each judgement's whole call is kept beside the answer.
        self.findings = []
        self.judge_sessions = []

    def _opened_execution(self, prompt) -> None:
        """Record one execution session and tell the hook it opened.

        The two execution arms differ only in the answer shape they are
        asked for, so both reach the count and the hook through here rather
        than each keeping its own idea of when a session begins.
        """
        self.execution_prompts.append(prompt)
        if self.on_execution is not None:
            self.on_execution(len(self.execution_prompts))

    async def stream(self, **kwargs):
        output_format = kwargs.get("output_format")
        properties = (output_format or {}).get("schema", {}).get("properties", {})
        self.schema_calls.append(properties)
        if "criteriaResults" in properties:
            assert self.evaluations, "Unexpected extra evaluation"
            self.evaluation_prompts.append(kwargs["prompt"])
            self.evaluation_workspaces.append(kwargs.get("cwd"))
            output = self.evaluations.pop(0)
            if self.on_evaluation is not None:
                # Awaited when the hook is one: what a board does between two
                # evaluations it does through the port, the way a write-back
                # does, and those calls are coroutines.
                answered = self.on_evaluation(len(self.evaluation_prompts))
                if inspect.isawaitable(answered):
                    await answered
        elif "baseCheckResults" in properties:
            self.base_prompts.append(kwargs["prompt"])
            self.base_workspaces.append(kwargs.get("cwd"))
            output = (
                self.base_readings.pop(0)
                if self.base_readings
                else unsatisfied_base_answer(kwargs["prompt"])
            )
            if output is RATE_LIMITED_BASE_READING:
                # The answer the pass would otherwise have given, with the
                # rejection in front of it in the same stream: what the drain
                # sees is a complete structured output the provider refused.
                output = unsatisfied_base_answer(kwargs["prompt"])
                yield RateLimitWarningEvent(status="rejected")
        elif "rulings" in properties:
            self.question_prompts.append(kwargs["prompt"])
            self.question_sessions.append(kwargs)
            output = (
                self.question_answers.pop(0)
                if self.question_answers
                else {"rulings": []}
            )
        elif "citedRefs" in properties:
            self.judge_sessions.append(kwargs)
            output = (
                self.findings.pop(0)
                if self.findings
                else {
                    "verdict": "holds",
                    "evidence": "Read the landed record at the base commit.",
                    "cited_refs": ["policy.py"],
                }
            )
        elif "instructions" in properties:
            self.remediation_prompts.append(kwargs["prompt"])
            if self.on_remediation is not None:
                self.on_remediation()
            output = {"instructions": "Repair only the observed failing behavior."}
        elif "claims" in properties:
            self._opened_execution(kwargs["prompt"])
            output = {"claims": []}
        elif output_format is None:
            self._opened_execution(kwargs["prompt"])
            output = None
        else:
            assert "requiredChanges" not in properties
            assert "criteria" not in properties
            assert "findings" not in properties
            async for event in super().stream(**kwargs):
                yield event
            return
        yield ResultEvent(
            subtype="result",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id=NATIVE_SESSION,
            structured_output=output,
        )


class CountingTracker(FakeTrackerPort):
    def __init__(self):
        source = tracker()
        super().__init__(
            issues=list(source.issues.values()),
            criteria_stage_label_key=STAGE_KEY,
            scope_label_members=source.scope_label_members,
        )
        self.spec_reads = 0
        #: Every resolve of a scope this board answered: the walk the subtree
        #: reading is made of, counted so one reading can be told from two.
        self.subtree_reads = 0
        self.unavailable = False

    async def read_fire_subject(self, *, issue_key):
        self.spec_reads += 1
        return await super().read_fire_subject(issue_key=issue_key)

    async def scope_issues(self, *, ref):
        if self.unavailable:
            raise ConnectionError("tracker unavailable")
        self.subtree_reads += 1
        return await super().scope_issues(ref=ref)


async def test_native_graph_executes_and_reviews_exact_checks():
    port = CountingTracker()
    executor = NativeExecutor([native_evaluation(), native_evaluation()])
    saver = InMemorySaver()
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        checkpointer=saver,
    )

    events = await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))

    iteration = next(e for e in events if isinstance(e, WorkflowIterationEvent))
    review = next(e for e in events if isinstance(e, WorkflowReviewEvent))
    terminal = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert iteration.verdict is AcceptVerdict.accepted
    assert review.passed
    assert terminal.outcome is WorkflowOutcome.handed_off_for_delivery
    assert port.spec_reads == 1
    assert not executor.evaluations
    for evaluated in (iteration.evaluation, review.evaluation):
        assert {c.criterion_id: c.criterion for c in evaluated.criteria_results} == {
            key: check_of(key) for key in OWED_KEYS
        }
    saved = fire.native_graph.get_state(
        {"configurable": {"thread_id": workflow_thread_id("native-fire")}}
    ).values
    # The actual checkpoint retains the native partition and its provenance.
    assert isinstance(saved["fire_spec"], TrackerSpec)
    assert isinstance(saved["criterion_set"], TrackerCriterionSet)
    assert all(isinstance(c, TrackerCriterion) for c in validated_criteria(saved))
    assert saved["fire_spec"].subject == SUBJECT
    # The captured extent is the subtree entire, the finished criteria and
    # the nested one included, not the Todo roster the loop was handed.
    assert saved["fire_spec"].criteria == tuple(sorted(ALL_CRITERIA))
    assert saved["fire_spec"].read_at_version
    assert saved["criteria_validation"] is None
    assert fire.implementation._artifact_persister.persist_calls == []
    assert "the subject's own text" in executor.execution_prompts[0]


async def test_native_remediation_refreshes_checks_without_recapturing_subject():
    port = CountingTracker()
    changed_check = "changed Check with  spaces and `code`"

    def amend():
        issue = port.issues[DIRECT_OWED]
        port.issues[DIRECT_OWED] = issue.model_copy(
            update={
                "body": f"**Check:** {changed_check}\n**Do:** repair\n**Evidence:** —"
            }
        )
        port.issues[SUBJECT] = port.issues[SUBJECT].model_copy(
            update={"body": "a later subject text"}
        )

    executor = NativeExecutor(
        [native_evaluation(failed=True), native_evaluation(), native_evaluation()],
        on_remediation=amend,
    )
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        remediation_rounds=1,
    )

    events = await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))

    iterations = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert len(iterations) == 2
    assert iterations[0].verdict is AcceptVerdict.rejected
    assert iterations[1].verdict is AcceptVerdict.accepted
    assert iterations[0].trajectory.never_passed_ids == list(OWED_KEYS)
    assert iterations[1].evaluation.criteria_results[0].criterion == changed_check
    remediation = next(e for e in events if isinstance(e, WorkflowRemediationEvent))
    assert isinstance(remediation.ticket, RemediationPlan)
    assert "Repair only" in executor.execution_prompts[1]
    assert "the subject's own text" in executor.execution_prompts[1]
    assert "a later subject text" not in executor.execution_prompts[1]
    assert port.spec_reads == 1
    assert not executor.evaluations


@pytest.mark.parametrize("failure", ["missing-check", "outage"])
async def test_native_remediation_cannot_bypass_tracker_revalidation(failure):
    port = CountingTracker()

    def break_tracker():
        if failure == "outage":
            port.unavailable = True
        else:
            port.issues[NESTED_OWED] = port.issues[NESTED_OWED].model_copy(
                update={"body": "**Do:** no Check"}
            )

    executor = NativeExecutor(
        [native_evaluation(failed=True)], on_remediation=break_tracker
    )
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        remediation_rounds=1,
    )

    error = FireSpecEntryError if failure == "outage" else InvalidFireCriterionError
    with pytest.raises(error):
        await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))

    assert len(executor.execution_prompts) == 1
    assert port.spec_reads == 1


@pytest.mark.parametrize("identity", ["", " ", "\t\n"])
async def test_execution_result_rejects_blank_tracker_identity(identity):
    with pytest.raises(ValidationError):
        AcceptanceCriteriaOutput.model_validate(
            {
                "criteriaResults": [
                    {
                        "criterionId": identity,
                        "criterion": "a Check",
                        "passed": True,
                        "reasoning": "evidence",
                    }
                ]
            }
        )


def change_tracker(port, change):
    if change == "outage":
        port.unavailable = True
        return
    issue = port.issues[NESTED_OWED]
    update = {"body": "**Check:** changed live Check with  spaces\n**Do:** repair"}
    if change == "missing-check":
        update = {"body": "**Do:** the Check disappeared"}
    elif change == "state":
        # A state that is neither Todo nor Done: the board took this
        # criterion out of the fire's obligation without it being finished.
        # Done is no longer such a state for a criterion the fire holds in
        # its roster, and it is the fire's own evaluation step that moves
        # one there.
        update = {
            "state_kind": WorkflowStateKind.STARTED,
            "state_name": "In Progress",
        }
    port.issues[NESTED_OWED] = issue.model_copy(update=update)
    port.issues[SUBJECT] = port.issues[SUBJECT].model_copy(
        update={"body": "later subject text must not replace frozen subject"}
    )


@pytest.mark.parametrize(
    "change", ["changed-check", "state", "missing-check", "outage"]
)
async def test_native_inner_iterations_read_current_checks(change):
    port = CountingTracker()
    remaining = {key: check_of(key) for key in OWED_KEYS if key != NESTED_OWED}
    executor = NativeExecutor(
        [
            native_evaluation(failed=True),
            native_evaluation(checks=remaining if change == "state" else None),
            native_evaluation(checks=remaining if change == "state" else None),
        ]
    )
    executor.on_evaluation = lambda count: (
        change_tracker(port, change) if count == 1 else None
    )
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        max_iterations=2,
    )
    if change in {"missing-check", "outage"}:
        error = FireSpecEntryError if change == "outage" else InvalidFireCriterionError
        with pytest.raises(error):
            await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))
        assert len(executor.execution_prompts) == 1
        return
    events = await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))
    iterations = [
        event for event in events if isinstance(event, WorkflowIterationEvent)
    ]
    assert len(iterations) == 2
    assert iterations[-1].iteration == 2
    # The tracker changed during dispatch; this result still answers the
    # snapshot that dispatch saw, while the next iteration gets fresh Checks.
    assert iterations[0].evaluation.criteria_results[-1].criterion == check_of(
        NESTED_OWED
    )
    assert iterations[-1].verdict is AcceptVerdict.accepted
    answers = {
        result.criterion_id: result.criterion
        for result in iterations[-1].evaluation.criteria_results
    }
    if change == "state":
        assert NESTED_OWED not in answers
        assert check_of(NESTED_OWED) not in executor.evaluation_prompts[1]
    else:
        assert answers[NESTED_OWED] == "changed live Check with  spaces"
        assert "changed live Check with  spaces" in executor.evaluation_prompts[1]
        assert "changed live Check with  spaces" in executor.execution_prompts[1]
    assert port.spec_reads == 1
    assert all(
        "the subject's own text" in prompt for prompt in executor.execution_prompts
    )
    assert all(
        "later subject text" not in prompt for prompt in executor.execution_prompts
    )


@pytest.mark.parametrize("barrier", ["run_ralph_loop", "review_against_ticket"])
@pytest.mark.parametrize(
    "change", ["changed-check", "state", "missing-check", "outage"]
)
async def test_native_fresh_engine_resume_reads_current_checks(barrier, change):
    port = CountingTracker()
    saver = InMemorySaver()
    executor = NativeExecutor([native_evaluation()])
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        checkpointer=saver,
    )
    initial, config = fire.prepare(
        prompt="Implement the requested behavior",
        issue_key=None,
        repo_path="/tmp/fire",
        repo_url="https://github.com/owner/repo",
        base_spec=trunk_base("main"),
        scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT),
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=["Bash"],
        cache_key="native-fire",
        surface_holder="native-fire",
    )
    async for _ in fire.native_graph.astream(
        initial, config=config, interrupt_before=[barrier]
    ):
        pass
    paused = fire.native_graph.get_state(config)
    assert paused.next == (barrier,)
    frozen = paused.values["fire_spec"]
    change_tracker(port, change)
    remaining = {key: check_of(key) for key in OWED_KEYS if key != NESTED_OWED}
    resumed_executor = NativeExecutor(
        [
            native_evaluation(checks=remaining if change == "state" else None),
            native_evaluation(checks=remaining if change == "state" else None),
        ]
    )
    resumed = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=resumed_executor,
        real_loop=True,
        checkpointer=saver,
    )
    if change in {"missing-check", "outage"}:
        error = FireSpecEntryError if change == "outage" else InvalidFireCriterionError
        with pytest.raises(error):
            async for _ in resumed.native_graph.astream(
                None, config=config, stream_mode="custom"
            ):
                pass
        assert resumed_executor.execution_prompts == []
        assert resumed_executor.evaluation_prompts == []
        return
    events = [
        event
        async for event in resumed.native_graph.astream(
            None, config=config, stream_mode="custom"
        )
    ]
    review = next(event for event in events if isinstance(event, WorkflowReviewEvent))
    assert review.passed
    answers = {
        result.criterion_id: result.criterion
        for result in review.evaluation.criteria_results
    }
    if change == "state":
        assert NESTED_OWED not in answers
    else:
        assert answers[NESTED_OWED] == "changed live Check with  spaces"
    saved = resumed.native_graph.get_state(config).values
    assert saved["fire_spec"] == frozen
    assert isinstance(saved["criterion_set"], TrackerCriterionSet)
    assert port.spec_reads == 1
    assert resumed.implementation._artifact_persister.persist_calls == []


@pytest.mark.parametrize("barrier", ["execute", "evaluate"])
@pytest.mark.parametrize(
    "change", ["changed-check", "state", "missing-check", "outage"]
)
async def test_native_inner_checkpoint_resume_requires_current_checks(barrier, change):
    from kodezart.types.domain.workflow import RalphLoopContext

    port = CountingTracker()
    source = TrackerCriteria(tracker=port)
    spec, snapshot = await source.read_entry(issue_key=SUBJECT)
    saver = InMemorySaver()
    original = engine(criteria=source, executor=NativeExecutor([]), real_loop=True)
    loop = original.implementation._quality_gate
    graph = loop._build_graph().compile(checkpointer=saver)
    context = RalphLoopContext(
        prompt=spec.body,
        repo_path="/tmp/fire",
        repo_url=None,
        cache_key="inner-resume",
        surface_holder="inner-resume",
        base_spec=trunk_base("main"),
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=["Bash"],
        feature_branch="feature",
        ralph_branch="ralph",
        work_base_ref="main",
        acceptance_criteria=snapshot.criteria,
        tracker_spec=spec,
        repo_visibility=RepoVisibility.UNKNOWN,
    )
    config = {"configurable": {**context.model_dump(), "thread_id": "inner-resume"}}
    initial = {
        "iteration": 0,
        "verdict": AcceptVerdict.rejected,
        "pending_failures": [],
        "iteration_records": [],
        # _execute_node reads state["outcome"] for the roster it holds, so a
        # hand-built state carries the loop's own initial value for it.
        "outcome": PendingRalphOutcome(),
    }
    async for _ in graph.astream(initial, config=config, interrupt_before=[barrier]):
        pass
    assert graph.get_state(config).next == (barrier,)
    change_tracker(port, change)
    remaining = {key: check_of(key) for key in OWED_KEYS if key != NESTED_OWED}
    executor = NativeExecutor(
        [native_evaluation(checks=remaining if change == "state" else None)]
    )
    fresh = engine(
        criteria=TrackerCriteria(tracker=port), executor=executor, real_loop=True
    )
    replay = fresh.implementation._quality_gate._build_graph().compile(
        checkpointer=saver
    )
    if change in {"missing-check", "outage"}:
        error = FireSpecEntryError if change == "outage" else InvalidFireCriterionError
        with pytest.raises(error):
            async for _ in replay.astream(None, config=config, stream_mode="custom"):
                pass
        assert executor.execution_prompts == []
        assert executor.evaluation_prompts == []
        return
    events = [
        event
        async for event in replay.astream(None, config=config, stream_mode="custom")
    ]
    iteration = next(
        event for event in events if isinstance(event, WorkflowIterationEvent)
    )
    assert iteration.verdict is AcceptVerdict.accepted
    answers = {
        result.criterion_id: result.criterion
        for result in iteration.evaluation.criteria_results
    }
    if change == "state":
        assert NESTED_OWED not in answers
    else:
        assert answers[NESTED_OWED] == "changed live Check with  spaces"
    assert port.spec_reads == 1


async def test_production_constructor_wires_native_source_to_shared_consumers(
    monkeypatch,
):
    from kodezart.composition.engine import build_workflow_engine
    from kodezart.config.app import AppConfig
    from tests.fakes import FakeRefPublisher

    monkeypatch.setattr(
        "kodezart.composition.engine.SubprocessGitSourceReader",
        NativeSourceReader,
    )

    port = CountingTracker()
    source = TrackerCriteria(tracker=port)
    executor = NativeExecutor([native_evaluation(), native_evaluation()])
    workspace = FakeWorkspaceProvider()
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor,
        workspace=workspace,
        persister=FakeChangePersister(),
    )
    artifacts = FakeArtifactPersister()
    router = build_workflow_engine(
        operation=native_operation(),
        scope_tracker=port,
        scope_registry=InMemoryJobRegistry(),
        scope_status=FakeScopeStatusWriter(),
        config=AppConfig(
            write_back=WriteBackSettings(max_verify_rounds=2),
            ticket_review_mode=TicketReviewMode.REVIEWED,
            max_iterations=1,
            retry_max_attempts=1,
            retry_initial_interval=0.1,
        ),
        repositories=(),
        agent_service=service,
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        workspace=workspace,
        merger=FakeBranchMerger(),
        artifact_persister=artifacts,
        ref_publisher=FakeRefPublisher(),
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        github_api=None,
        checkpointer=InMemorySaver(),
        criteria=source,
    )
    # Direct fire proves constructor capability. The scope wrapper has its own
    # production-route tests; only the immutable Git adapter is doubled here.
    fire = router.arm_for(None).fire
    events = await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))
    terminal = next(
        event for event in events if isinstance(event, WorkflowCompleteEvent)
    )
    assert terminal.outcome is WorkflowOutcome.handed_off_for_delivery
    assert port.spec_reads == 1
    assert artifacts.persist_calls == []


@pytest.mark.parametrize("consumer", ["implementation", "review", "loop"])
async def test_native_consumer_without_runtime_reader_refuses(consumer):
    port = CountingTracker()
    executor = NativeExecutor([native_evaluation(), native_evaluation()])
    fire = engine(
        criteria=TrackerCriteria(tracker=port), executor=executor, real_loop=True
    )
    target = {
        "implementation": fire.implementation,
        "review": fire.review,
        "loop": fire.implementation._quality_gate,
    }[consumer]
    target._criteria_reader = None
    with pytest.raises(FireSpecEntryError, match="reader is not configured"):
        await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))
    assert len(executor.execution_prompts) == (1 if consumer == "review" else 0)


async def test_native_loop_without_frozen_spec_cannot_use_cached_criteria():
    source = TrackerCriteria(tracker=CountingTracker())
    spec, snapshot = await source.read_entry(issue_key=SUBJECT)
    executor = NativeExecutor([])
    fire = engine(criteria=source, executor=executor, real_loop=True)
    with pytest.raises(ValidationError, match="frozen subject source"):
        async for _ in fire.implementation._quality_gate.run(
            prompt=spec.body,
            repo_path="/tmp/fire",
            repo_url=None,
            cache_key="missing-spec",
            base_spec=trunk_base("main"),
            permission_mode=PermissionMode.UNATTENDED,
            allowed_tools=["Bash"],
            feature_branch="feature",
            ralph_branch="ralph",
            work_base_ref="main",
            acceptance_criteria=snapshot.criteria,
            repo_visibility=RepoVisibility.UNKNOWN,
        ):
            pass
    assert executor.execution_prompts == []
    assert executor.evaluation_prompts == []


async def test_native_remediation_receives_the_final_inner_evaluation_snapshot():
    port = CountingTracker()
    executor = NativeExecutor(
        [
            native_evaluation(failed=True),
            native_evaluation(failed=True),
            native_evaluation(),
            native_evaluation(),
        ]
    )
    executor.on_evaluation = lambda count: (
        change_tracker(port, "changed-check") if count == 1 else None
    )
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        max_iterations=2,
        remediation_rounds=1,
    )
    await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))
    assert len(executor.remediation_prompts) == 1
    assert "changed live Check with  spaces" in executor.remediation_prompts[0]
    assert check_of(NESTED_OWED) not in executor.remediation_prompts[0]


# ---------------------------------------------------------------------------
# The fire's current set keeps the roster criteria the fire itself finished.
# ---------------------------------------------------------------------------


def moved(port, key, *, kind, name):
    """Put one criterion sub-issue in another workflow state on the board."""
    port.issues[key] = port.issues[key].model_copy(
        update={"state_kind": kind, "state_name": name}
    )


def finished(port, key):
    """The board after the fire's own evaluation step finished *key*."""
    moved(port, key, kind=WorkflowStateKind.COMPLETED, name="Done")


def snapshot_state(spec, recorded):
    """The graph state a delivery barrier reads its judged roster from."""
    return {"fire_spec": spec, "criterion_set": recorded}


async def entry_roster(port):
    """The subject's spec and the roster a fire entering it takes on."""
    source = TrackerCriteria(tracker=port)
    spec, roster = await source.read_entry(issue_key=SUBJECT)
    return source, spec, roster


async def test_a_held_criterion_the_fire_finished_stays_in_its_current_set():
    """Finishing owed work does not shrink what the fire is judged against.

    The criterion left Todo because this fire's own evaluation moved it, so
    the set it is compared against still holds it, with the Check text it
    was graded on. Read without the roster the same board answers the
    smaller set, so the roster is what carries it and not the state.
    """
    port = tracker()
    source, spec, roster = await entry_roster(port)
    finished(port, DIRECT_OWED)

    current = await source.read_current(spec=spec, held=roster)

    assert current == roster
    assert {criterion.id: criterion.text for criterion in current.criteria} == {
        key: check_of(key) for key in OWED_KEYS
    }
    entry_shaped = await source.read_current(spec=spec)
    assert {criterion.id for criterion in entry_shaped.criteria} == {
        key for key in OWED_KEYS if key != DIRECT_OWED
    }


async def test_a_criterion_finished_before_entry_stays_outside_a_held_set():
    """A roster is the only way into the set, and it is fixed at entry.

    Two criteria of this subtree were Done before the fire arrived. They are
    in no roster, so no later barrier re-owes them, and the fire that
    finishes every criterion it did take on is still judged against exactly
    those.
    """
    port = tracker()
    source, spec, roster = await entry_roster(port)
    assert {criterion.id for criterion in roster.criteria} == set(OWED_KEYS)
    for key in OWED_KEYS:
        finished(port, key)

    current = await source.read_current(spec=spec, held=roster)

    assert current == roster
    assert DIRECT_DONE not in {criterion.id for criterion in current.criteria}
    assert NESTED_DONE not in {criterion.id for criterion in current.criteria}


async def test_a_new_todo_criterion_still_changes_the_set():
    """The roster admits finished work, never a new obligation."""
    port = tracker()
    source, spec, roster = await entry_roster(port)
    finished(port, DIRECT_OWED)
    added = "fire/owed-added"
    port.issues[added] = make_tracker_issue(
        added,
        parent_key=SUBJECT,
        issue_labels=frozenset({"criterion"}),
        body=criterion_body(added),
    )

    current = await source.read_current(spec=spec, held=roster)

    assert {criterion.id for criterion in current.criteria} == {*OWED_KEYS, added}
    with pytest.raises(FireSpecEntryError, match="evaluated snapshot"):
        await require_current_native_snapshot(
            snapshot_state(spec, roster), reader=source
        )


@pytest.mark.parametrize(
    "kind,name",
    [
        (WorkflowStateKind.STARTED, "In Progress"),
        (WorkflowStateKind.CANCELED, "Canceled"),
        (WorkflowStateKind.BACKLOG, "Backlog"),
    ],
)
async def test_a_held_criterion_that_left_todo_and_done_still_changes_the_set(
    kind, name
):
    """Only Todo and the fire's own Done keep a roster criterion inside.

    A criterion the board started, cancelled or sent back to the backlog is
    an obligation nobody holds this fire to any more, so the set shrinks and
    the barrier refuses rather than delivering against a judged roster that
    no longer stands.
    """
    port = tracker()
    source, spec, roster = await entry_roster(port)
    moved(port, NESTED_OWED, kind=kind, name=name)

    current = await source.read_current(spec=spec, held=roster)

    assert {criterion.id for criterion in current.criteria} == {
        key for key in OWED_KEYS if key != NESTED_OWED
    }
    with pytest.raises(FireSpecEntryError, match="evaluated snapshot"):
        await require_current_native_snapshot(
            snapshot_state(spec, roster), reader=source
        )


async def test_delivery_proceeds_when_every_held_criterion_is_done():
    """A lane that finished all its work passes its own delivery barrier.

    This is the barrier the cross-off would otherwise close against itself:
    every criterion the lane owed is Done, so the entry-shaped read finds no
    Todo criterion at all and refuses outright, while the barrier reading
    the lane's roster finds exactly what it was judged against.
    """
    port = tracker()
    source, spec, roster = await entry_roster(port)
    for key in OWED_KEYS:
        finished(port, key)

    await require_current_native_snapshot(snapshot_state(spec, roster), reader=source)

    with pytest.raises(FireSpecEntryError, match="no Todo criteria"):
        await source.read_current(spec=spec)


async def test_a_native_remediation_round_keeps_its_roster():
    """A remediation round entered after the work finished still revalidates.

    The loop accepts and crosses every criterion off, the post-merge review
    rejects, and the round opens on a subtree with nothing left in Todo. Both
    boards the round reads — the remediation draft's own read and the
    pre-loop step it re-enters at — carry the roster the run holds, so they
    revalidate against it instead of refusing for having no Todo criterion
    left, and the drafted round is told the Checks it is still owed.
    """
    port = CountingTracker()
    executor = NativeExecutor(
        [
            # The loop's own iteration, the review that rejects what it
            # produced, then the round's iteration and the review after it.
            native_evaluation(),
            native_evaluation(failed=True),
            native_evaluation(),
            native_evaluation(),
        ]
    )
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        remediation_rounds=1,
    )

    events = await drive(fire, scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT))

    iterations = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert len(iterations) == 2
    assert [event.verdict for event in iterations] == [
        AcceptVerdict.accepted,
        AcceptVerdict.accepted,
    ]
    reviews = [event for event in events if isinstance(event, WorkflowReviewEvent)]
    assert [review.passed for review in reviews] == [False, True]
    assert len(executor.remediation_prompts) == 1
    # The round's own prompt carries every Check the roster still holds it to,
    # read off a board on which every one of them is already finished.
    for key in OWED_KEYS:
        assert check_of(key) in executor.remediation_prompts[0]
    assert {
        result.criterion_id for result in iterations[1].evaluation.criteria_results
    } == set(OWED_KEYS)
    assert all(
        port.issues[key].state_kind is WorkflowStateKind.COMPLETED for key in OWED_KEYS
    )


# ---------------------------------------------------------------------------
# A lane with nothing left to execute enters to deliver: the roster it stands
# on is its finished subtree, and the step before the loop routes it past it.
# ---------------------------------------------------------------------------

RECORDED_DELIVERABLE = "kodezart/fire/subject-1234abcd"
RECORDED_LOOP = f"{RECORDED_DELIVERABLE}-ralph-0"
RECORDED_HEAD = "c" * 40

ALL_CRITERIA = (
    DIRECT_OWED,
    DIRECT_DONE,
    NESTED_OWED,
    DIRECT_OWED_TOO,
    NESTED_DONE,
)


def entry_of(kind: str):
    """One of the three ways a lane enters, named by kind."""
    if kind == "new":
        return NewLane()
    shape = ResumedLane if kind == "resumed" else DeliverOnlyLane
    return shape(
        deliverable_branch=RECORDED_DELIVERABLE,
        loop_branch=RECORDED_LOOP,
        head_sha=RECORDED_HEAD,
        body_digest=None,
    )


def prepared(fire: RalphWorkflowEngine, *, entry):
    """The state the walker's entry produces, without streaming the graph."""
    state, _ = fire.prepare(
        prompt="Implement the requested behavior",
        issue_key=None,
        repo_path="/tmp/fire",
        repo_url="https://github.com/owner/repo",
        base_spec=trunk_base("main"),
        scope=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT),
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=["Bash"],
        cache_key="native-fire",
        surface_holder="native-fire",
        entry=entry,
    )
    return state


async def finished_subtree():
    """The board of a lane whose every criterion is Done, and its spec.

    The spec is captured while the lane still owes, which is the reading a
    fire enters on, and the cross-offs its own work would record follow. A
    delivering read taken afterwards is then addressed by the same subject.
    """
    port = tracker()
    source = TrackerCriteria(tracker=port)
    spec, _ = await source.read_entry(issue_key=SUBJECT)
    for key in OWED_KEYS:
        finished(port, key)
    return port, source, spec


async def test_the_finished_roster_is_the_whole_subtree_not_a_todo_selection():
    """What a delivering lane stands on is every criterion under it.

    The owed reading answers nothing at all on this board — there is no Todo
    criterion left for it to return — while this one answers all five, the
    two that were Done before the lane arrived included: the fact the
    delivery rests on is about the subtree, not about which criteria this
    lane happened to take on.

    Read at ``read_entry(delivering=True)``: that is the surface the source
    now carries this roster on, and the one the delivering lane's own
    revalidation invokes. The case moved off the spec-taking reading it used
    to make, which no caller of the source makes.
    """
    _, source, spec = await finished_subtree()

    _, roster = await source.read_entry(issue_key=SUBJECT, delivering=True)

    assert [criterion.id for criterion in roster.criteria] == sorted(ALL_CRITERIA)
    assert {criterion.id: criterion.text for criterion in roster.criteria} == {
        key: check_of(key) for key in ALL_CRITERIA
    }
    with pytest.raises(FireSpecEntryError, match="no Todo criteria"):
        await source.read_current(spec=spec)


async def test_the_finished_roster_carries_the_barrier_that_follows_it():
    """The roster read here is the one every later barrier re-reads.

    The barriers between this step and the pull request compare the state's
    roster with the owed reading taken against it, so a roster shaped
    differently here would refuse the lane at the first of them. Read the
    barrier with this roster and it passes; the comparison is the whole
    reason both readings are shaped in one place.
    """
    _, source, spec = await finished_subtree()

    _, roster = await source.read_entry(issue_key=SUBJECT, delivering=True)

    await require_current_native_snapshot(snapshot_state(spec, roster), reader=source)
    assert await source.read_current(spec=spec, held=roster) == roster


@pytest.mark.parametrize(
    "kind,name",
    [
        (WorkflowStateKind.UNSTARTED, "Todo"),
        (WorkflowStateKind.STARTED, "In Progress"),
        (WorkflowStateKind.BACKLOG, "Backlog"),
        (WorkflowStateKind.TRIAGE, "Triage"),
    ],
)
async def test_one_criterion_that_is_not_done_refuses_and_is_named(kind, name):
    """A criterion reopened before the entry refuses, and says which one.

    Every OPEN kind is an open obligation, not the two a fire usually moves
    a criterion between, so the lane is not deliverable and the refusal
    names the criterion rather than leaving a reader to diff two rosters.
    """
    port, source, _ = await finished_subtree()
    moved(port, NESTED_OWED, kind=kind, name=name)

    with pytest.raises(FireSpecEntryError) as caught:
        await source.read_entry(issue_key=SUBJECT, delivering=True)

    assert caught.value.issue_key == SUBJECT
    assert NESTED_OWED in caught.value.reason
    assert "not finished" in caught.value.reason
    # Only the offender is named: the four that are Done are not.
    for key in ALL_CRITERIA:
        if key != NESTED_OWED:
            assert key not in caught.value.reason


@pytest.mark.parametrize(
    "kind,name",
    [
        (WorkflowStateKind.CANCELED, "Canceled"),
        (WorkflowStateKind.DUPLICATE, "Duplicate"),
    ],
)
async def test_a_canceled_criterion_is_non_counting_and_the_lane_still_delivers(
    kind, name
):
    """A criterion nobody owes any more neither counts nor refuses (KOD-794).

    The arithmetic that decides a lane owes nothing reads state alone, and a
    criterion the board Canceled — or closed as a Duplicate of another — is
    not an obligation it holds anybody to. The roster this reading answers
    is therefore the finished criteria only, with the abandoned one left
    out, and the lane delivers on it. A reading that refused instead would
    hold such a lane refused on every invocation, for a criterion no
    readiness read counts either.
    """
    port, source, spec = await finished_subtree()
    moved(port, NESTED_OWED, kind=kind, name=name)

    _, roster = await source.read_entry(issue_key=SUBJECT, delivering=True)

    counting = sorted(key for key in ALL_CRITERIA if key != NESTED_OWED)
    assert [criterion.id for criterion in roster.criteria] == counting
    assert {criterion.id: criterion.text for criterion in roster.criteria} == {
        key: check_of(key) for key in counting
    }
    # The barrier that follows the entry compares equal to this roster, so
    # the lane reaches its delivery rather than refusing at the first one.
    await require_current_native_snapshot(snapshot_state(spec, roster), reader=source)
    assert await source.read_current(spec=spec, held=roster) == roster


async def test_a_subtree_whose_criteria_were_all_abandoned_has_nothing_to_deliver():
    """A roster that counts nothing is refused, not delivered as empty.

    Non-counting criteria refuse nothing one at a time, which would leave a
    subtree of only abandoned criteria reading as a vacuously finished lane.
    It is refused where a subtree holding no criterion at all is, and for the
    same reason: there is no obligation for the delivery to discharge.
    """
    port, source, _ = await finished_subtree()
    for key in ALL_CRITERIA:
        moved(port, key, kind=WorkflowStateKind.CANCELED, name="Canceled")

    with pytest.raises(FireSpecEntryError, match="no criteria to deliver") as caught:
        await source.read_entry(issue_key=SUBJECT, delivering=True)

    assert caught.value.issue_key == SUBJECT


async def test_a_subtree_holding_no_criterion_has_nothing_to_deliver():
    """An empty roster is a refusal, never a vacuously finished lane.

    Every criterion of no criteria is Done, so a reading that only checked
    the state would hand a delivery an empty obligation to discharge. The
    readiness read refuses such a member for the same reason; this is the
    same refusal made where the fire enters.

    Asserted at ``TrackerCriteria._finished``, the one place the counting
    roster is arithmetic, because the case moved off a spec-taking reading
    no caller of the source makes. It cannot move to ``read_entry`` instead:
    the entry refuses an empty subtree earlier, at the capture, with
    ``EmptyFireCriteriaError`` — so the entry never reaches this refusal and
    reading through it would assert the capture's clause rather than this
    one. The companion case over an all-abandoned subtree does go through
    ``read_entry(delivering=True)``, so the refusal is reached publicly too.
    """
    port = board([make_tracker_issue(SUBJECT, issue_labels=frozenset({STAGE_KEY}))])
    spec = TrackerSpec(
        subject=SUBJECT,
        body="the subject's own text",
        criteria=(),
        read_at_version="1",
    )

    with pytest.raises(FireSpecEntryError, match="no criteria to deliver"):
        TrackerCriteria(tracker=port)._finished(spec, {})


@pytest.mark.parametrize("kind", ["new", "resumed", "deliver_only"])
def test_only_a_lane_entered_to_deliver_is_prepared_already_accepted(kind) -> None:
    """The verdict states the entry's own fact, and only that entry's.

    A deliver-only entry exists because every criterion of the subtree is
    Done, which is what acceptance means once the evaluation crossed them
    off, so consolidation's gate can read it off the state. A new or resumed
    lane has work left and must earn the verdict in the loop.
    """
    fire = engine(criteria=TrackerCriteria(tracker=tracker()))

    state = prepared(fire, entry=entry_of(kind))

    expected = (
        AcceptVerdict.accepted if kind == "deliver_only" else AcceptVerdict.rejected
    )
    assert state["accept_verdict"] is expected
    if kind == "new":
        assert state["feature_branch"] != RECORDED_DELIVERABLE
        assert state["work_base_ref"] == "main"
    else:
        # Both recorded entries continue the same two branches and cut
        # nothing; the verdict above is the only difference between them.
        assert state["feature_branch"] == RECORDED_DELIVERABLE
        assert state["ralph_branch"] == RECORDED_LOOP
        assert state["work_base_ref"] == RECORDED_LOOP
    # A fire prepared without a walker is a new lane, and earns its verdict.
    assert prepared(fire, entry=None)["accept_verdict"] is AcceptVerdict.rejected


@pytest.mark.parametrize(
    "kind,remediating,destination",
    [
        ("new", False, "rule_open_questions"),
        ("resumed", False, "rule_open_questions"),
        ("deliver_only", False, "merge_to_feature"),
        ("deliver_only", True, "rule_open_questions"),
    ],
)
def test_the_step_before_the_loop_routes_a_delivering_lane_past_it(
    kind, remediating, destination
) -> None:
    """Only a lane with nothing to execute skips the loop, and not on a round.

    A remediation round on such a lane is a fresh obligation the review
    drafted, so it takes the loop like any other round; the entry kind alone
    does not send it to consolidation.
    """
    fire = engine(criteria=TrackerCriteria(tracker=tracker()))
    state = prepared(fire, entry=entry_of(kind))
    if remediating:
        state["remediation_ticket"] = RemediationPlan(
            instructions="close what the review named, under the same Checks"
        )

    assert fire._route_after_revalidation(state) == destination

    # The route the parameters above choose between is one the graph holds:
    # consolidation is reachable from the step without the loop at all.
    assert fire.native_graph is not None
    edges = {(edge.source, edge.target) for edge in fire.native_graph.get_graph().edges}
    assert ("revalidate_criteria", "merge_to_feature") in edges
    assert ("revalidate_criteria", "rule_open_questions") in edges
    assert ("rule_open_questions", "run_ralph_loop") in edges


class CountingSource:
    """The criteria source, answering as usual and recording which reading ran.

    Which of the two readings this step takes is the whole subject of the
    test below, and both answer a roster the state cannot tell apart, so the
    fact is unobservable on the result alone.
    """

    def __init__(self, source: TrackerCriteria) -> None:
        self._source = source
        #: Every reading made, by name, with the roster it was held to or
        #: the entry mode it was taken in.
        self.calls: list[tuple[str, object]] = []

    async def read_entry(self, *, issue_key: str, delivering: bool = False):
        self.calls.append(("read_entry", delivering))
        return await self._source.read_entry(issue_key=issue_key, delivering=delivering)

    async def read_current(self, *, spec, held=None):
        self.calls.append(("read_current", held))
        return await self._source.read_current(spec=spec, held=held)


@pytest.mark.parametrize("round_two", [False, True])
async def test_a_delivering_lane_reads_its_finished_roster_once_and_then_holds_it(
    round_two,
):
    """The finished reading is the entry's, and a later pass revalidates it.

    A lane entered to deliver owes nothing, so its first pass reads the
    roster its subtree finished. A remediation round the review sent back
    re-enters the same step carrying that roster, and revalidating it is the
    owed reading held to it — the barrier every other pass makes. Reading the
    finished roster again there would take the entry's reading twice and
    answer a question about the subtree instead of about this run.
    """
    port, source, spec = await finished_subtree()
    _, roster = await source.read_entry(issue_key=SUBJECT, delivering=True)
    counting = CountingSource(source)
    state = {
        "issue_key": SUBJECT,
        "fire_spec": spec if round_two else None,
        "lane_entry": entry_of("deliver_only"),
        "criterion_set": roster if round_two else None,
    }

    result = await revalidate_criteria(state, {}, source=counting)

    expected = ("read_current", roster) if round_two else ("read_entry", True)
    assert counting.calls == [expected]
    assert result["criterion_set"] == roster
    # Equal, not identical: the first pass composes its own spec, and
    # finishing a criterion does not touch the subject it is composed from.
    assert result["fire_spec"] == spec
    assert port.issues[DIRECT_DONE].state_kind is WorkflowStateKind.COMPLETED


async def test_a_fixture_supplying_a_persisted_set_fails_at_the_native_barrier():
    """The set is read from the tracker at the write, never carried in (KOD-652).

    A persisted criteria document handed to the native entry barrier is
    refused by type before the barrier reads anything, so a run cannot
    enter on criteria carried in on a branch file. The sibling case above
    is the control: a roster of the kind the tracker read produces passes
    through the same slot.
    """
    port = tracker()
    source = TrackerCriteria(tracker=port)
    spec, _ = await source.read_entry(issue_key=SUBJECT)
    counting = CountingSource(source)
    state = {
        "issue_key": SUBJECT,
        "fire_spec": spec,
        "lane_entry": entry_of("new"),
        "criterion_set": CriteriaArtifact(
            criteria=make_criteria("recorded"),
            conjunction=ConjunctionVerdict(satisfiable=True),
        ),
    }

    with pytest.raises(PersistedCriterionSetError):
        await revalidate_criteria(state, {}, source=counting)

    assert counting.calls == []
    assert port.issues[DIRECT_OWED].state_kind is WorkflowStateKind.UNSTARTED
