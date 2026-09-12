"""The tracker-native fire: an execution-only graph over staged criteria.

Two contracts live here.  The graph HOLDS no ticket- or criteria-generation
node and reaches its loop only through the pre-loop re-validation step; the
step reads what the fire owes from the tracker's own spec read, over the
subject's whole subtree, and nothing carried alongside that read stands in
for it.
"""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from kodezart.chains.criteria import TrackerCriteria
from kodezart.chains.fire_consolidation import FireConsolidation
from kodezart.chains.fire_implementation import FireImplementation
from kodezart.chains.fire_remediation import FireRemediation
from kodezart.chains.fire_review import FireReview
from kodezart.chains.fire_specification import FireSpecification
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.chains.remediation import RemediationChain
from kodezart.core.protocols import QualityGate
from kodezart.domain.errors import (
    FireSpecEntryError,
    InvalidFireCriterionError,
    ScopedExecutionUnavailableError,
)
from kodezart.domain.thread_id import workflow_thread_id
from kodezart.domain.workflow_state import validated_criteria
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
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
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.outcome import WorkflowOutcome
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
    quality_gate: QualityGate | None = None,
    executor: FakeAgentExecutor | None = None,
    real_loop: bool = False,
    remediation_rounds: int = 0,
    max_iterations: int = 1,
    checkpointer=None,
) -> RalphWorkflowEngine:
    """The fire engine, wired the way composition wires it, plus the stage."""
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor or FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    prompts = make_prompt_provider()
    gate = PassThroughGate()
    git = FakeGitService(remote_branch_shas={"main": "b" * 40})
    if real_loop:
        quality_gate = RalphLoop(
            service=service,
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
            fan_in_max_attempts=1,
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
                evaluation=AcceptanceCriteriaOutput.model_validate(native_evaluation()),
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
                    service=service, prompts=prompts, skills=SUPPRESS_ALL_SKILLS
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
        evaluation=AcceptanceCriteriaOutput.model_validate(native_evaluation()),
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
        evaluation=AcceptanceCriteriaOutput.model_validate(native_evaluation()),
        last_commit_sha="a" * 40,
    )
    fire = engine(criteria=TrackerCriteria(tracker=tracker()), quality_gate=gate)

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


def native_evaluation(*, failed: bool = False, checks=None):
    selected = checks or {key: check_of(key) for key in OWED_KEYS}
    return {
        "criteriaResults": [
            {
                "criterionId": key,
                "criterion": "an evaluator echo",
                "passed": not failed,
                "reasoning": "Observed the selected check.",
            }
            for key in selected
        ]
    }


class NativeExecutor(FakeAgentExecutor):
    """Only the agent boundary is scripted; all execution consumers are real."""

    def __init__(self, evaluations, *, on_remediation=None):
        super().__init__(events=[])
        self.evaluations = list(evaluations)
        self.on_remediation = on_remediation
        self.schema_calls = []
        self.execution_prompts = []
        self.evaluation_prompts = []
        self.remediation_prompts = []
        self.on_evaluation = None

    async def stream(self, **kwargs):
        output_format = kwargs.get("output_format")
        properties = (output_format or {}).get("schema", {}).get("properties", {})
        self.schema_calls.append(properties)
        if "criteriaResults" in properties:
            assert self.evaluations, "Unexpected extra evaluation"
            self.evaluation_prompts.append(kwargs["prompt"])
            output = self.evaluations.pop(0)
            if self.on_evaluation is not None:
                self.on_evaluation(len(self.evaluation_prompts))
        elif "instructions" in properties:
            self.remediation_prompts.append(kwargs["prompt"])
            if self.on_remediation is not None:
                self.on_remediation()
            output = {"instructions": "Repair only the observed failing behavior."}
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
            session_id="native-session",
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
        update = {"state_kind": WorkflowStateKind.COMPLETED, "state_name": "Done"}
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
    from kodezart.core.config import AppConfig
    from tests.fakes import FakeRefPublisher

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
        config=AppConfig(
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
    # Direct fire proves constructor capability only. The outer public scope
    # router still refuses scoped jobs until its separate production slice.
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
