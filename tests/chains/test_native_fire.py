"""The tracker-native fire: an execution-only graph over staged criteria.

Two contracts live here.  The graph HOLDS no ticket- or criteria-generation
node and reaches its loop only through the pre-loop re-validation step; the
step reads what the fire owes from the tracker's own spec read, over the
subject's whole subtree, and nothing carried alongside that read stands in
for it.
"""

import inspect
import re

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

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
    FireSpecEntryError,
    InvalidFireCriterionError,
    LaneEntryError,
    ScopedExecutionUnavailableError,
)
from kodezart.domain.thread_id import workflow_thread_id
from kodezart.domain.workflow_state import validated_criteria
from kodezart.services.agent_service import AgentService
from kodezart.services.fire_time_rulings import FireTimeRulings
from kodezart.services.lane_state_writer import TrackerLaneStateWriter
from kodezart.services.native_amendments import NativeAmendments
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    BRANCH_NAME_SCHEMA,
    AcceptanceCriteriaOutput,
    ResultEvent,
    TicketDraftOutput,
    WorkflowCompleteEvent,
    WorkflowIterationEvent,
    WorkflowRemediationEvent,
    WorkflowReviewEvent,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.criteria import TrackerCriterion, TrackerCriterionSet
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
from kodezart.types.domain.tracker import WorkflowStateKind
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
        make_tracker_issue(SUBJECT, issue_labels=labels, body="the subject's own text"),
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
# The spec read is the source; the subtree is the extent it is read over.
# ---------------------------------------------------------------------------


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
    class Moving(FakeTrackerPort):
        """A criterion archived between the spec read and the subtree read."""

        async def read_fire_spec(self, *, issue_key: str) -> TrackerSpec:
            spec = await super().read_fire_spec(issue_key=issue_key)
            del self.issues[DIRECT_OWED_TOO]
            return spec

    port = tracker()
    moving = Moving(
        issues=list(port.issues.values()),
        criteria_stage_label_key=STAGE_KEY,
        scope_label_members=port.scope_label_members,
    )

    with pytest.raises(InvalidFireCriterionError) as caught:
        await TrackerCriteria(tracker=moving).read_owed_criteria(issue_key=SUBJECT)

    assert caught.value.criterion_key == DIRECT_OWED_TOO


async def test_a_criterion_the_fire_does_not_owe_is_not_revalidated() -> None:
    """A finished criterion is outside the obligation, so its body is too.

    The subject's own direct family is validated whole by the spec read,
    which is that read's contract.  What this stage adds is the subtree,
    and there the state is what decides: a Done criterion under a
    deliverable child is not re-read for a Check it no longer owes.
    """
    stage = TrackerCriteria(
        tracker=tracker(bodies={NESTED_DONE: "no template rows at all"})
    )

    owed = await stage.read_owed_criteria(issue_key=SUBJECT)

    assert set(owed) == {DIRECT_OWED, NESTED_OWED, DIRECT_OWED_TOO}


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
        #: One answer set per pass through the question step, in order, and
        #: the prompt plus the whole call of each pass it opened.
        self.question_answers = []
        self.question_prompts = []
        self.question_sessions = []
        #: One judgement per landed record; the default upholds what landed.
        self.findings = []

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
            self.execution_prompts.append(kwargs["prompt"])
            output = {"claims": []}
        elif output_format is None:
            self.execution_prompts.append(kwargs["prompt"])
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
        self.unavailable = False

    async def read_fire_spec(self, *, issue_key):
        self.spec_reads += 1
        return await super().read_fire_spec(issue_key=issue_key)

    async def scope_issues(self, *, ref):
        if self.unavailable:
            raise ConnectionError("tracker unavailable")
        return await super().scope_issues(ref=ref)


def no_authored_ticket(*args, **kwargs):
    raise AssertionError("Native execution constructed an authored ticket")


async def test_native_graph_executes_and_reviews_exact_checks(monkeypatch):
    port = CountingTracker()
    executor = NativeExecutor([native_evaluation(), native_evaluation()])
    saver = InMemorySaver()
    fire = engine(
        criteria=TrackerCriteria(tracker=port),
        executor=executor,
        real_loop=True,
        checkpointer=saver,
    )
    monkeypatch.setattr(TicketDraftOutput, "__init__", no_authored_ticket)

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
    assert saved["fire_spec"].read_at_version
    assert saved["criteria_validation"] is None
    assert fire.implementation._artifact_persister.persist_calls == []
    assert "the subject's own text" in executor.execution_prompts[0]


async def test_native_remediation_refreshes_checks_without_recapturing_subject(
    monkeypatch,
):
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
    monkeypatch.setattr(TicketDraftOutput, "__init__", no_authored_ticket)

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
    spec = await source.read_spec(issue_key=SUBJECT)
    snapshot = await source.read_current(spec=spec)
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
    monkeypatch.setattr(TicketDraftOutput, "__init__", no_authored_ticket)
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
    spec = await source.read_spec(issue_key=SUBJECT)
    snapshot = await source.read_current(spec=spec)
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
    spec = await source.read_spec(issue_key=SUBJECT)
    return source, spec, await source.read_current(spec=spec)


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
    """The board of a lane whose every criterion is Done, and its spec."""
    port = tracker()
    source = TrackerCriteria(tracker=port)
    spec = await source.read_spec(issue_key=SUBJECT)
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
    """
    _, source, spec = await finished_subtree()

    roster = await source.read_finished(spec=spec)

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

    roster = await source.read_finished(spec=spec)

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
    port, source, spec = await finished_subtree()
    moved(port, NESTED_OWED, kind=kind, name=name)

    with pytest.raises(FireSpecEntryError) as caught:
        await source.read_finished(spec=spec)

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

    roster = await source.read_finished(spec=spec)

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
    port, source, spec = await finished_subtree()
    for key in ALL_CRITERIA:
        moved(port, key, kind=WorkflowStateKind.CANCELED, name="Canceled")

    with pytest.raises(FireSpecEntryError, match="no criteria to deliver") as caught:
        await source.read_finished(spec=spec)

    assert caught.value.issue_key == SUBJECT


async def test_a_subtree_holding_no_criterion_has_nothing_to_deliver():
    """An empty roster is a refusal, never a vacuously finished lane.

    Every criterion of no criteria is Done, so a reading that only checked
    the state would hand a delivery an empty obligation to discharge. The
    readiness read refuses such a member for the same reason; this is the
    same refusal made where the fire enters.
    """
    port = board([make_tracker_issue(SUBJECT, issue_labels=frozenset({STAGE_KEY}))])
    spec = TrackerSpec(
        subject=SUBJECT,
        body="the subject's own text",
        criteria=(),
        read_at_version="1",
    )

    with pytest.raises(FireSpecEntryError, match="no criteria to deliver"):
        await TrackerCriteria(tracker=port).read_finished(spec=spec)


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
        #: Every reading made, by name, with the roster it was held to.
        self.calls: list[tuple[str, TrackerCriterionSet | None]] = []

    async def read_spec(self, *, issue_key: str):
        self.calls.append(("read_spec", None))
        return await self._source.read_spec(issue_key=issue_key)

    async def read_current(self, *, spec, held=None):
        self.calls.append(("read_current", held))
        return await self._source.read_current(spec=spec, held=held)

    async def read_finished(self, *, spec):
        self.calls.append(("read_finished", None))
        return await self._source.read_finished(spec=spec)


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
    roster = await source.read_finished(spec=spec)
    counting = CountingSource(source)
    state = {
        "issue_key": SUBJECT,
        "fire_spec": spec,
        "lane_entry": entry_of("deliver_only"),
        "criterion_set": roster if round_two else None,
    }

    result = await revalidate_criteria(state, {}, source=counting)

    expected = ("read_current", roster) if round_two else ("read_finished", None)
    assert counting.calls == [expected]
    assert result["criterion_set"] == roster
    assert result["fire_spec"] is spec
    assert port.issues[DIRECT_DONE].state_kind is WorkflowStateKind.COMPLETED
