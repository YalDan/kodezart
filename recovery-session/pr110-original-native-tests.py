"""The tracker-native fire: an execution-only graph over staged criteria.

Two contracts live here.  The graph HOLDS no ticket- or criteria-generation
node and reaches its loop only through the pre-loop re-validation step; the
step reads what the fire owes from the tracker's own spec read, over the
subject's whole subtree, and nothing carried alongside that read stands in
for it.
"""

import pytest

from kodezart.chains.criteria import TrackerCriteria
from kodezart.chains.fire_consolidation import FireConsolidation
from kodezart.chains.fire_implementation import FireImplementation
from kodezart.chains.fire_remediation import FireRemediation
from kodezart.chains.fire_review import FireReview
from kodezart.chains.fire_specification import FireSpecification
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.domain.errors import (
    FireSpecEntryError,
    InvalidFireCriterionError,
    ScopedExecutionUnavailableError,
)
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode
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
    FakeTicketGenerator,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_passing_evaluation,
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
    """A criterion sub-issue on the board's own four-row template."""
    return (
        f"**Check:** the check {key} states\n"
        f"**Do:** the build {key} names\n"
        f"**Evidence:** the evidence {key} recorded\n"
        "**Class:** hard"
    )


def check_of(key: str) -> str:
    return f"the check {key} states"


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
    return FakeTrackerPort(
        issues=issues,
        criteria_stage_label_key=STAGE_KEY,
        scope_label_members={
            ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT): (
                frozenset({ScopeLabel.APPROVED}) if approved else frozenset()
            )
        },
    )


def engine(
    *,
    criteria: TrackerCriteria | None,
    quality_gate: FakeQualityGate | None = None,
) -> RalphWorkflowEngine:
    """The fire engine, wired the way composition wires it, plus the stage."""
    service = AgentService(
        git_base_url="https://github.com",
        executor=FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    prompts = make_prompt_provider()
    gate = PassThroughGate()
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
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
            quality_gate=quality_gate
            or FakeQualityGate(
                events=[],
                evaluation=make_passing_evaluation(),
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
            service=service,
            prompts=prompts,
            skills=SUPPRESS_ALL_SKILLS,
            git=git,
            cache=FakeRepoCache(),
            fan_in_max_attempts=2,
        ),
        remediation=FireRemediation(remediator=None, remediation_max_rounds=0),
        git_base_url="https://github.com",
        retry_max_attempts=1,
        retry_initial_interval=0,
        delay_floor_for=no_delay_floor,
        criteria=criteria,
    )


async def drive(fire: RalphWorkflowEngine, *, scope: ScopeRef | None) -> None:
    async for _ in fire.run(
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
        pass


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
        evaluation=make_passing_evaluation(),
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
