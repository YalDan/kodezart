"""The tracker-native fire: an execution-only graph over staged criteria.

Two contracts live here.  The graph HOLDS no ticket- or criteria-generation
node and reaches its loop only through the pre-loop re-validation step; the
step admits the subject through the tracker's own read and measures what the
fire owes over that subject's whole subtree, in one reading, and nothing
carried alongside that read stands in for it.
"""

import ast
import collections
import functools
import importlib.util
import inspect
import math
import pathlib
import re
import types
import typing
from unittest.mock import Mock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

import kodezart
from kodezart.adapters.job_registry import InMemoryJobRegistry
from kodezart.chains import fire_implementation, fire_review
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
from kodezart.domain.workflow_state import (
    recorded_native_roster,
    validated_criteria,
)
from kodezart.services.agent_service import AgentService
from kodezart.services.fire_time_rulings import FireTimeRulings
from kodezart.services.lane_state_writer import TrackerLaneStateWriter
from kodezart.services.native_amendments import NativeAmendments
from kodezart.services.scope_membership import read_scope_members
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    BRANCH_NAME_SCHEMA,
    AcceptanceCriteriaOutput,
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
    owns_workspace: bool = True,
    rulings=DEFAULT,
) -> RalphWorkflowEngine:
    """The fire engine, wired the way composition wires it, plus the stage.

    The Git, source and persister doubles default to today's no-commit ones;
    a test about what a commit leaves behind supplies its own repository, and
    a test about WHICH tree a lane opened supplies the workspace provider so
    it can read the acquisitions back.
    *writes_lane_state* and *owns_workspace* are the two collaborators a test
    withholds on purpose: a native loop without either is the wiring the
    execute node refuses at, before it opens a session.
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

    @property
    def _tracker(self):
        """The board behind the source, which composition wires beside it.

        Exposed so this wrapper can stand where the source itself stands
        when a whole engine is built on it, and "no reading was taken" is
        then a statement about every node rather than about one call.
        """
        return self._source._tracker

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


@functools.cache
def _package_modules():
    """Every module of the package, imported, beside its own syntax tree.

    Read once per session: one file per module, each parsed once, so every
    walk below is bounded by the package itself.
    """
    package = pathlib.Path(kodezart.__file__).resolve().parent
    found = []
    for path in sorted(package.rglob("*.py")):
        parts = path.relative_to(package.parent).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        found.append(
            (
                importlib.import_module(name),
                ast.parse(path.read_text(encoding="utf-8")),
            )
        )
    return tuple(found)


def _module_named_by(node, modules):
    """The module an expression names through module aliases, or nothing."""
    if isinstance(node, ast.Name):
        return modules.get(node.id)
    if isinstance(node, ast.Attribute):
        outer = _module_named_by(node.value, modules)
        inner = getattr(outer, node.attr, None) if outer is not None else None
        return inner if isinstance(inner, types.ModuleType) else None
    return None


def _unwrapped(value):
    """A function stored on a class, as the function object itself."""
    if isinstance(value, (staticmethod, classmethod)):
        return value.__func__
    return value


def _is_one_of(value, functions):
    """Whether *value* IS one of *functions*: identity, never equality."""
    return any(value is function for function in functions)


def _value_named_by(node, namespace, modules):
    """The object a name or a module-alias attribute names in its module."""
    if isinstance(node, ast.Name):
        return namespace.get(node.id)
    if isinstance(node, ast.Attribute):
        owner = _module_named_by(node.value, modules)
        return getattr(owner, node.attr, None) if owner is not None else None
    return None


def _classes_named_by(annotation, namespace, modules):
    """The classes an annotation declares, read in the annotation's module.

    A class named directly or through a module alias, either side of ``|``,
    and the members of ``Optional[...]`` or ``Union[...]``.  Anything else,
    a string annotation included, declares no class.
    """
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _classes_named_by(
            annotation.left, namespace, modules
        ) | _classes_named_by(annotation.right, namespace, modules)
    if isinstance(annotation, ast.Subscript):
        generic = _value_named_by(annotation.value, namespace, modules)
        if generic is not typing.Optional and generic is not typing.Union:
            return set()
        members = annotation.slice
        return set().union(
            *(
                _classes_named_by(member, namespace, modules)
                for member in (
                    members.elts if isinstance(members, ast.Tuple) else [members]
                )
            )
        )
    value = _value_named_by(annotation, namespace, modules)
    return {value} if isinstance(value, type) else set()


def _receiver_name(method):
    """The name a method's own instance (or class) is bound to, if any."""
    if any(
        isinstance(decorator, ast.Name) and decorator.id == "staticmethod"
        for decorator in method.decorator_list
    ):
        return None
    positional = [*method.args.posonlyargs, *method.args.args]
    return positional[0].arg if positional else None


def _paired(target, value):
    """Each (target, value) an assignment pairs, unpacking equal-length tuples."""
    if (
        isinstance(target, (ast.Tuple, ast.List))
        and isinstance(value, (ast.Tuple, ast.List))
        and len(target.elts) == len(value.elts)
    ):
        return [
            pair
            for inner, source in zip(target.elts, value.elts, strict=True)
            for pair in _paired(inner, source)
        ]
    return [(target, value)]


#: The nodes that open a scope of their own inside a module.
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_nodes(scope):
    """Every node *scope* holds itself, the scopes nested in it left out.

    Each node of the tree is visited at most once, so the walk is bounded by
    the scope's own source.
    """
    body = scope.body if isinstance(scope.body, list) else [scope.body]
    pending = [node for node in body if not isinstance(node, _SCOPES)]
    while pending:
        node = pending.pop()
        yield node
        pending += [
            child
            for child in ast.iter_child_nodes(node)
            if not isinstance(child, _SCOPES)
        ]


def _local_bindings(scope):
    """Each ``(name, value)`` *scope* binds a plain name to, form by form.

    Read in the scope's own body -- a function's, a lambda's or the module's
    -- and never in a scope nested inside it: an assignment (plain,
    annotated, chained, or unpacking tuples of equal length), an assignment
    expression, a ``for`` target -- a loop's or a comprehension's -- over a
    literal tuple, list or set, and a parameter's default value.
    """
    pairs = []
    for node in _own_nodes(scope):
        if isinstance(node, ast.Assign):
            pairs += [
                pair for target in node.targets for pair in _paired(target, node.value)
            ]
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value:
            pairs.append((node.target, node.value))
        elif isinstance(
            node, (ast.For, ast.AsyncFor, ast.comprehension)
        ) and isinstance(node.iter, (ast.Tuple, ast.List, ast.Set)):
            pairs += [
                pair
                for element in node.iter.elts
                for pair in _paired(node.target, element)
            ]
    bound = [
        (target.id, value) for target, value in pairs if isinstance(target, ast.Name)
    ]
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        arguments = scope.args
        positional = [*arguments.posonlyargs, *arguments.args]
        defaulted = positional[len(positional) - len(arguments.defaults) :]
        bound += [
            (argument.arg, default)
            for argument, default in zip(defaulted, arguments.defaults, strict=True)
        ]
        bound += [
            (argument.arg, default)
            for argument, default in zip(
                arguments.kwonlyargs, arguments.kw_defaults, strict=True
            )
            if default is not None
        ]
    return bound


def _attribute_classes(klass, namespace, modules):
    """The classes each instance attribute holds, as the class declares it.

    Declared means a class-body annotation, an annotated assignment to
    ``self.<attribute>``, or an assignment to ``self.<attribute>`` from a
    parameter of the same method whose annotation names the class -- the
    way ``__init__`` stores what it is handed.
    """
    declared = {}
    for statement in klass.body:
        if isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            declared.setdefault(statement.target.id, set()).update(
                _classes_named_by(statement.annotation, namespace, modules)
            )
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        receiver = _receiver_name(statement)
        arguments = statement.args
        parameters = {
            argument.arg: argument.annotation
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            )
            if argument.annotation is not None
        }
        for node in ast.walk(statement):
            if isinstance(node, ast.AnnAssign):
                pairs = [(node.target, None)]
            elif isinstance(node, ast.Assign):
                pairs = [
                    pair
                    for target in node.targets
                    for pair in _paired(target, node.value)
                ]
            else:
                continue
            for target, value in pairs:
                if not (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == receiver
                ):
                    continue
                if value is None:
                    annotation = node.annotation
                elif isinstance(value, ast.Name) and value.id in parameters:
                    annotation = parameters[value.id]
                else:
                    continue
                declared.setdefault(target.attr, set()).update(
                    _classes_named_by(annotation, namespace, modules)
                )
    return declared


def _calls_in(module, tree, functions):
    """The holder of each call in *module* whose callee IS one of *functions*."""
    return [holder for holder, _, _ in _located_calls_in(module, tree, functions)]


def _located_calls_in(module, tree, functions):
    """Each call in *module* to *functions*: its holder, the call, the holder's node.

    The holder's node is the outermost function's definition, or nothing for
    a call recorded by its line.
    """
    namespace = vars(module)
    # Resolved by identity, not by spelling: an aliased import and a
    # module-level rebinding both leave a name whose value is the function.
    names = {name for name, value in namespace.items() if _is_one_of(value, functions)}
    modules = {
        name: value
        for name, value in namespace.items()
        if isinstance(value, types.ModuleType)
    }

    # An import made inside a function binds nothing at module level, so it
    # is read from the tree, in the scope that makes it, and resolved the
    # same way: the name it binds counts when the object it imports IS the
    # function (or a module).  A plain ``import a.b.c`` binds only ``a``, to
    # the top-level package, and the call's dotted attributes reach the
    # function from there.
    def imported_into(scope, names, modules):
        for node in _own_nodes(scope):
            if isinstance(node, ast.ImportFrom):
                source = importlib.import_module(
                    importlib.util.resolve_name(
                        "." * node.level + (node.module or ""), module.__package__
                    )
                    if node.level
                    else node.module
                )
                for alias in node.names:
                    value = getattr(source, alias.name, None)
                    if _is_one_of(value, functions):
                        names.add(alias.asname or alias.name)
                    elif isinstance(value, types.ModuleType):
                        modules[alias.asname or alias.name] = value
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported = importlib.import_module(alias.name)
                    if alias.asname:
                        modules[alias.asname] = imported
                    else:
                        top = alias.name.partition(".")[0]
                        modules[top] = importlib.import_module(top)

    # A method is reached through its own instance: ``self.<method>`` in a
    # method of the class, or ``self.<attribute>.<method>`` where the class
    # declares what that attribute holds.  Either is resolved on the class,
    # so the callee is still the function object itself.
    declared_by_class = {}

    def receiver_of(prefix, klass, method):
        owner = module
        for part in prefix:
            owner = getattr(owner, part, None)
        receiver = _receiver_name(method)
        if not isinstance(owner, type) or receiver is None:
            return None
        if id(klass) not in declared_by_class:
            declared_by_class[id(klass)] = _attribute_classes(klass, namespace, modules)
        return receiver, owner, declared_by_class[id(klass)]

    # A scope is ``(receiver, names, narrowed, modules)``: the method's own
    # instance, if any; the plain names whose value IS the function; the
    # local names that hold a declared ``self`` attribute, with its classes;
    # and the names that hold a module.
    def classes_of(node, scope):
        receiver, _, narrowed, _ = scope
        if isinstance(node, ast.Name):
            if receiver is not None and node.id == receiver[0]:
                return (receiver[1],)
            return tuple(narrowed.get(node.id, ()))
        if (
            receiver is not None
            and isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == receiver[0]
        ):
            return tuple(receiver[2].get(node.attr, ()))
        return ()

    # A method reached through its class, ``<Class>.<method>`` with the
    # class named directly or through a module alias, or through
    # ``super()`` inside a method, which looks it up in the classes after
    # the method's own class in that class's MRO.
    def looked_up(node, attribute, scope):
        receiver = scope[0]
        if (
            receiver is not None
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "super"
            and "super" not in namespace
            and not node.args
            and not node.keywords
        ):
            return [
                next(
                    (
                        inspect.getattr_static(klass, attribute)
                        for klass in receiver[1].__mro__[1:]
                        if attribute in vars(klass)
                    ),
                    None,
                )
            ]
        named = _value_named_by(node, namespace, scope[3])
        classes = {
            *classes_of(node, scope),
            *([named] if isinstance(named, type) else []),
        }
        return [inspect.getattr_static(klass, attribute, None) for klass in classes]

    def is_function(value, scope):
        if isinstance(value, ast.NamedExpr):
            return is_function(value.value, scope)
        if isinstance(value, ast.Name):
            return value.id in scope[1]
        if not isinstance(value, ast.Attribute):
            return False
        owner = _module_named_by(value.value, scope[3])
        if owner is not None:
            return _is_one_of(getattr(owner, value.attr, None), functions)
        return any(
            _is_one_of(_unwrapped(found), functions)
            for found in looked_up(value.value, value.attr, scope)
        )

    # A local binding exists only in the tree, and only in its own scope: a
    # name the scope binds to a value that already resolves to the function
    # is the function too, to a fixed point, and so is every name a nested
    # scope sees from it.  A local bound to a declared ``self`` attribute --
    # the narrowing ``x = self._x`` before ``if x is None`` -- holds what
    # that attribute holds.  Each pass adds a name or ends the loop, so it
    # runs at most once per binding.
    def scope_of(node, receiver, outer):
        names, modules_here = set(outer[1]), dict(outer[3])
        imported_into(node, names, modules_here)
        bindings = _local_bindings(node)
        narrowed = dict(outer[2])
        for name, value in bindings:
            if (
                receiver is not None
                and isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == receiver[0]
            ):
                narrowed[name] = {
                    *narrowed.get(name, ()),
                    *receiver[2].get(value.attr, ()),
                }
        grown = True
        while grown:
            scope = (receiver, names, narrowed, modules_here)
            added = {name for name, value in bindings if is_function(value, scope)}
            added -= names
            names |= added
            grown = bool(added)
        return receiver, frozenset(names), narrowed, modules_here

    found = []

    def walk(parent, prefix, outer, scope):
        for child in ast.iter_child_nodes(parent):
            here, path, inner = outer, prefix, scope
            if outer is None and isinstance(child, ast.ClassDef):
                path = (*prefix, child.name)
            if outer is None and isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                here = (".".join((*prefix, child.name)), child)
                if isinstance(parent, ast.ClassDef):
                    inner = (receiver_of(prefix, parent, child), *scope[1:])
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                inner = scope_of(child, inner[0], inner)
            if isinstance(child, ast.Call) and is_function(child.func, inner):
                holder = here[0] if here else f"module line {child.lineno}"
                found.append(((module.__name__, holder), child, here and here[1]))
            walk(child, path, here, inner)

    walk(tree, (), None, scope_of(tree, None, (None, frozenset(names), {}, modules)))
    return found


def calls_to(*functions):
    """The holder of every call in the package to any of *functions*.

    One entry per call, so a function that calls twice is listed twice.
    """
    return sorted(
        holder
        for module, tree in _package_modules()
        for holder in _calls_in(module, tree, functions)
    )


#: The statements after which a block does not go on to what follows it.
_EXITS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def _holds(node, targets):
    """Whether one of the *targets* nodes is *node* or lies inside it."""
    return any(inner is target for inner in ast.walk(node) for target in targets)


def _blocks_of(statement):
    """The statement lists a compound statement holds, each a block."""
    parts = [
        statement,
        *(
            child
            for child in ast.iter_child_nodes(statement)
            if isinstance(child, (ast.excepthandler, ast.match_case))
        ),
    ]
    return [
        value
        for part in parts
        for _, value in ast.iter_fields(part)
        if isinstance(value, list) and value and isinstance(value[0], ast.stmt)
    ]


def _ifs_in(statement):
    """The outermost ``if`` statements of *statement*, looking through blocks."""
    if isinstance(statement, ast.If):
        return [statement]
    return [
        found
        for block in _blocks_of(statement)
        for inner in block
        for found in _ifs_in(inner)
    ]


def _ways_to_hold(test):
    """How many ways *test* can hold: one per disjunct of its ``or``.

    An ``or`` holds by any one of its operands, and an ``and`` by one way
    of each of its operands at once; any other condition holds one way.
    """
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return sum(_ways_to_hold(value) for value in test.values)
    if isinstance(test, ast.BoolOp):
        return math.prod(_ways_to_hold(value) for value in test.values)
    return 1


def _reaches_past(block, routed):
    """Whether *block* falls through having passed one of its *routed* calls.

    Such a branch rejoins the node with a check behind it, and what it did
    around that check, which another branch never did.  A branch that ends
    in a return, a raise, a ``continue`` or a ``break`` never rejoins at
    all; a compound last statement is read block by block.
    """
    if not block or isinstance(block[-1], _EXITS):
        return False
    if any(_holds(statement, routed) for statement in block[:-1]):
        return True
    inner = _blocks_of(block[-1])
    if not inner:
        return _holds(block[-1], routed)
    return any(_reaches_past(branch, routed) for branch in inner)


def _arms_and_skips(call, function, routed):
    """The arms of *function* into *call*, and how many of them skip it.

    The count ``_arms_into`` states, split: the arms that reach *call*
    with the disjuncts of its wrapping guards, and the skip sides of those
    guards, one per wrapping ``if`` with no ``else``.
    """
    arms, around, guards, skips = 0, True, 0, 0
    block = function.body
    while block:
        index = next(
            i for i, statement in enumerate(block) if _holds(statement, [call])
        )
        for earlier in block[:index]:
            for branching in _ifs_in(earlier):
                sides = [
                    (branching.body, _ways_to_hold(branching.test)),
                    (branching.orelse, 1),
                ]
                bearing = any(_reaches_past(side, routed) for side, _ in sides)
                taken = [
                    bearing and bool(side) and not isinstance(side[-1], _EXITS)
                    for side, _ in sides
                ]
                arms += sum(
                    ways for (_, ways), took in zip(sides, taken, strict=True) if took
                )
                around = around and any(
                    not took and not (side and isinstance(side[-1], _EXITS))
                    for (side, _), took in zip(sides, taken, strict=True)
                )
        holding = block[index]
        if isinstance(holding, ast.If) and any(
            _holds(statement, [call]) for statement in holding.body
        ):
            guards += _ways_to_hold(holding.test) - 1
            skips += not holding.orelse
        block = next(
            (
                inner
                for inner in _blocks_of(holding)
                if any(_holds(statement, [call]) for statement in inner)
            ),
            None,
        )
    reached = arms + around
    return (reached + guards, skips) if reached else (0, 0)


def _arms_into(call, function, routed):
    """How many arms of *function* lead to *call* or around it, off its source.

    An earlier ``if`` with a branch that passes a *routed* call and falls
    through is one arm per way into each branch of it that falls through:
    the body once per disjunct of its condition's ``or``, the ``else``
    once.  The path that takes no such branch is one more, when every
    earlier ``if`` can be passed some other way.  An ``if`` that encloses
    *call* in its body adds an arm per further disjunct of its condition,
    and, when it has no ``else``, one more: its skip side, on which the
    function goes on past *call* without making it.  An ``if`` is earlier
    when it comes before *call* in a block that holds *call*, at any depth,
    looking through ``with``, ``for``, ``try`` and ``match`` blocks to the
    ``if`` statements inside them.  Each block holding *call* is read once,
    so the walk is bounded by the function.
    """
    return sum(_arms_and_skips(call, function, routed))


def _skips_around(call, function, routed):
    """How many of the arms ``_arms_into`` counts skip *call* (see there)."""
    return _arms_and_skips(call, function, routed)[1]


def _counted_in(module, tree, functions, count, by_line):
    """Each holder's *count* over its calls to *functions* in one module.

    *count* is taken per call the holder makes, with the other calls of the
    same holder as its routed calls; a call recorded by its line counts
    *by_line*.
    """
    counted = collections.Counter()
    located = _located_calls_in(module, tree, functions)
    for holder, call, function in located:
        routed = [other for _, other, owner in located if owner is function]
        counted[holder] += count(call, function, routed) if function else by_line
    return counted


def _routes_in(module, tree, functions):
    """Each holder's routes to *functions* in one module (see ``routes_to``)."""
    return _counted_in(module, tree, functions, _arms_into, 1)


def _bypasses_in(module, tree, functions):
    """Each holder's skip sides around *functions* in one module."""
    return _counted_in(module, tree, functions, _skips_around, 0)


def _counted_to(functions, count_in):
    """*count_in* summed over the package, by holder, as a plain dict."""
    counted = collections.Counter()
    for module, tree in _package_modules():
        counted.update(count_in(module, tree, functions))
    return dict(counted)


def routes_to(*functions):
    """Each holder's routes to *functions*: one per (call, arm that reaches it).

    A call is a route on every arm of its holder that reaches it, since what
    the node did since its last check differs from arm to arm, and on the
    skip side of each guard with no ``else`` around it (see ``_arms_into``).
    A call recorded by its line is one route.
    """
    return _counted_to(functions, _routes_in)


def bypasses_to(*functions):
    """How many of each holder's routes to *functions* skip the call.

    One per guard with no ``else`` around a call: the skip side, which
    ``routes_to`` counts among the holder's routes and on which the call is
    not made, so no arrival can meet the barrier at it.  A holder with none
    is left out.
    """
    return _counted_to(functions, _bypasses_in)


def callers_of(*functions):
    """Every function in the package that calls *functions*, found by identity.

    A call counts when its callee resolves to the function object itself,
    in a ``def`` and an ``async def`` alike.  Followed, each held by a
    control over ``RESOLVER_PROBE`` below:

    - a direct call, through a name whose module-level value IS the function
      (so an aliased import or a module-level rebinding counts);
    - ``self.<method>`` inside the class, ``self.<attribute>.<method>``
      where the class declares the attribute's class, ``<Class>.<method>``
      where ``<Class>`` is a module-level name whose value IS a class, and
      ``super().<method>`` inside a method, looked up in the classes after
      the method's own class in its MRO; each counts when that class's
      attribute IS the function, a ``staticmethod`` or ``classmethod``
      unwrapped;
    - a declaration by an ``Optional[...]``, ``Union[...]`` or ``|``
      annotation, a class-body annotation, an annotated assignment to
      ``self.<attribute>``, or an assignment from an annotated parameter;
    - a plain ``import a.b.c`` inside a function, which binds ``a`` to the
      top-level package, from which ``a.b.c.<function>`` reaches the
      function attribute by attribute; ``import a.b as m``,
      ``from ... import <module> as m`` and ``from ... import <function> as
      x`` inside the calling function or one enclosing it; and a module
      alias;
    - a name bound, in the calling function or one enclosing it (the call
      made in a nested ``def``, ``async def`` or lambda), to any of these by
      plain, chained or annotated assignment, by unpacking a tuple into one
      of equal length with no starred target, by a walrus (and a walrus as
      the callee), by a ``for`` or comprehension target over a literal
      tuple or list, or by a positional or keyword-only parameter default,
      a lambda's own parameters included, followed to a fixed point;
    - a bound method held in a local (``check = self._require_current``,
      ``check = self._delivery._require_current``, or by a walrus), and an
      attribute narrowed through a local (``delivery = self._delivery``,
      then ``delivery._require_current(...)``), in the calling function or
      one enclosing it.

    A binding holds only in its own scope, so a same-named local of another
    function, a method of another class with the same name (through
    ``self``, a declared attribute or that class itself), and a same-named
    function of another module are not the function, and a walrus inside a
    lambda binds nothing in the function around it.

    Not seen, and held unseen by the same control: a conditional
    expression; starred unpacking; a ``for`` over a name bound to a
    sequence; a module bound to a local name by assignment; an instance
    held in a parameter, or in a local it was built into; a mapping;
    ``functools.partial``; and a name assembled at run time.

    The reach, stated once for the resolver and the drive: the static
    resolver, ``callers_of``, follows the forms its docstring states, each
    held by a control over ``RESOLVER_PROBE``; the behavioural drive,
    ``test_every_input_of_a_gated_node_meets_the_set_at_every_await``,
    covers every path the enumerated inputs reach, whatever a guard or a
    call is spelled as; and the one general limit is an input no fake
    enumerates -- a port raising an exception the fakes do not raise, or a
    value outside the enum a node branches on -- so a node branch on such
    an input is not driven, which the drive's derived product shows.

    Each caller is recorded as ``(module, qualified name of the outermost
    function)``, so two modules or two classes never merge into one name,
    and a call made in a closure is the node that holds it.  A call outside
    any function -- a class body, a module-level lambda -- is recorded as its
    own line, which no reach table can hold, so it is reported rather than
    folded in.
    """
    return tuple(sorted(set(calls_to(*functions))))


def function_at(node):
    """The function a ``(module, qualified name)`` node names, or nothing.

    Nothing for a call recorded by its line, which names no function.
    """
    module, qualname = node
    value = importlib.import_module(module)
    for part in qualname.split("."):
        value = inspect.getattr_static(value, part, None)
    value = _unwrapped(value)
    return value if inspect.isfunction(value) else None


def reached_through_callers(function):
    """Every function from which *function* is reached, call by call.

    The callers of *function*, then the callers of those, to a fixed point
    over the package's call graph as ``callers_of`` resolves it.  A caller
    that is itself called is a helper, and whoever calls it reaches
    *function* through it; the helper stays in the set as well.  Each round
    adds a function the package defines or ends the loop, so it runs at most
    once per function.
    """
    found = set(callers_of(function))
    frontier = found
    while frontier:
        helpers = [helper for helper in map(function_at, sorted(frontier)) if helper]
        frontier = set(callers_of(*helpers)) - found if helpers else set()
        found |= frontier
    return tuple(sorted(found))


def node_of(function):
    """The ``(module, qualified name)`` a reach table keys *function* under.

    Read off the function object, so a reach table names its nodes the way
    ``callers_of`` records them and never through a hand-typed twin.
    """
    return (function.__module__, function.__qualname__)


#: Every native barrier that states its own roster, derived from the shipped
#: tree instead of named by hand: every function that calls the persisted-set
#: refusal, however it is spelled.  The entry gate is one of four, not the
#: only one: a checkpoint resumed at the loop or at the post-merge review
#: lands on a node that passes *held* as well, and the snapshot barrier the
#: delivery and loop-gate nodes go through asks the same question without
#: being a node itself (KOD-652).
PERSISTED_SET_BARRIERS = callers_of(recorded_native_roster)

#: How each barrier is reached, one hand-written line per barrier.  Which
#: barriers exist is read off the tree; requiring the two to agree is what
#: makes the refusal a statement about the whole set.  A new asking site
#: arrives here without a way to reach it, and a site that stops asking
#: leaves one behind.
BARRIER_REACH = {
    node_of(revalidate_criteria): lambda at: revalidate_criteria(
        at.state, at.config, source=at.counting
    ),
    node_of(require_current_native_snapshot): lambda at: (
        require_current_native_snapshot(at.state, reader=at.counting)
    ),
    node_of(FireImplementation.run_ralph_loop): lambda at: (
        at.fire.implementation.run_ralph_loop(at.state, at.config)
    ),
    node_of(FireReview.review_against_ticket): lambda at: (
        at.fire.review.review_against_ticket(at.state, at.config)
    ),
}


def test_every_derived_barrier_has_a_reach_and_every_reach_a_barrier():
    """The derived roster is the reach table's keys, and it is never empty.

    Not parametrised: a derivation that found nothing would collect no case
    at all, and the refusal below would then be stated over no barrier while
    reporting green.  Here an empty roster fails outright (KOD-652).
    """
    assert PERSISTED_SET_BARRIERS, "the tree derived no persisted-set barrier"
    assert set(PERSISTED_SET_BARRIERS) == set(BARRIER_REACH)


def persisted_artifact():
    """A criteria document of the kind a branch file carries, not a roster."""
    return CriteriaArtifact(
        criteria=make_criteria("recorded"),
        conjunction=ConjunctionVerdict(satisfiable=True),
    )


@pytest.mark.parametrize("barrier", sorted(BARRIER_REACH), ids=":".join)
async def test_a_fixture_supplying_a_persisted_set_fails_at_the_native_barrier(
    barrier, monkeypatch
):
    """The set is read from the tracker at the write, never carried in (KOD-652).

    A persisted criteria document reaching ANY native barrier is refused by
    type before that barrier reads anything, so a run cannot proceed on
    criteria carried in on a branch file -- not on entry, not on a
    checkpoint resumed at the loop or at the post-merge review, both of
    which state their own roster, and not at the snapshot barrier the
    delivery and loop-gate nodes go through. The delivering-lane case above
    is the control: a roster of the kind the tracker read produces passes
    through the same slot.
    """
    port = tracker()
    counting = CountingSource(TrackerCriteria(tracker=port))
    spec, _ = await counting.read_entry(issue_key=SUBJECT)
    counting.calls.clear()
    fire = engine(criteria=counting)
    # One state over the barriers, holding what each reads before it asks
    # the roster question: the entry gate reads the lane it entered on, the
    # loop and the review read the remediation slot, and the review reads
    # the two shas consolidation left behind.  The branch and counter slots
    # are what the loop reads AFTER the barrier, present so that a barrier
    # which failed to refuse is reported as a refusal that did not happen
    # rather than as a missing key further down.
    state = {
        "issue_key": SUBJECT,
        "fire_spec": spec,
        "lane_entry": entry_of("new"),
        "remediation_ticket": None,
        "review_base_sha": "a" * 40,
        "review_head_sha": "b" * 40,
        "feature_branch": "kodezart/fire-subject",
        "ralph_branch": "kodezart/fire-subject-loop",
        "work_base_ref": "main",
        "repo_visibility": RepoVisibility.PUBLIC,
        "total_iterations": 0,
        "criterion_set": persisted_artifact(),
    }
    config = {
        "configurable": {
            "prompt": "Implement the requested behavior",
            "repo_path": "/tmp/fire",
            "repo_url": "https://github.com/owner/repo",
            "cache_key": "native-fire",
            "base_spec": trunk_base("main"),
            "permission_mode": PermissionMode.UNATTENDED,
            "allowed_tools": ["Bash"],
        }
    }
    for module in (fire_implementation, fire_review):
        monkeypatch.setattr(module, "get_stream_writer", lambda: lambda _: None)
    at = types.SimpleNamespace(state=state, config=config, counting=counting, fire=fire)

    with pytest.raises(PersistedCriterionSetError):
        await BARRIER_REACH[barrier](at)

    assert counting.calls == []
    assert port.issues[DIRECT_OWED].state_kind is WorkflowStateKind.UNSTARTED


# ---------------------------------------------------------------------------
# The resolver's own controls: small modules fed to ``_calls_in``, one
# function per form it follows and one per form it does not.  Every branch
# of the resolver is held here, so deleting one turns a form red, and every
# shape of the stated limit is held unseen, so the limit is a fact the tests
# hold rather than a sentence (KOD-652).
# ---------------------------------------------------------------------------

#: A module whose functions each reach the targets by one form, or by one
#: shape the resolver does not follow, or reach a same-named other thing.
#: The targets are the snapshot check and ``Gate``'s own methods.
RESOLVER_PROBE = """
import functools
from typing import Optional, Union

from kodezart.chains import criteria as _criteria
from kodezart.chains.criteria import require_current_native_snapshot


class Gate:
    def _require_current(self):
        return None

    @staticmethod
    def _static_check():
        return None

    @classmethod
    def _class_check(cls):
        return None

    def own_method(self):
        self._require_current()

    def static_method(self):
        self._static_check()

    def class_method(self):
        self._class_check()

    def bound_method(self):
        check = self._require_current
        check()

    def walrus_method(self):
        (check := self._require_current)()

    async def async_bound_method(self):
        check = self._require_current
        await check()

    async def async_walrus_method(self):
        await (check := self._require_current)()


class Other:
    def _require_current(self):
        return None

    def other_class_method(self):
        self._require_current()


class Sub(Gate):
    def _require_current(self):
        return None

    def through_super(self):
        super()._require_current()


class Piped:
    def __init__(self, gate: Gate | None):
        self._gate = gate

    def attribute_method(self):
        self._gate._require_current()

    def bound_attribute_method(self):
        check = self._gate._require_current
        check()

    def narrowed_attribute(self):
        gate = self._gate
        if gate is None:
            raise ValueError("no gate")
        gate._require_current()

    async def async_bound_attribute_method(self):
        check = self._gate._require_current
        await check()

    async def async_narrowed_attribute(self):
        gate = self._gate
        if gate is None:
            raise ValueError("no gate")
        await gate._require_current()

    async def closure(self):
        check = self._gate._require_current

        async def inner():
            await check()

        await inner()

    async def lambda_closure(self):
        check = self._gate._require_current
        run = lambda: check()
        await run()

    async def narrowed_closure(self):
        gate = self._gate
        if gate is None:
            raise ValueError("no gate")

        async def inner():
            await gate._require_current()

        await inner()


class OptionalDeclared:
    def __init__(self, gate: Optional[Gate]):
        self._gate = gate

    def optional_declared(self):
        self._gate._require_current()


class UnionDeclared:
    def __init__(self, gate: Union[Gate, None]):
        self._gate = gate

    def union_declared(self):
        self._gate._require_current()


class BodyDeclared:
    _gate: Gate

    def body_declared(self):
        self._gate._require_current()


class AssignDeclared:
    def __init__(self):
        self._gate: Gate = Gate()

    def assign_declared(self):
        self._gate._require_current()


class OtherHeld:
    def __init__(self, other: Other):
        self._other = other

    def other_attribute(self):
        self._other._require_current()


def direct(state):
    require_current_native_snapshot(state, reader=None)


async def async_direct(state):
    await require_current_native_snapshot(state, reader=None)


async def async_assignment(state):
    check = require_current_native_snapshot
    await check(state, reader=None)


async def async_default(state, check=require_current_native_snapshot):
    await check(state, reader=None)


def module_alias(state):
    _criteria.require_current_native_snapshot(state, reader=None)


def plain_import(state):
    import kodezart.chains.criteria

    kodezart.chains.criteria.require_current_native_snapshot(state, reader=None)


def local_module_import(state):
    import kodezart.chains.criteria as crit

    crit.require_current_native_snapshot(state, reader=None)


def local_from_import(state):
    from kodezart.chains.criteria import require_current_native_snapshot as ask

    ask(state, reader=None)


def local_module_from_import(state):
    from kodezart.chains import criteria as crit

    crit.require_current_native_snapshot(state, reader=None)


async def enclosing_module_from_import(state):
    from kodezart.chains import criteria as crit

    async def inner():
        await crit.require_current_native_snapshot(state, reader=None)

    await inner()


async def enclosing_module_import(state):
    import kodezart.chains.criteria as crit

    async def inner():
        await crit.require_current_native_snapshot(state, reader=None)

    await inner()


async def lambda_default(state):
    run = lambda s, check=require_current_native_snapshot: check(s, reader=None)
    await run(state)


def lambda_walrus(state):
    run = lambda: (check := require_current_native_snapshot)
    run()
    check(state, reader=None)


def through_the_class(gate):
    Gate._require_current(gate)


def other_through_the_class(other):
    Other._require_current(other)


def assignment(state):
    check = require_current_native_snapshot
    check(state, reader=None)


def chained(state):
    first = second = require_current_native_snapshot
    second(state, reader=None)


def annotated(state):
    check: object = require_current_native_snapshot
    check(state, reader=None)


def unpacking(state):
    check, other = require_current_native_snapshot, None
    check(state, reader=None)


def walrus(state):
    (check := require_current_native_snapshot)(state, reader=None)


def walrus_bound(state):
    if check := require_current_native_snapshot:
        check(state, reader=None)


def loop_target(state):
    for check in (require_current_native_snapshot,):
        check(state, reader=None)


def comprehension_target(state):
    return [check(state, reader=None) for check in [require_current_native_snapshot]]


def positional_default(state, check=require_current_native_snapshot):
    check(state, reader=None)


def keyword_default(state, *, check=require_current_native_snapshot):
    check(state, reader=None)


def other_local(state):
    check = len
    check(state)


def conditional(state):
    check = require_current_native_snapshot if state else None
    check(state, reader=None)


def starred(state):
    check, *rest = require_current_native_snapshot, None, None
    check(state, reader=None)


def loop_over_name(state):
    candidates = (require_current_native_snapshot,)
    for check in candidates:
        check(state, reader=None)


def local_module(state):
    crit = _criteria
    crit.require_current_native_snapshot(state, reader=None)


def parameter_instance(gate: Gate):
    gate._require_current()


def constructed_instance():
    gate = Gate()
    gate._require_current()


def mapping(state):
    {"check": require_current_native_snapshot}["check"](state, reader=None)


def partial(state):
    functools.partial(require_current_native_snapshot, reader=None)(state)


def run_time(state):
    getattr(_criteria, "require_current_" + "native_snapshot")(state, reader=None)
"""

#: The form each function of the probe reaches a target by, one per form
#: ``callers_of`` states it follows.
RESOLVER_FOLLOWS = {
    "direct": "a direct call",
    "Gate.own_method": "self.<method>",
    "Piped.attribute_method": "self.<attribute>.<method>",
    "plain_import": "a plain import inside a function",
    "local_module_import": "an import-as of a module inside a function",
    "local_from_import": "a from-import inside a function",
    "module_alias": "a module alias",
    "assignment": "assignment",
    "chained": "chained assignment",
    "annotated": "annotated assignment",
    "unpacking": "equal-length tuple unpacking",
    "walrus": "a walrus as the callee",
    "walrus_bound": "a name a walrus binds",
    "loop_target": "a for target over a literal sequence",
    "comprehension_target": "a comprehension target over a literal sequence",
    "positional_default": "a positional default",
    "keyword_default": "a keyword-only default",
    "Gate.static_method": "a staticmethod, unwrapped",
    "Gate.class_method": "a classmethod, unwrapped",
    "Piped.bound_attribute_method": "self.<attribute>.<method> held in a local",
    "OptionalDeclared.optional_declared": "an Optional[...] declaration",
    "UnionDeclared.union_declared": "a Union[...] declaration",
    "BodyDeclared.body_declared": "a class-body annotation",
    "AssignDeclared.assign_declared": "an annotated assignment to self",
    "Gate.bound_method": "a bound method held in a local",
    "Gate.walrus_method": "a bound method held by a walrus",
    "Piped.narrowed_attribute": "an attribute narrowed through a local",
    "local_module_from_import": "a from-import of a module inside a function",
    "through_the_class": "<Class>.<method>, the class named by object",
    "Sub.through_super": "super().<method>, the next class in the MRO",
    "async_direct": "a direct call in an async function",
    "async_assignment": "assignment, in an async function",
    "async_default": "a positional default, in an async function",
    "Gate.async_bound_method": "a bound method held in a local, in an async method",
    "Gate.async_walrus_method": "a bound method held by a walrus, in an async method",
    "Piped.async_bound_attribute_method": (
        "self.<attribute>.<method> held in a local, in an async method"
    ),
    "Piped.async_narrowed_attribute": (
        "an attribute narrowed through a local, in an async method"
    ),
    "Piped.closure": "a bound method held by the enclosing function, in a nested def",
    "Piped.lambda_closure": (
        "a bound method held by the enclosing function, in a lambda"
    ),
    "Piped.narrowed_closure": (
        "an attribute narrowed by the enclosing function, in a nested def"
    ),
    "enclosing_module_from_import": (
        "a from-import of a module by the enclosing function, in a nested def"
    ),
    "enclosing_module_import": (
        "an import-as of a module by the enclosing function, in a nested def"
    ),
    "lambda_default": "a lambda's own parameter default",
}

#: The functions of the probe that must stay out: a same-named thing that is
#: not a target, and each shape of the stated limit.
RESOLVER_DOES_NOT_FOLLOW = {
    "other_local": "a same-named local in another function",
    "Other.other_class_method": "a method of another class with the name",
    "OtherHeld.other_attribute": "that method through a declared attribute",
    "other_through_the_class": "that method through its own class",
    "lambda_walrus": "a walrus inside a lambda, read in the enclosing function",
    "conditional": "a conditional expression",
    "starred": "starred unpacking",
    "loop_over_name": "a for over a name bound to a sequence",
    "local_module": "a module bound to a local name",
    "parameter_instance": "an instance held in a parameter",
    "constructed_instance": "an instance held in a local it was built into",
    "mapping": "a mapping",
    "partial": "functools.partial",
    "run_time": "a name assembled at run time",
}

#: A module of its own holding a function named like the check, and a call
#: to that function: the same spelling, another object.
RESOLVER_ELSEWHERE = """
def require_current_native_snapshot(state, reader):
    return None


def same_name(state):
    require_current_native_snapshot(state, reader=None)
"""


def _probe_module(directory, name, source):
    """*source* imported as the module *name*, beside its own syntax tree."""
    path = directory / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, ast.parse(source)


def _probe_targets(module):
    """The snapshot check and ``Gate``'s own methods, as function objects.

    Taken off the wrappers here, not through the resolver's own unwrapping,
    so the unwrapping is under test rather than assumed.
    """
    gate = vars(module.Gate)
    return (
        require_current_native_snapshot,
        gate["_require_current"],
        gate["_static_check"].__func__,
        gate["_class_check"].__func__,
    )


def test_the_resolver_follows_each_stated_form_and_nothing_else(tmp_path):
    """Exactly the functions that reach a target by a followed form are found.

    Not parametrised: one reading of the probe, compared whole.  A branch of
    the resolver that is deleted drops its form from what is found; a shape
    the limit names that starts being followed adds one; either way the two
    sets differ (KOD-652).
    """
    module, tree = _probe_module(tmp_path, "resolver_probe", RESOLVER_PROBE)
    # Both tables name functions the probe defines, and no function twice,
    # so a negative can neither be missing nor hide among the positives.
    assert not set(RESOLVER_FOLLOWS) & set(RESOLVER_DOES_NOT_FOLLOW)
    for qualname in (*RESOLVER_FOLLOWS, *RESOLVER_DOES_NOT_FOLLOW):
        value = module
        for part in qualname.split("."):
            value = vars(value)[part]
        assert inspect.isfunction(value), qualname

    found = {holder for _, holder in _calls_in(module, tree, _probe_targets(module))}

    assert found == set(RESOLVER_FOLLOWS)


def test_a_same_named_function_in_another_module_is_not_the_check(tmp_path):
    """A call to another module's function of the same name is not found."""
    probe, _ = _probe_module(tmp_path, "resolver_probe", RESOLVER_PROBE)
    module, tree = _probe_module(tmp_path, "resolver_elsewhere", RESOLVER_ELSEWHERE)

    assert module.require_current_native_snapshot is not (
        require_current_native_snapshot
    )
    assert _calls_in(module, tree, _probe_targets(probe)) == []


# ---------------------------------------------------------------------------
# The route analyzer's own controls: a small module fed to ``_routes_in``,
# one function per shape an arm into a call can take.  Every branch of the
# analyzer is held here, so deleting one changes a count (KOD-652).
# ---------------------------------------------------------------------------

#: A module whose functions each reach the snapshot check along arms of one
#: shape.  ``len(state)`` stands for whatever a branch does after its check.
ROUTES_PROBE = """
import contextlib

from kodezart.chains.criteria import require_current_native_snapshot as check


def straight(state):
    check(state)


def wrapped(state):
    if state.a:
        check(state)


def wrapped_with_else(state):
    if state.a:
        check(state)
    else:
        len(state)


def wrapped_by_or(state):
    if state.a or state.b:
        check(state)


def wrapped_by_and_of_ors(state):
    if (state.a or state.b) and (state.c or state.d or state.e):
        check(state)


def wrapped_in_else(state):
    if state.a or state.b:
        pass
    else:
        check(state)


def after_acting_if(state):
    if state.a:
        check(state)
        len(state)
    check(state)


def after_if_ending_on_route(state):
    if state.a:
        check(state)
    check(state)


def after_route_in_nested_block(state):
    if state.a:
        with contextlib.nullcontext():
            check(state)
    check(state)


def after_if_else(state):
    if state.a:
        check(state)
    else:
        len(state)
    check(state)


def after_two_if_elses(state):
    if state.a:
        check(state)
    else:
        check(state)
    if state.b:
        check(state)
    else:
        len(state)
    check(state)


def after_acting_or(state):
    if state.a or state.b:
        check(state)
        len(state)
    check(state)


def after_exiting_if(state):
    if state.a:
        check(state)
        return
    check(state)


def after_nested_exit(state):
    if state.a:
        if state.b:
            check(state)
            return
    check(state)


def after_if_else_one_side_exits(state):
    if state.a:
        check(state)
        len(state)
    else:
        return
    check(state)


def after_if_else_without_route(state):
    if state.a:
        len(state)
    else:
        len(state)
    check(state)


def unreachable(state):
    if state.a:
        return
    else:
        raise ValueError(state)
    if state.b or state.c:
        check(state)


def through_with(state):
    with contextlib.nullcontext():
        if state.a:
            check(state)
            len(state)
    check(state)


def through_try(state):
    try:
        len(state)
    except ValueError:
        if state.a:
            check(state)
            len(state)
    check(state)


def through_match(state):
    match state.kind:
        case "one":
            if state.a:
                check(state)
                len(state)
    check(state)


def nested_holding_block(state):
    if state.x:
        if state.a:
            check(state)
            len(state)
        check(state)
"""

#: How many routes each function of the probe has to the check, read by
#: hand off the shape: one per call and per arm of the function into it,
#: the skip side of each wrapping guard with no else among them.
ROUTE_ARMS = {
    # one call, one arm
    "straight": 1,
    # entered, or skipped
    "wrapped": 2,
    # entered; the else is a branch of its own, not a skip side
    "wrapped_with_else": 1,
    # entered by either disjunct, or skipped
    "wrapped_by_or": 3,
    # (a | b) and (c | d | e): two ways times three, or skipped
    "wrapped_by_and_of_ors": 7,
    # the else of an or is entered one way
    "wrapped_in_else": 1,
    # 2 (entered, or skipped), then 2: after the branch, and around it
    "after_acting_if": 4,
    # 2, then 2: a branch ending on its check still rejoins after it
    "after_if_ending_on_route": 4,
    # 2, then 2: the check sits one block deeper in the branch
    "after_route_in_nested_block": 4,
    # 1, then 2: either side, and no way around an if/else
    "after_if_else": 3,
    # 1, 1, 2 (either side of the first), then 4 (either side of each)
    "after_two_if_elses": 8,
    # 3 (a guard of two disjuncts, or skipped), then 3: after each
    # disjunct, and around
    "after_acting_or": 6,
    # 2 (entered, or skipped), then 1: the branch never rejoins
    "after_exiting_if": 3,
    # 3 (entered, or skipped at either guard), then 1: the only branch
    # holding a check leaves from inside it
    "after_nested_exit": 4,
    # 1, then 1: the side that leaves is no arm, and no way around
    "after_if_else_one_side_exits": 2,
    # an if/else with no check in it is no arm of its own
    "after_if_else_without_route": 1,
    # nothing passes the if/else, so no disjunct of the guard is reached
    "unreachable": 0,
    # 2, then 2, the if read through the with
    "through_with": 4,
    # 2, then 2, the if read through the except handler
    "through_try": 4,
    # 2, then 2, the if read through the case
    "through_match": 4,
    # 3 (entered, or skipped at either guard), then 3: the acting if sits
    # inside the block that holds the call, and that block can be skipped
    "nested_holding_block": 6,
}

#: How many of those routes are skip sides: one per guard with no else
#: around a call, read by hand off the same shapes.
ROUTE_SKIPS = {
    "wrapped": 1,
    "wrapped_by_or": 1,
    "wrapped_by_and_of_ors": 1,
    "after_acting_if": 1,
    "after_if_ending_on_route": 1,
    "after_route_in_nested_block": 1,
    "after_acting_or": 1,
    "after_exiting_if": 1,
    "after_nested_exit": 2,
    "through_with": 1,
    "through_try": 1,
    "through_match": 1,
    "nested_holding_block": 3,
}


def test_the_route_analyzer_counts_each_arm_of_each_shape(tmp_path):
    """Each probe function has exactly the routes its shape gives it.

    Not parametrised: one reading of the probe, compared whole.  A branch
    of the analyzer that is deleted changes the count of a shape that needs
    it, so the two tables differ; the skip sides are compared on their own
    as well, so a skip side counted as an arm into the call is told apart
    from one counted as a skip (KOD-652).
    """
    module, tree = _probe_module(tmp_path, "routes_probe", ROUTES_PROBE)
    for name in ROUTE_ARMS:
        assert inspect.isfunction(vars(module)[name]), name
    assert set(ROUTE_SKIPS) <= set(ROUTE_ARMS)

    routes = _routes_in(module, tree, (require_current_native_snapshot,))
    skips = _bypasses_in(module, tree, (require_current_native_snapshot,))

    assert {holder: count for (_, holder), count in routes.items()} == ROUTE_ARMS
    assert {
        holder: count for (_, holder), count in skips.items() if count
    } == ROUTE_SKIPS
