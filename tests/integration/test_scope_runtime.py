"""Real request composition, controller and native graphs with external doubles."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
import structlog.testing
from langgraph.checkpoint.memory import InMemorySaver

from kodezart.chains.criteria import TrackerCriteria
from kodezart.composition.engine import build_workflow_engine
from kodezart.composition.jobs import build_job_queue
from kodezart.config.app import AppConfig
from kodezart.config.job_queue import JobQueueSettings
from kodezart.config.write_back import WriteBackSettings
from kodezart.core.errors import TrackerUnavailableError
from kodezart.domain.agent import mint_lane_branches
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import (
    BaseResolutionError,
    ForgeAPIError,
    GitSourceReadError,
    ScopePlanRefusalError,
)
from kodezart.domain.fire_spec import criterion_field_bodies
from kodezart.domain.git_url import resolve_repo_url
from kodezart.domain.lane_entry import recorded_branches
from kodezart.handlers.agent_handler import AgentHandler
from kodezart.services import scope_runtime
from kodezart.services.agent_service import AgentService
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.agent import (
    ResultEvent,
    SystemEvent,
    WorkflowCompleteEvent,
    WorkflowConsolidationEvent,
    WorkflowIterationEvent,
    WorkflowScopeBaseEvent,
)
from kodezart.types.domain.branch import (
    BranchRole,
    WorkRef,
    WorkRefRole,
    trunk_base,
)
from kodezart.types.domain.consolidation import (
    ChangesetDigest,
    ConsolidationOutcome,
    ConsolidationStatus,
)
from kodezart.types.domain.job import JobState
from kodezart.types.domain.native_delivery import LaneDeliveryEvent
from kodezart.types.domain.operation import LifecycleStage, RepoEntry, ScopeLabel
from kodezart.types.domain.persist import PersistResult, PersistSource
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_runtime import ScopeLaneEvent, ScopeWalkEvent
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.ticket_review import TicketReviewMode
from kodezart.types.domain.tracker import WorkflowStateKind
from kodezart.types.requests.agent import WorkflowRequest
from tests.adapters.test_github_api import _make_client
from tests.api.v1.test_jobs import _build_app
from tests.chains.test_native_fire import (
    NativeExecutor,
    NativeSourceReader,
    native_evaluation,
    native_operation,
)
from tests.fakes import (
    FIXTURE_EPOCH,
    SUPPRESS_ALL_SKILLS,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeChangePersister,
    FakeDeliveryProbe,
    FakeGitService,
    FakeRefPublisher,
    FakeRepoCache,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_prompt_provider,
    make_tracker_issue,
)
from tests.lane_fixture import (
    TRUNK_BRANCHES,
    TRUNK_SHA,
    LaneRepo,
    ScopeForgeWire,
    criteria_echo,
)

ORIGIN = "file:///scope-repository.git"
SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="scoped-project")
STAGED = "criteria-staged"


def board(*, lanes=("A",), blocked=None, approved=True, checks=None):
    """The scope's lanes and their criterion sub-issues.

    *checks* names each lane's criteria; a lane not named there has the one
    criterion every lane has had, under the body every test reads it by.
    """
    rows = []
    for key in lanes:
        rows.append(
            make_tracker_issue(
                key,
                body=f"Exact native subject {key}  with spaces\n",
                blocked_by=(blocked or {}).get(key, ()),
                issue_labels=frozenset({STAGED}),
            )
        )
        for name in (checks or {}).get(key, ("check",)):
            label = key if name == "check" else f"{key}/{name}"
            rows.append(
                make_tracker_issue(
                    f"{key}/{name}",
                    parent_key=key,
                    issue_labels=frozenset({"criterion"}),
                    body=f"**Check:** {label} live Check  bytes\n**Evidence:** —",
                )
            )
    return FakeTrackerPort(
        issues=rows,
        scope_memberships={SCOPE: tuple(lanes)},
        criteria_stage_label_key=STAGED,
        # The board reads its markers under the operation the engine writes
        # them under; a port with no prefixes could answer for no lane.
        marker_prefixes=native_operation().marker_prefixes,
        scope_label_members={
            ScopeRef(kind=ScopeKind.ISSUE, key=key): frozenset({ScopeLabel.APPROVED})
            for key in lanes
            if approved
        },
    )


class RemoteGit(FakeGitService):
    async def remote_branch_sha(self, cwd, remote, branch):
        self.calls.append(("remote_branch_sha", cwd, remote, branch))
        return ("b" if branch in {"trunk", "main"} else "a") * 40


class ObservedNativeExecutor(NativeExecutor):
    """Script the SDK's real opening/result pair for attributed node sessions."""

    def __init__(self, evaluations):
        super().__init__(evaluations)
        self.run_identities = []

    async def stream(self, **kwargs):
        self.run_identities.append(kwargs.get("run_identity"))
        async for event in super().stream(**kwargs):
            if isinstance(event, ResultEvent):
                yield SystemEvent(subtype="init", data={"session_id": event.session_id})
            yield event


@dataclass
class Harness:
    engine: object
    port: FakeTrackerPort
    executor: NativeExecutor
    service: AgentService
    artifacts: FakeArtifactPersister
    saver: InMemorySaver
    workspace: FakeWorkspaceProvider


def runtime(
    *,
    port=None,
    lanes=("A",),
    saver=None,
    evaluations=None,
    trunk="trunk",
    origin=ORIGIN,
    forge=None,
    persister=None,
    git=None,
    source=None,
    workspace=None,
    merger=None,
    max_iterations=1,
):
    """The composed engine over external doubles.

    *persister*, *git*, *source* and *workspace* default to today's
    no-commit doubles; a test about what a walk leaves on the board supplies
    repositories that actually commit, so every recorded fact comes from an
    observation of one.
    """
    port = port or board(lanes=lanes)
    executor = ObservedNativeExecutor(
        evaluations
        or [
            native_evaluation(checks={f"{key}/check": f"{key} live Check  bytes"})
            for key in lanes
            for _ in range(2)
        ]
    )
    git = git if git is not None else RemoteGit()
    # The workspace reports its identity through the same Git double the rest
    # of the fixture reads, so a prepared tree and the head it was cut at are
    # one repository's answer rather than two doubles'.
    workspace = FakeWorkspaceProvider(git=git) if workspace is None else workspace
    service = AgentService(
        git_base_url="https://github.com",
        executor=executor,
        workspace=workspace,
        persister=persister if persister is not None else FakeChangePersister(),
    )
    artifacts = FakeArtifactPersister()
    saver = saver or InMemorySaver()
    # Pair the fake filesystem/Git boundary with its immutable-source double.
    # The production builder, native owner and graph remain actual consumers.
    with pytest.MonkeyPatch.context() as external:
        external.setattr(
            "kodezart.composition.engine.SubprocessGitSourceReader",
            NativeSourceReader if source is None else (lambda: source),
        )
        engine = build_workflow_engine(
            operation=native_operation(),
            config=AppConfig(
                write_back=WriteBackSettings(max_verify_rounds=2),
                ticket_review_mode=TicketReviewMode.REVIEWED,
                max_iterations=max_iterations,
                retry_max_attempts=1,
                retry_initial_interval=0.1,
            ),
            repositories=(RepoEntry(url=origin, trunk=trunk),),
            agent_service=service,
            git=git,
            cache=FakeRepoCache(),
            workspace=workspace,
            merger=merger
            if merger is not None
            else FakeBranchMerger(
                consolidation_outcomes=[
                    ConsolidationOutcome(
                        status=ConsolidationStatus.FAST_FORWARDED,
                        feature_tip_sha="a" * 40,
                    )
                    for _ in lanes
                ],
            ),
            artifact_persister=artifacts,
            ref_publisher=FakeRefPublisher(),
            prompts=make_prompt_provider(),
            skills=SUPPRESS_ALL_SKILLS,
            gate=PassThroughGate(),
            github_api=forge,
            checkpointer=saver,
            criteria=TrackerCriteria(tracker=port),
            scope_tracker=port,
        )
    return Harness(engine, port, executor, service, artifacts, saver, workspace)


def drive(harness, *, job="scope-job", scope=SCOPE, origin=ORIGIN, path=None):
    return harness.engine.run(
        prompt="Request prose is not the native subject",
        repo_path=path,
        repo_url=origin,
        base_spec=trunk_base("unused-request-default"),
        scope=scope,
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=[],
        cache_key=job,
    )


def lane_failures(events):
    """Every lane failure the walk contained, as its last observation names them."""
    observations = [
        event.observation for event in events if isinstance(event, ScopeWalkEvent)
    ]
    return observations[-1].failed_lanes if observations else ()


async def walk_reporting(harness, *, kind, match="", **rest):
    """Drive a walk one lane of which fails, and read that failure off it.

    A lane's own refusal no longer ends the run (KOD-841): it is contained at
    the walk's lane boundary and named on the observation, so a test about
    that refusal asserts its type and message there instead of catching it.
    """
    events = [event async for event in drive(harness, **rest)]
    failures = lane_failures(events)
    assert [failure.error.error_kind for failure in failures] == [kind]
    assert match in failures[0].error.error
    return events


async def test_request_queue_constructor_reaches_real_native_graph_without_child_jobs():
    # Over repositories that actually commit (``resumable``, below): B's base
    # is A's recorded deliverable branch, so A has to leave a record and push
    # the branch it names before B can be prepared at all (KOD-842).
    harness = resumable(
        repos=WalkRepos(),
        port=board(lanes=("A", "B"), blocked={"B": ("A",)}),
        lanes=("A", "B"),
    )
    queue = build_job_queue(settings=JobQueueSettings(), workflow_engine=harness.engine)
    handler = AgentHandler(harness.service, SUPPRESS_ALL_SKILLS, queue=queue)
    await queue.start()
    try:
        record = await handler.submit_workflow(
            WorkflowRequest(
                prompt="Run approved scope",
                repo_url=ORIGIN,
                scope={"kind": "project", "key": SCOPE.key},
            ),
            lane="scope-requests",
        )
        payloads = []
        async with asyncio.timeout(15):
            async for item in handler.attach_job(job_id=record.job_id):
                payloads.append(item)
        assert not [item for item in payloads if item["type"] == "error"]
        nested = [item for item in payloads if item["type"] == "scope_lane"]
        iterations = [
            item for item in nested if item["event"]["type"] == "workflow_iteration"
        ]
        assert [item["laneKey"] for item in iterations] == ["A", "B"]
        assert (
            iterations[0]["event"]["evaluation"]["criteriaResults"][0]["criterionId"]
            == "A/check"
        )
        observations = [
            item["observation"] for item in payloads if item["type"] == "scope_walk"
        ]
        assert observations[-1]["dispatched"] == ["A", "B"]
        assert observations[-1]["unresolvedCriteria"] == []
        assert "workflow_complete" not in {item["type"] for item in payloads}
        finished = await queue.get(job_id=record.job_id)
        assert finished.state is JobState.TERMINAL
        assert finished.outcome is None
        assert list(queue._records) == [record.job_id]
        assert harness.port.claim_writes == []
        # The only bodies the walk wrote are the two Evidence rows its own
        # evaluations stamped; no child job and no claim was written at all.
        assert {key for key, _, _ in harness.port.issue_writes} == {
            "A/check",
            "B/check",
        }
        assert harness.artifacts.persist_calls == []
        assert (
            "Exact native subject A  with spaces"
            in harness.executor.execution_prompts[0]
        )
        assert "Request prose" not in harness.executor.execution_prompts[0]
    finally:
        await queue.stop()


@pytest.mark.parametrize("change", ["approval", "membership", "check"])
async def test_next_lane_uses_current_approval_membership_and_check(change):
    port = board(lanes=("A", "B"))
    harness = runtime(port=port, lanes=("A", "B"))
    events = []
    async for event in drive(harness):
        events.append(event)
        if isinstance(event, ScopeLaneEvent) and isinstance(
            event.event, WorkflowIterationEvent
        ):
            if event.lane_key != "A":
                continue
            if change == "approval":
                port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="B")] = (
                    frozenset()
                )
            elif change == "membership":
                port.scope_memberships[SCOPE] = ("A",)
            else:
                port.issues["B/check"] = port.issues["B/check"].model_copy(
                    update={
                        "body": "**Check:** amended B Check  exact bytes\n"
                        "**Evidence:** —"
                    }
                )
    launched = [
        event.lane_key
        for event in events
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, WorkflowIterationEvent)
    ]
    assert launched == (["A", "B"] if change == "check" else ["A"])
    last = [event.observation for event in events if isinstance(event, ScopeWalkEvent)][
        -1
    ]
    if change == "approval":
        assert last.unapproved_lanes == ("B",)
        assert "B/check" in last.unresolved_criteria
    if change == "check":
        assert "amended B Check  exact bytes" in harness.executor.execution_prompts[-1]
        assert "B live Check  bytes" not in harness.executor.execution_prompts[-1]


async def test_unapproved_scope_observation_cannot_equal_closed_scope():
    port = board(approved=False)
    harness = runtime(port=port)
    events = [event async for event in drive(harness)]
    assert len(events) == 1
    observation = events[0].observation
    assert observation.unapproved_lanes == ("A",)
    assert observation.unresolved_criteria == ("A/check",)
    assert observation.dispatched == ()
    assert harness.executor.schema_calls == []
    assert not any(isinstance(event, WorkflowCompleteEvent) for event in events)


async def test_existing_plan_barrier_prevents_any_lane_effect():
    port = board()
    port.issues["decision"] = make_tracker_issue(
        "decision", parent_key="A", issue_labels=frozenset({"decision"})
    )
    harness = runtime(port=port)
    with pytest.raises(ScopePlanRefusalError) as caught:
        _ = [event async for event in drive(harness)]
    assert caught.value.open_decisions == ("decision",)
    assert harness.executor.schema_calls == []


@pytest.mark.parametrize("failure", ["base", "record", "fire"])
async def test_one_lanes_failure_is_reported_and_the_walk_continues(
    monkeypatch, failure
):
    """Three ready lanes, the second one's own work raises (KOD-841).

    The other two fire, the failing lane is named on the observation right
    after its tick and on every later one, with its error's type and its own
    message, and it is never offered again. That ``drive()`` runs to its end
    at all IS the "no engine error" clause: the walk raises nothing, and a
    contained failure travels on the observation instead. A failure of the
    SCOPE's own ready read is a different fault and still ends the run, which
    ``test_tracker_outage_between_lanes_cannot_reuse_the_previous_ready_set``
    and ``test_a_failed_ready_read_in_a_lanes_turn_ends_the_run`` pin.
    """
    lanes = ("A", "B", "C")
    port = board(lanes=lanes)
    harness = runtime(
        port=port,
        lanes=lanes,
        # Only the two lanes that reach a grading have one scripted: an echo
        # left over for the failing lane would be spent on the next lane and
        # grade it against another lane's Check.
        evaluations=[
            native_evaluation(checks={f"{key}/check": f"{key} live Check  bytes"})
            for key in ("A", "C")
            for _ in range(2)
        ],
    )
    if failure == "base":
        resolver = harness.engine._scoped_arm._resolver
        resolve = resolver.resolve

        async def refuse_one(*, issue_key, **rest):
            if issue_key == "B":
                raise BaseResolutionError(
                    "the lane's base cannot be resolved", issue_id=issue_key
                )
            return await resolve(issue_key=issue_key, **rest)

        monkeypatch.setattr(resolver, "resolve", refuse_one)
    elif failure == "record":
        # The listing the lane's record is read from fails for this lane only:
        # unreadable is never treated as "no record", so nothing is minted.
        listing = port.list_comments

        async def unreadable(*, issue_key):
            if issue_key == "B":
                raise TrackerUnavailableError("the lane record listing failed")
            return await listing(issue_key=issue_key)

        monkeypatch.setattr(port, "list_comments", unreadable)
    else:
        # A criterion carrying no Check at all: the lane's own entry refuses
        # it, before any session, the way a malformed obligation does.
        port.issues["B/check"] = port.issues["B/check"].model_copy(
            update={"body": "**Evidence:** — and no Check field at all"}
        )
    # Bounded: a lane offered again after it failed would walk forever, and a
    # hang is not a failing assertion. Four ticks take milliseconds here.
    async with asyncio.timeout(30):
        events = [event async for event in drive(harness)]
    observations = [
        event.observation for event in events if isinstance(event, ScopeWalkEvent)
    ]
    iterations = [
        event.lane_key
        for event in events
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, WorkflowIterationEvent)
    ]
    assert iterations == ["A", "C"]
    # A is selected at tick 1, B at tick 2 and fails, C at tick 3, and tick 4
    # closes the walk: the failure is on the observation of the tick right
    # after it and on every later one, not only on the terminal one.
    assert [
        observation.tick for observation in observations if observation.failed_lanes
    ] == [3, 4]
    assert all(
        [item.issue_key for item in observation.failed_lanes] == ["B"]
        for observation in observations[2:]
    )
    # A lane that raised before its graph launched was never dispatched; one
    # that raised inside it was launched exactly once and not offered again.
    assert list(observations[-1].dispatched).count("B") == (
        1 if failure == "fire" else 0
    )
    assert [key for key in observations[-1].dispatched if key != "B"] == ["A", "C"]
    reported = observations[-1].failed_lanes
    # Exactly one entry: the lane was tried once and rested, not retried on
    # every remaining tick.
    assert [item.issue_key for item in reported] == ["B"]
    assert (
        reported[0].error.error_kind
        == {
            "base": "BaseResolutionError",
            "record": "LaneRecordReadError",
            "fire": "InvalidFireCriterionError",
        }[failure]
    )
    # The message the lane's own refusal was raised with, not merely some text:
    # a report carrying another lane's reason, or a fixed one, reads the same
    # against a truthiness check.
    assert {
        "base": "the lane's base cannot be resolved",
        "record": "lane record None on 'B' for 'B' could not be read: "
        "the tracker comment read failed or was incomplete",
        "fire": "criterion 'B/check' of fire subject 'B' cannot be consumed: "
        "one nonempty Check field is required",
    }[failure] in reported[0].error.error
    fired = {
        key
        for key in lanes
        if any(
            f"Exact native subject {key}" in prompt
            for prompt in harness.executor.execution_prompts
        )
    }
    assert fired == {"A", "C"}


@pytest.mark.parametrize("site", ["readmission", "launch"])
async def test_a_failed_ready_read_in_a_lanes_turn_ends_the_run(monkeypatch, site):
    """The scope's own ready read is the walk's fault wherever it lands.

    A lane's turn makes two of the walk's three ready reads: one after base
    resolution and one before the graph launches. Both are the SCOPE's read,
    so an outage at either ends the run rather than being recorded against
    the lane that happened to be in flight and resting it — and the lane it
    was about never opens a session.
    """
    harness = runtime(port=board(lanes=("A", "B")), lanes=("A", "B"))
    fresh = scope_runtime.read_scope_ready
    reads = 0

    async def failing(*, ref, tracker):
        nonlocal reads
        reads += 1
        # 1 is the tick's own read, 2 the readmission after base resolution,
        # 3 the one before the launch.
        if reads == {"readmission": 2, "launch": 3}[site]:
            raise TrackerUnavailableError("the scope ready read failed")
        return await fresh(ref=ref, tracker=tracker)

    monkeypatch.setattr(scope_runtime, "read_scope_ready", failing)
    events = []
    with pytest.raises(TrackerUnavailableError, match="the scope ready read failed"):
        async for event in drive(harness):
            events.append(event)
    assert lane_failures(events) == ()
    assert harness.executor.execution_prompts == []


async def test_tracker_outage_between_lanes_cannot_reuse_the_previous_ready_set(
    monkeypatch,
):
    port = board(lanes=("A", "B"))
    harness = runtime(port=port, lanes=("A", "B"))
    original = port.scope_issues

    async def current_scope(*, ref):
        if ref == SCOPE:
            raise TrackerUnavailableError("current scope unavailable")
        return await original(ref=ref)

    with pytest.raises(TrackerUnavailableError, match="current scope unavailable"):
        async for event in drive(harness):
            if isinstance(event, ScopeLaneEvent) and isinstance(
                event.event, WorkflowIterationEvent
            ):
                monkeypatch.setattr(port, "scope_issues", current_scope)
    assert len(harness.executor.execution_prompts) == 1
    assert "Exact native subject B" not in harness.executor.execution_prompts[0]
    assert port.claim_writes == []


async def test_approval_removed_during_preparation_prevents_any_native_node(
    monkeypatch,
):
    port = board()
    harness = runtime(port=port)
    controller = harness.engine._scoped_arm
    original = controller._cache.ensure_available

    async def ensure_available(url, cache_key=None):
        path = await original(url, cache_key)
        port.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key="A")] = frozenset()
        return path

    monkeypatch.setattr(controller._cache, "ensure_available", ensure_available)
    events = [event async for event in drive(harness)]
    assert harness.executor.schema_calls == []
    assert events[-1].observation.unapproved_lanes == ("A",)
    assert events[-1].observation.unresolved_criteria == ("A/check",)


def owed_again(port, *, lane="A", check=None):
    """Put a lane's criterion back in Todo, the way the product does.

    A lane whose every criterion its run crossed off is closed by the
    tracker's rollup, and the walker offers such a lane for its delivery
    alone. What re-opens one in production is the amendment write-back: the
    criterion returns to the unstarted state with its Evidence cleared,
    carrying the amended Check when the amendment changed the text.
    """
    key = f"{lane}/check"
    issue = port.issues[key]
    current = (
        check
        if check is not None
        else criterion_field_bodies(issue.body, field="Check")[0]
    )
    port.issues[key] = issue.model_copy(
        update={
            "state_kind": WorkflowStateKind.UNSTARTED,
            "state_name": "Todo",
            "body": f"**Check:** {current}\n**Evidence:** —",
        }
    )


def lane_of(harness):
    return harness.engine._scoped_arm._lane_for(ORIGIN)


async def test_current_closed_blocker_unlocks_next_lane_using_its_recorded_branch():
    """B stands on the deliverable branch A's own record names (KOD-842).

    Nothing seeds a ref anywhere on this board: A's record is written by A's
    own commit, so the branch B was prepared with can only have been read
    from it. The record stands on A's LOOP branch, which is asserted to be
    what B was NOT based on — a reader answering with the record's branch
    would otherwise resolve a base and look right.
    """
    repos = WalkRepos()
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    harness = resumable(repos=repos, port=port, lanes=("A", "B"))
    events = []
    async for event in drive(harness):
        events.append(event)
        if isinstance(event, ScopeLaneEvent) and isinstance(
            event.event, LaneDeliveryEvent
        ):
            if event.lane_key == "A":
                # A's criterion is already Done: its own evaluation step
                # crossed it off, so nothing here has to close the blocker.
                assert port.issues["A/check"].state_kind is WorkflowStateKind.COMPLETED
    iterations = [
        event.lane_key
        for event in events
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, WorkflowIterationEvent)
    ]
    assert iterations == ["A", "B"]
    assert port.issues["A"].state_kind is WorkflowStateKind.UNSTARTED
    final = events[-1].observation
    assert final.unresolved_criteria == ()
    # Exactly the two criterion sub-issues moved, and neither subject did.
    assert port.workflow_writes == [
        ("A/check", LifecycleStage.DONE),
        ("B/check", LifecycleStage.DONE),
    ]
    # The base B was actually prepared with, read off the run's own base event
    # rather than a checkpoint's request metadata (KOD-776, KOD-806: the scope
    # path persists no graph state).
    bases = {
        event.lane_key: event.event.base_branch
        for event in events
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, WorkflowScopeBaseEvent)
    }
    record = await lane_record(port, "A")
    assert bases["B"] == recorded_branches(record=record).deliverable_branch
    assert bases["B"] != record.branch
    # And no work ref was ever recorded on either lane: the record is the
    # whole carrier of a blocker's branch on this path.
    assert await port.work_refs(issue_key="A") == ()


def finish_by_hand(port, key: str) -> None:
    """Lane *key* as a board holds it when somebody finished it elsewhere.

    Its criterion is Done and its own issue is closed, and nothing anywhere
    records a branch for it: the lane never ran here, so base resolution can
    only assume its work reached the trunk.
    """
    for issue_key in (key, f"{key}/check"):
        port.issues[issue_key] = port.issues[issue_key].model_copy(
            update={"state_name": "Done", "state_kind": WorkflowStateKind.COMPLETED}
        )


class UnreadableDelivery(FakeDeliveryProbe):
    """A forge that cannot answer the delivery question about one issue.

    It answers every other issue as usual, because the question about the
    candidate itself is asked outside the lane's boundary: a double refusing
    both would end the walk instead of reporting the lane.
    """

    def __init__(self, *, refuses: str) -> None:
        super().__init__()
        self.refuses = refuses

    async def open_delivery_exists(self, *, repo_url: str, issue_key: str) -> bool:
        if issue_key == self.refuses:
            self.calls.append(issue_key)
            raise ForgeAPIError(
                "the delivery listing failed",
                status_code=500,
                detail=f"GET /pulls for {issue_key}",
            )
        return await super().open_delivery_exists(
            repo_url=repo_url, issue_key=issue_key
        )


@pytest.mark.parametrize("answer", ["false", "true", "unreadable"])
async def test_a_closed_blocker_with_no_record_is_gated_by_one_open_delivery_read(
    answer,
):
    """A closed blocker recording nothing is assumed landed — after one read.

    The assumption base resolution makes for such a blocker is wrong in one
    observable case: the blocker's work is sitting in a delivery nobody merged
    (KOD-721, KOD-777). The walker asks the forge once, before it resolves, and
    all three answers are the lane's: no open delivery states the assumption in
    the log and the lane stands on the trunk; an open one refuses the lane; a
    forge that cannot answer refuses it with the forge's own error.
    """
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    finish_by_hand(port, "A")
    harness = runtime(
        port=port,
        lanes=("A", "B"),
        evaluations=[
            native_evaluation(checks={"B/check": "B live Check  bytes"})
            for _ in range(2)
        ],
    )
    probe = (
        UnreadableDelivery(refuses="A")
        if answer == "unreadable"
        else FakeDeliveryProbe(delivered=("A",) if answer == "true" else ())
    )
    harness.engine._scoped_arm._probe_for = lambda _: probe

    with structlog.testing.capture_logs() as logs:
        events = [event async for event in drive(harness)]

    # The blocker was asked about exactly once, whatever the answer was: the
    # read is made per blocker per turn and the lane is not offered again.
    assert probe.calls.count("A") == 1
    # The carve-out is one OPEN-delivery read and nothing else. The probe the
    # walk is handed answers merge state as readily as the native client does,
    # so an empty list is a fact about the walker and not about an unreachable
    # double (KOD-721: no code path derives landedness from the forge).
    assert probe.merge_state.calls == []
    fired = [
        event.lane_key
        for event in events
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, WorkflowIterationEvent)
    ]
    assumed = [
        event
        for event in logs
        if event.get("event") == "base_input_no_open_delivery"
        and event.get("blocker") == "A"
    ]
    if answer == "false":
        assert [failure.error.error_kind for failure in lane_failures(events)] == []
        assert fired == ["B"]
        bases = {
            event.lane_key: event.event.base_branch
            for event in events
            if isinstance(event, ScopeLaneEvent)
            and isinstance(event.event, WorkflowScopeBaseEvent)
        }
        assert bases["B"] == "trunk"
        # Stated by name, once, and never silently.
        assert [event["lane"] for event in assumed] == ["B"]
        return
    assert fired == []
    assert assumed == []
    failures = lane_failures(events)
    assert [failure.issue_key for failure in failures] == ["B"]
    assert [failure.error.error_kind for failure in failures] == [
        {"true": "BaseResolutionError", "unreadable": "ForgeAPIError"}[answer]
    ]
    assert {
        "true": "an unrecorded open delivery exists for the blocker",
        "unreadable": "the delivery listing failed",
    }[answer] in failures[0].error.error
    if answer == "true":
        # A reader of the walk sees which blocker refused the lane. The failure
        # carries str(exc) alone, so the message is the only place it can.
        assert "A" in failures[0].error.error


async def test_actual_http_sse_preserves_nested_progress_and_delivery_discriminators():
    harness = runtime()
    async for app in _build_app(harness.engine, checkpointer=harness.saver):
        fired = await app.client.post(
            "/api/v1/agent/fire",
            json={
                "prompt": "scope request",
                "repoUrl": ORIGIN,
                "scope": SCOPE.model_dump(mode="json"),
            },
        )
        assert fired.status_code == 202
        job_id = fired.json()["jobId"]
        stream = await app.client.get(f"/api/v1/jobs/{job_id}/stream")
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(line[6:])
            for line in stream.text.splitlines()
            if line.startswith("data: ")
        ]
        assert not [event for event in events if event["type"] == "error"]
        lane_events = [event for event in events if event["type"] == "scope_lane"]
        assert lane_events and all(event["laneKey"] == "A" for event in lane_events)
        assert all(
            ScopeLaneEvent.model_validate_json(json.dumps(event)).lane_key == "A"
            for event in lane_events
        )
        iteration = next(
            event["event"]
            for event in lane_events
            if event["event"]["type"] == "workflow_iteration"
        )
        assert iteration["evaluation"]["criteriaResults"][0]["criterionId"] == "A/check"
        session = next(
            event["event"]
            for event in lane_events
            if event["event"]["type"] == "node_session_started"
        )
        assert session["invocation"]["run"]["name"] == "A"
        assert session["invocation"]["run"]["started_at"]
        delivery = next(
            event["event"]
            for event in lane_events
            if event["event"]["type"] == "lane_delivery"
        )
        assert delivery["delivery"]["phase"] == "skipped"
        observation = events[-1]["observation"]
        assert observation["skippedLanes"] == ["A"]
        assert observation["unresolvedCriteria"] == []
        assert harness.port.issues["A/check"].state_kind is WorkflowStateKind.COMPLETED
        assert all(event["type"] != "workflow_complete" for event in events)
        status = (await app.client.get(f"/api/v1/jobs/{job_id}")).json()
        assert status["state"] == "terminal" and status["outcome"] is None


async def test_actual_scope_composition_retains_completed_native_delivery_record():
    """A completed delivery is retained where a lane's state lives: its record.

    The scope path persists no graph state (KOD-840), so what the lane graph
    held in process is gone with the process. The record is read back through
    the production reader afterwards and carries the pull request the delivery
    opened — url, number and state — written by the delivering step onto the
    one comment the commit before it left (KOD-843).
    """
    from kodezart.types.domain.native_delivery import CompletedLaneDelivery

    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A",))
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        harness = resumable(
            port=port, repos=repos, origin=FORGE_ORIGIN, forge=forge, trunk="main"
        )
        events = [event async for event in drive(harness, origin=FORGE_ORIGIN)]
        deliveries = [
            event.event.delivery
            for event in events
            if isinstance(event, ScopeLaneEvent)
            and isinstance(event.event, LaneDeliveryEvent)
        ]
        assert len(deliveries) == len(wire.creates) == 1
        phase = deliveries[0]
        assert isinstance(phase, CompletedLaneDelivery)
        assert phase.result.issue_id == phase.result.lane_key == "A"
        assert phase.result.base_branch == "main"
        assert phase.result.checks_passed is True
        assert not phase.result.remediation_pending
        addressed = ScopeLaneEvent(
            lane_key="A", event=LaneDeliveryEvent(delivery=phase)
        )
        assert (
            ScopeLaneEvent.model_validate_json(addressed.model_dump_json()) == addressed
        )
        record = await lane_record(port, "A")
        assert record.pr is not None
        assert (record.pr.url, record.pr.number, record.pr.state) == (
            phase.result.pr.url,
            phase.result.pr.number,
            phase.result.pr.state,
        )
        # The head that pull request was opened on is the lane's own deliverable
        # at the sha this walk's repositories hold, not a value spelled here.
        assert wire.creates[0]["head"] == phase.result.head_branch
        assert phase.result.final_commit_sha == repos.head_of(phase.result.head_branch)
        assert record.head_sha == repos.head_of(record.branch)
        assert events[-1].observation.unresolved_criteria == ()
        assert harness.port.workflow_writes == [("A/check", LifecycleStage.DONE)]
        # The sha the lane's own loop branch stands at, as the repositories
        # answer it, rather than a value spelled here.
        assert parse_criterion_evidence(
            harness.port.issues["A/check"].body
        ).graded_sha == repos.head_of(record.branch)
    finally:
        await forge.close()


# ---------------------------------------------------------------------------
# KOD-832 clause 4 — a scoped walk leaves every criterion finished with the
# head sha and a record.
# ---------------------------------------------------------------------------


class WalkRepos:
    """One repository per lane branch, each numbering its own shas.

    Every repository is seeded where no other one's shas reach, so a lane's
    head is that lane's: unseeded, the first commit of each branch would be
    the same value and no assertion could tell one lane's tree from
    another's. A walk dispatches its lanes one at a time and the workspace
    provider hands each of them the same path, so the tree an unaddressed
    read belongs to is the lane that last committed and not the path. Push
    status is still answered per branch: a double that answered every branch
    alike would report one lane's push from a branch nobody pushed.
    """

    #: How far apart two repositories' sha spaces are set.
    SPACING = 0x1000

    def __init__(self, *, remote: str = "origin", url: str | None = None) -> None:
        self.remote = remote
        #: The same remote, under the other name callers have for it: the lane
        #: state writer knows the configured remote NAME and a delivery knows
        #: the repository URL. Two names for one remote, so a read through
        #: either is a read of these repositories — and a read through a third
        #: name is still a read of a remote this walk knows nothing about.
        self.remote_names = frozenset(
            {remote}
            | ({url, resolve_repo_url(url, "https://github.com")} if url else set())
        )
        self.branches: dict[str, LaneRepo] = {}
        #: The refs a delivery published, which a later lane resolves a base
        #: from: they carry no commits of this walk and hold one sha each.
        self.delivered: dict[str, str] = {}
        # Before the first commit of the walk the trees are the trunk's, which
        # is what the repository this walk was cut from holds.
        self.committing = LaneRepo(branch="trunk", remote=remote)

    def head_of(self, branch: str) -> str | None:
        """The sha this walk holds for *branch*, without creating a repository.

        ``of`` makes one and makes it the tree later unaddressed reads answer
        from, so a forge double asking what a branch stands at would change
        what the walk is standing on.
        """
        repo = self.branches.get(branch)
        if repo is not None:
            return repo.head
        return self.delivered.get(branch)

    def of(self, branch: str) -> LaneRepo:
        repo = self.branches.setdefault(
            branch,
            LaneRepo(
                branch=branch,
                remote=self.remote,
                seed=(len(self.branches) + 1) * self.SPACING,
            ),
        )
        self.committing = repo
        return repo

    @property
    def current(self) -> LaneRepo:
        return self.committing


class WalkGit(FakeGitService):
    """The git reads of a whole walk, answered from its repositories."""

    def __init__(self, repos: WalkRepos) -> None:
        super().__init__()
        self.repos = repos

    async def current_sha(self, cwd: str) -> str:
        self.calls.append(("current_sha", cwd))
        return self.repos.current.head

    async def remote_branch_sha(self, cwd, remote, branch):
        self.calls.append(("remote_branch_sha", cwd, remote, branch))
        if branch in TRUNK_BRANCHES:
            return TRUNK_SHA
        if remote not in self.repos.remote_names:
            return None
        repo = self.repos.branches.get(branch)
        if repo is not None:
            return repo.pushed
        return self.repos.delivered.get(branch)

    async def is_ancestor(self, cwd, ancestor_ref, descendant_ref):
        self.calls.append(("is_ancestor", cwd, ancestor_ref, descendant_ref))
        shas = [TRUNK_SHA, *self.repos.current.shas]
        return (
            ancestor_ref in shas
            and descendant_ref in shas
            and shas.index(ancestor_ref) <= shas.index(descendant_ref)
        )

    async def diff_summary(self, cwd, base_ref, head_ref):
        self.calls.append(("diff_summary", cwd, base_ref, head_ref))
        repo = self.repos.current
        made = repo.shas.index(head_ref) + 1 if head_ref in repo.shas else 0
        return ChangesetDigest(
            file_paths=[f"lane-{index}.py" for index in range(made)],
            commit_subjects=[f"feat: commit {index + 1}" for index in range(made)],
            commit_count=made,
        )


class WalkSource(NativeSourceReader):
    """Resolves each lane's refs against the repository that holds them.

    A ref no repository of this walk holds is a read this double cannot
    answer, and it refuses: answered with the current head instead, a
    grading of some ref nobody wrote would read as a grading of the lane's
    own branch.
    """

    def __init__(self, repos: WalkRepos) -> None:
        self.repos = repos

    async def resolve_commit(self, *, cwd, ref):
        if ref in TRUNK_BRANCHES or ref == TRUNK_SHA:
            return TRUNK_SHA
        if ref in self.repos.branches:
            return self.repos.branches[ref].head
        if ref in self.repos.delivered:
            return self.repos.delivered[ref]
        if ref in set(self.repos.delivered.values()) or any(
            ref in repo.shas for repo in self.repos.branches.values()
        ):
            return ref
        raise GitSourceReadError(
            ref=ref, path=None, reason="no repository of this walk holds the ref"
        )


class WalkWorkspaces(FakeWorkspaceProvider):
    """Cuts each lane's tree from the branch that lane commits on.

    A lane's repository exists from the moment its tree is acquired, not
    from its first commit: the guard reads the tree's starting HEAD before
    anything is committed in it, and a walk whose repositories appeared only
    at the commit would answer that read from the previous lane's branch.
    """

    def __init__(self, repos: WalkRepos, *, git: WalkGit) -> None:
        super().__init__(git=git)
        self.repos = repos

    async def acquire(self, **arguments):
        branch = arguments.get("branch_name")
        if branch is not None:
            self.repos.of(branch)
        return await super().acquire(**arguments)


class WalkMerger(FakeBranchMerger):
    """Consolidates a lane's loop branch onto its deliverable, in the repositories.

    The deliverable branch then stands on the remote at the sha the loop branch
    reached, which is the fact a delivery checks before it opens anything. A
    double that answered a tip nobody published would let a delivery proceed
    from a head no repository holds.
    """

    def __init__(self, repos: WalkRepos) -> None:
        super().__init__()
        self.repos = repos

    async def consolidate(
        self,
        *,
        repo_path,
        repo_url,
        base_branch,
        feature_branch,
        source_branch,
        cache_key=None,
    ):
        committing = self.repos.current
        source = self.repos.of(source_branch)
        feature = self.repos.of(feature_branch)
        integrated = feature.head == source.head
        feature.head = source.head
        feature.shas = list(source.shas)
        feature.publish()
        # Consolidation moves a branch; it does not make the lane's tree the
        # one later reads answer from.
        self.repos.committing = committing
        self.calls.append(
            {
                "method": "consolidate",
                "base_branch": base_branch,
                "feature_branch": feature_branch,
                "source_branch": source_branch,
            }
        )
        return ConsolidationOutcome(
            status=ConsolidationStatus.ALREADY_INTEGRATED
            if integrated
            else ConsolidationStatus.FAST_FORWARDED,
            feature_tip_sha=source.head,
        )


class WalkPersister(FakeChangePersister):
    """Commits and pushes the branch each lane of the walk is on."""

    def __init__(self, repos: WalkRepos) -> None:
        super().__init__()
        self.repos = repos

    async def persist(
        self, *, workspace_path, branch, before_commit=None, before_publish=None, **rest
    ):
        if before_commit is not None:
            await before_commit()
        self.calls.append({"workspace_path": workspace_path, "branch": branch})
        repo = self.repos.of(branch)
        sha = repo.commit()
        if before_publish is not None:
            await before_publish(sha)
        repo.publish()
        return PersistResult(
            commit_sha=sha,
            branch=branch,
            message=f"feat: {branch} commit {len(repo.shas)}\n\nthe body of it",
            source=PersistSource.WORKING_TREE_COMMIT,
        )


async def lane_record(port, key: str):
    """The record this walk left on one lane's issue, read back fresh."""
    _, record = await LaneRecordReader(tracker=port, operation=native_operation()).read(
        issue_key=key, lane_key=key
    )
    return record


def deliverable_of(repos: WalkRepos, key: str) -> WorkRef:
    """The ref a delivery records, published on the remote as a delivery does."""
    branch, sha = f"recorded-{key}", "a" * 40
    repos.delivered[branch] = sha
    return WorkRef(
        issue_id=key,
        role=WorkRefRole.DELIVERABLE,
        branch=branch,
        pushed_head_sha=sha,
        recorded_at=FIXTURE_EPOCH,
    )


async def test_a_scoped_walk_finishes_every_criterion_at_its_head_with_a_record():
    """The whole walk, in process: two lanes, one blocked on the other.

    Read off the board as the walk runs and again at the end. Each lane's
    criterion is finished with the sha its own branch head carries, and each
    lane's issue holds a record naming that head and the push of it, written
    by the commit that made it. Neither subject is written by anything, the
    walk's last observation owes nothing, and each lane posted its first push
    exactly once.
    """
    repos = WalkRepos()
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    git = WalkGit(repos)
    source = WalkSource(repos)
    harness = runtime(
        port=port,
        lanes=("A", "B"),
        persister=WalkPersister(repos),
        git=git,
        source=source,
        workspace=WalkWorkspaces(repos, git=git),
    )
    mid_walk: dict[str, tuple[str, str, str, str | None, str]] = {}

    events = []
    async for event in drive(harness):
        events.append(event)
        if not isinstance(event, ScopeLaneEvent):
            continue
        if isinstance(event.event, WorkflowIterationEvent):
            lane = event.lane_key
            if lane not in mid_walk:
                record = await lane_record(port, lane)
                mid_walk[lane] = (
                    port.issues[f"{lane}/check"].state_name,
                    parse_criterion_evidence(
                        port.issues[f"{lane}/check"].body
                    ).graded_sha,
                    record.head_sha,
                    record.pushed_head_sha,
                    # The repository's own head at this instant: read here
                    # rather than at the end, the comparison is to the tree
                    # this lane stood at when the cross-off was written.
                    repos.branches[record.branch].head,
                )
        if isinstance(event.event, LaneDeliveryEvent):
            # A delivery leaves the lane's deliverable branch on the remote,
            # which is the ref a dependent lane's base resolves to. The branch
            # is read off the record, because the record is where a lane's
            # deliverable branch is written (KOD-842).
            delivered = recorded_branches(
                record=await lane_record(port, event.lane_key)
            )
            repos.delivered[delivered.deliverable_branch] = repos.branches[
                delivered.loop_branch
            ].head

    heads = {branch: repo.head for branch, repo in repos.branches.items()}
    assert len(heads) == 2
    # Two repositories, two sha spaces: each lane's own head is a value no
    # other lane's tree ever stood at, so "its own branch head" is a
    # comparison and not a coincidence.
    assert len({*heads.values()}) == 2
    assert set(mid_walk) == {"A", "B"}

    for lane in ("A", "B"):
        criterion = port.issues[f"{lane}/check"]
        record = await lane_record(port, lane)
        own_head = heads[record.branch]
        state_name, graded_sha, head, pushed, head_then = mid_walk[lane]
        assert state_name == LifecycleStage.DONE.value
        assert graded_sha == head == pushed == head_then
        assert criterion.state_kind is WorkflowStateKind.COMPLETED
        assert parse_criterion_evidence(criterion.body).graded_sha == own_head
        assert record.head_sha == record.pushed_head_sha == own_head
        assert [
            event.kind
            for event in await port.lane_run_events(issue_key=lane, lane_key=lane)
        ] == [RunEventKind.FIRST_PUSH]
        assert port.issues[lane].state_kind is WorkflowStateKind.UNSTARTED

    assert port.workflow_writes == [
        ("A/check", LifecycleStage.DONE),
        ("B/check", LifecycleStage.DONE),
    ]
    # One record and one first-push event per lane, and nothing else.
    assert len(port.comments) == 4
    # Push status is per branch: a branch this walk never pushed is reported
    # as unpushed, so no lane's push is ever read off another lane's branch.
    assert await git.remote_branch_sha("/w", repos.remote, "never-pushed") is None
    # And a ref no repository of this walk holds is a read the source refuses:
    # answered with the current head, a grading of a ref nobody wrote would
    # read as a grading of the lane's own branch.
    with pytest.raises(GitSourceReadError):
        await source.resolve_commit(cwd="/w", ref="no-such-ref")
    observations = [
        event.observation for event in events if isinstance(event, ScopeWalkEvent)
    ]
    assert observations[-1].unresolved_criteria == ()
    assert observations[-1].dispatched == ("A", "B")


#: Two more criteria under lane A, so one iteration can pass some of its
#: roster and fail the rest and the loop has somewhere left to go.
FURTHER_CHECKS = ("A/second", "A/third")


def lane_with_three_criteria() -> FakeTrackerPort:
    """Lane A's board, widened by two more criterion sub-issues."""
    port = board(lanes=("A",))
    for key in FURTHER_CHECKS:
        port.issues[key] = make_tracker_issue(
            key,
            parent_key="A",
            issue_labels=frozenset({"criterion"}),
            body=f"**Check:** {key} live Check  bytes\n**Evidence:** —",
        )
    return port


async def test_a_walk_that_breaks_what_it_finished_takes_it_back_once():
    """The regression, in process, over the whole walk.

    Iteration 1 finishes one criterion. Iteration 2 breaks it and finishes
    the other two: read at that iteration's event, the sub-issue is back out
    of its finished state, its Evidence row carries the sha that grading was
    read at, and the lane's stream holds exactly one refutation naming that
    sha. Iteration 3 finishes it again at the final head, and the stream
    still holds that one refutation: a criterion this lane broke and then
    fixed is one regression, recorded once.
    """
    broken, *rest = keys = ("A/check", *FURTHER_CHECKS)
    repos = WalkRepos()
    port = lane_with_three_criteria()
    git = WalkGit(repos)
    harness = runtime(
        port=port,
        lanes=("A",),
        evaluations=[
            criteria_echo(keys=keys, passed={broken}),
            criteria_echo(keys=keys, passed=set(rest)),
            criteria_echo(keys=keys, passed=set(keys)),
            criteria_echo(keys=keys, passed=set(keys)),
        ],
        persister=WalkPersister(repos),
        git=git,
        source=WalkSource(repos),
        workspace=WalkWorkspaces(repos, git=git),
        max_iterations=3,
    )
    at_iteration: dict[int, tuple[str, str, list[str | None], str]] = {}

    async for event in drive(harness):
        if not isinstance(event, ScopeLaneEvent):
            continue
        if isinstance(event.event, WorkflowIterationEvent):
            issue = port.issues[broken]
            at_iteration[event.event.iteration] = (
                issue.state_name,
                parse_criterion_evidence(issue.body).graded_sha,
                [
                    posted.graded_sha
                    for posted in await port.lane_run_events(
                        issue_key="A", lane_key="A"
                    )
                    if posted.kind is RunEventKind.CRITERION_REFUTED
                ],
                repos.branches[event.event.branch].head,
            )
        if isinstance(event.event, LaneDeliveryEvent):
            port.recorded_work_refs["A"] = [deliverable_of(repos, "A")]

    heads = [repo.head for repo in repos.branches.values()]
    assert len(heads) == 1
    # The refuting sha is the branch's own head at that iteration, captured
    # from the repository rather than read back off the row it was written on.
    refuted_at = at_iteration[2][3]
    assert at_iteration[2] == ("Todo", refuted_at, [refuted_at], refuted_at)
    assert at_iteration[1][0] == LifecycleStage.DONE.value
    assert refuted_at != at_iteration[1][1]

    final = port.issues[broken]
    assert final.state_kind is WorkflowStateKind.COMPLETED
    assert parse_criterion_evidence(final.body).graded_sha == heads[0]
    assert [
        posted.graded_sha
        for posted in await port.lane_run_events(issue_key="A", lane_key="A")
        if posted.kind is RunEventKind.CRITERION_REFUTED
    ] == [refuted_at]
    assert all(
        port.issues[key].state_kind is WorkflowStateKind.COMPLETED for key in keys
    )
    # Nothing wrote the issue that owns them, and the state moves are exactly
    # the four a tick makes: one per criterion, plus the criterion this walk
    # broke and finished again. The move back out of the finished state is not
    # among them — the double ledgers it in neither write log — so the row's
    # own state and the stream above are what carry it.
    assert port.issues["A"].state_kind is WorkflowStateKind.UNSTARTED
    assert port.workflow_writes == [
        (broken, LifecycleStage.DONE),
        *((key, LifecycleStage.DONE) for key in rest),
        (broken, LifecycleStage.DONE),
    ]


# ---------------------------------------------------------------------------
# KOD-684 — re-entry driven by the record alone: the recorded branch is
# checked out and nothing is minted.
# ---------------------------------------------------------------------------

#: Lane A with a second criterion, so one fire can finish part of its roster
#: and the lane is still owed when the next process enters it.
TWO_CHECKS = {"A": ("check", "second")}
A_KEYS = ("A/check", "A/second")
C_KEYS = ("C/check", "C/second")


def echoes(*, passed, rounds: int = 6):
    """Enough evaluator echoes for whatever the fire does, each passing *passed*.

    A fire that fails part of its roster may take a remediation round, and the
    number of gradings that follows is the graph's business and not this
    fixture's; every grading of one run answers the same way.
    """
    return [criteria_echo(keys=A_KEYS, passed=passed) for _ in range(rounds)]


def resumable(*, repos: WalkRepos, **rest):
    """A runtime over one repository family that commits as a real lane does.

    Two runtimes built over the SAME family are two processes against one
    remote: the branches and their pushed heads outlive the first one, which
    is the whole premise of entering from the record.
    """
    git = WalkGit(repos)
    return runtime(
        persister=WalkPersister(repos),
        git=git,
        source=WalkSource(repos),
        workspace=WalkWorkspaces(repos, git=git),
        merger=WalkMerger(repos),
        **rest,
    )


async def first_fire(port, repos, *, passed=("A/check",)):
    """Run one: a fire that finishes part of lane A's roster and leaves a record."""
    harness = resumable(port=port, repos=repos, evaluations=echoes(passed=set(passed)))
    _ = [event async for event in drive(harness, job="first-job")]
    return harness, await lane_record(port, "A")


def mint_spy(monkeypatch) -> list[str]:
    """Record every branch-name mint the walker path makes, and mint as usual.

    Patched at the definition AND at the alias the fire holds: a mint through
    either name is a mint, and a spy on one name only answers "uncalled" for a
    mint made through the other. It records rather than raises, so what the
    test states is the empty list beside a walk that otherwise ran — a raising
    spy would be contained at the lane boundary and read as some other
    failure.
    """
    calls: list[str] = []

    def recording(issue_key):
        calls.append(issue_key)
        return mint_lane_branches(issue_key)

    for name in (
        "kodezart.domain.agent.mint_lane_branches",
        "kodezart.chains.ralph_workflow.mint_lane_branches",
    ):
        monkeypatch.setattr(name, recording)
    return calls


async def test_a_recorded_lane_resumes_on_its_recorded_branch_and_mints_nothing(
    monkeypatch,
):
    """A second process enters lane A from its record and nothing else.

    Run one finishes one of A's two criteria and records its commit. Run two
    shares only the board and the remote: it checks the recorded branch out
    without cutting it, mints no name, adds its own run's association pair and
    one more commit row to the same record, and owes only the criterion run
    one did not finish.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    _, before = await first_fire(port, repos)
    assert port.issues["A/check"].state_kind is WorkflowStateKind.COMPLETED
    assert port.issues["A/second"].state_kind is WorkflowStateKind.UNSTARTED
    deliverable = recorded_branches(record=before).deliverable_branch

    minted = mint_spy(monkeypatch)
    second = resumable(port=port, repos=repos, evaluations=echoes(passed=set(A_KEYS)))
    events = [event async for event in drive(second, job="second-job")]

    # Nothing was minted on any path, and the lane did not fail on the way to
    # not minting: an empty spy beside a reported failure would say nothing.
    assert minted == []
    assert lane_failures(events) == ()
    assert not any("slug" in props for props in second.executor.schema_calls)
    opened = second.workspace.acquisitions[0]
    assert opened["branch_name"] == opened["ref"] == before.branch
    assert opened["create_branch"] is False
    after = await lane_record(port, "A")
    assert after.branch == before.branch
    # Exactly one more association pair, this run's, and nothing else: every
    # branch run one recorded is still named, under run one's id.
    assert {(item.branch, item.role, item.run_id) for item in after.associations} == {
        *((item.branch, item.role, item.run_id) for item in before.associations),
        (before.branch, BranchRole.LOOP, "second-job"),
        (deliverable, BranchRole.DELIVERABLE, "second-job"),
    }
    assert len(after.commits) == len(before.commits) + 1
    assert after.head_sha == repos.branches[before.branch].head
    prompt = second.executor.execution_prompts[0]
    assert "A/second live Check  bytes" in prompt
    assert "A live Check  bytes" not in prompt
    assert port.issues["A/second"].state_kind is WorkflowStateKind.COMPLETED


async def test_a_changed_check_on_re_entry_is_owed_and_graded_again():
    """Nothing of a previous grading is replayed on re-entry.

    A criterion the first fire finished is put back the way the product puts
    one back, carrying an amended Check. The second process reads the Todo set
    at entry, so that criterion is owed again and the text it is graded
    against is the amended one.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    await first_fire(port, repos)
    owed_again(port, check="materially changed Check")

    second = resumable(port=port, repos=repos, evaluations=echoes(passed=set(A_KEYS)))
    _ = [event async for event in drive(second, job="second-job")]

    prompt = second.executor.execution_prompts[0]
    assert "materially changed Check" in prompt
    assert "A live Check  bytes" not in prompt
    assert "A/second live Check  bytes" in prompt
    assert all(
        port.issues[key].state_kind is WorkflowStateKind.COMPLETED for key in A_KEYS
    )


@pytest.mark.parametrize("damage", ["base", "branch"])
async def test_a_recorded_lane_the_facts_no_longer_admit_is_reported_not_fired(
    monkeypatch, damage
):
    """Neither a moved base nor a vanished branch is answered by minting.

    A branch cut from a base the lane would no longer be diffed against, and a
    recorded branch the remote no longer holds, are both refusals of this one
    lane: nothing is minted in their place, no session opens, and the record
    is left exactly as the first fire wrote it.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    _, before = await first_fire(port, repos)
    minted = mint_spy(monkeypatch)
    if damage == "branch":
        repos.branches[before.branch].pushed = None

    second = resumable(
        port=port,
        repos=repos,
        evaluations=echoes(passed=set(A_KEYS)),
        trunk="other-trunk" if damage == "base" else "trunk",
    )
    await walk_reporting(
        second,
        kind="LaneEntryError",
        match=("is not the base" if damage == "base" else "absent from the remote"),
    )
    assert minted == []
    assert second.executor.schema_calls == []
    assert await lane_record(port, "A") == before


async def test_the_scoped_arm_holds_no_checkpointer_while_the_authored_arm_keeps_it():
    """Every graph of BOTH scoped lanes, none compiled with a saver.

    The scoped arm holds two lanes, one per origin shape, and each has its own
    fire engine: a test that inspected only the origin this harness runs on
    would say nothing about the other, and a forge-origin run would persist
    lane state unnoticed. A saver IS configured for this deployment, and the
    authored arm still holds it, so the absence is a composition choice and
    not an unconfigured deployment (KOD-840). A whole scoped run then writes
    nothing to it.
    """
    saver = InMemorySaver()
    harness = runtime(saver=saver)

    for url in (ORIGIN, FORGE_ORIGIN):
        lane = harness.engine._scoped_arm._lane_for(url)
        assert lane.graph.checkpointer is None
        assert lane.fire.native_graph is not None
        assert lane.fire.native_graph.checkpointer is None
        assert lane.fire.checkpointer is None
        assert lane.fire.implementation._quality_gate._checkpointer is None
    # The two are different engines, so the loop above asserted twice about
    # two things and not twice about one.
    assert harness.engine._scoped_arm._lane_for(ORIGIN) is not (
        harness.engine._scoped_arm._lane_for(FORGE_ORIGIN)
    )
    # Not vacuous: the arm a run without a scope takes still holds it.
    authored = harness.engine.arm_for(None).fire
    assert authored.graph.checkpointer is saver
    assert authored.implementation._quality_gate._checkpointer is saver

    events = [event async for event in drive(harness)]

    # The run really ran, so the empty saver below is a statement about it.
    assert events[-1].observation.dispatched == ("A",)
    assert [checkpoint async for checkpoint in saver.alist(None)] == []


# ---------------------------------------------------------------------------
# KOD-433 — the subject text is read once at entry and compared with the
# digest the record pinned.
# ---------------------------------------------------------------------------


def subject_reads(port, monkeypatch) -> list[str]:
    """Every read of a subject's own text the board answers, in order.

    The criterion says the text is read ONCE at entry, and a re-read returns
    the same bytes in every fixture here: only the count can tell the two
    apart.
    """
    reads: list[str] = []
    answering = port.read_fire_spec

    async def counted(*, issue_key):
        reads.append(issue_key)
        return await answering(issue_key=issue_key)

    monkeypatch.setattr(port, "read_fire_spec", counted)
    return reads


async def test_the_digest_is_pinned_at_the_first_record_write(monkeypatch):
    """What the record keeps about the subject is the sha256 of the text read.

    Computed here rather than through the production function: an expectation
    taken from the code under test is a tautology on the algorithm, and the
    record's own field would accept a digest of any other one.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    reads = subject_reads(port, monkeypatch)
    minted = mint_spy(monkeypatch)
    _, record = await first_fire(port, repos)

    assert (
        record.body_digest
        == hashlib.sha256(port.issues["A"].body.encode("utf-8")).hexdigest()
    )
    assert reads == ["A"]
    # The spy every "mints nothing" assertion rests on sees the one mint a
    # first fire makes: a spy that saw nothing here would answer "uncalled"
    # for every resumed lane too.
    assert minted == ["A"]


def unpin_digest(port, key: str) -> None:
    """Rewrite one lane's record the way a record written before the pin reads.

    The field is dropped from the stored JSON rather than set to null: every
    record a board carried before the pin existed has no such field at all, and
    that is the record a resumed lane meets first.
    """
    prefix = native_operation().marker_prefixes["run_state"]
    for index, comment in enumerate(port.comments):
        if comment.body.startswith(f"[{prefix}:{key}]"):
            port.comments[index] = comment.model_copy(
                update={
                    "body": "\n".join(
                        line
                        for line in comment.body.splitlines()
                        if '"bodyDigest"' not in line
                    )
                }
            )


async def test_a_record_with_no_digest_is_entered_and_pinned_by_its_next_write(
    monkeypatch,
):
    """A lane whose record predates the pin re-enters and leaves one behind.

    Every record written before the digest existed carries none, so this is the
    path production takes first: the entry has nothing to compare, the lane
    resumes on its recorded branch without minting, reads its subject once, and
    the commit that follows pins the digest of the text it read.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    _, before = await first_fire(port, repos)
    assert before.body_digest is not None
    unpin_digest(port, "A")
    assert (await lane_record(port, "A")).body_digest is None

    minted = mint_spy(monkeypatch)
    second = resumable(port=port, repos=repos, evaluations=echoes(passed=set(A_KEYS)))
    reads = subject_reads(port, monkeypatch)
    events = [event async for event in drive(second, job="second-job")]

    # Not refused, and not re-entered by minting a second branch beside the
    # record: the lane ran, on the branch the record names.
    assert lane_failures(events) == ()
    assert minted == []
    assert reads == ["A"]
    assert len(second.executor.execution_prompts) == 1
    assert "Exact native subject A" in second.executor.execution_prompts[0]
    after = await lane_record(port, "A")
    assert (
        after.body_digest
        == hashlib.sha256(port.issues["A"].body.encode("utf-8")).hexdigest()
    )


async def test_a_subject_amended_between_runs_is_refused_by_digest_not_re_read(
    monkeypatch,
):
    """An edited subject is an amendment, never a silent re-read.

    Run one pins the digest. The subject is then edited, and the second
    process reads the text once at entry, compares, and refuses this one lane
    before any session: the criteria it owes were graded against the text the
    digest names. Nothing re-pins the digest, so the record is untouched.
    """
    repos = WalkRepos()
    port = board(lanes=("A",), checks=TWO_CHECKS)
    _, before = await first_fire(port, repos)
    port.issues["A"] = port.issues["A"].model_copy(
        update={"body": "A later subject body must not be read into a resumed lane"}
    )
    minted = mint_spy(monkeypatch)

    second = resumable(port=port, repos=repos, evaluations=echoes(passed=set(A_KEYS)))
    reads = subject_reads(port, monkeypatch)
    await walk_reporting(
        second, kind="SubjectAmendedError", match="differs from the recorded"
    )

    # Read once, at the entry, and compared there: a second process that
    # re-read the text on its way past the comparison would read it again.
    assert reads == ["A"]
    assert minted == []
    assert second.executor.execution_prompts == []
    assert second.executor.evaluation_prompts == []
    after = await lane_record(port, "A")
    assert after == before
    assert (
        after.body_digest
        == before.body_digest
        != hashlib.sha256(port.issues["A"].body.encode("utf-8")).hexdigest()
    )


# ---------------------------------------------------------------------------
# KOD-449 — a killed scope re-enters and dispatches exactly the lanes that
# are left, on their recorded branches, reading no merge state to decide.
# ---------------------------------------------------------------------------

FORGE_ORIGIN = "https://github.com/owner/repo"
#: Lane C owes two criteria, so the fire killed mid-flight leaves one finished
#: and one open and the re-entry has something to tell apart.
THREE_LANES = ("A", "B", "C")


def one_check_echoes(key: str, rounds: int = 2):
    """The gradings one single-criterion lane's fire asks for."""
    return [
        criteria_echo(keys=(f"{key}/check",), passed={f"{key}/check"})
        for _ in range(rounds)
    ]


async def test_kill_and_re_enter_dispatches_exactly_the_remaining_lanes(monkeypatch):
    """A scope killed after two lanes finished re-enters on the third alone.

    Run one delivers A and B — really delivers them: the forge double answers
    each pull request's head at the sha the walk's own merger published, so
    the identity check a delivery makes passes and no lane fails — and is then
    KILLED while C's fire is in flight, after C's first criterion was crossed
    off and recorded: the walk's task is cancelled, and cancellation is a
    BaseException the lane boundary does not contain, so the run really ends
    where a process would. Run two shares only the board, the remote and the
    forge: it dispatches C and nothing else, fails no lane, resumes on C's
    recorded branch without minting, acquires no other branch, owes only C's
    open criterion, and reads no pull-request state at all before C's first
    session.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=THREE_LANES, checks={"C": ("check", "second")})
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        first = resumable(
            port=port,
            repos=repos,
            lanes=THREE_LANES,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=[
                *one_check_echoes("A"),
                *one_check_echoes("B"),
                *[criteria_echo(keys=C_KEYS, passed={"C/check"}) for _ in range(4)],
            ],
        )
        seen: list[object] = []

        async def walk() -> None:
            async for event in drive(first, job="first-job", origin=FORGE_ORIGIN):
                seen.append(event)

        task = asyncio.create_task(walk())
        # The same bound over run one's own spin: the walk either reaches C or
        # ends, and "neither" is a failure rather than a test that never
        # returns.
        deadline = asyncio.get_running_loop().time() + 60
        while not any(
            isinstance(event, ScopeLaneEvent)
            and event.lane_key == "C"
            and isinstance(event.event, WorkflowIterationEvent)
            for event in seen
        ):
            assert not task.done(), "the walk ended before lane C was in flight"
            assert asyncio.get_running_loop().time() < deadline, (
                "the walk never reached lane C"
            )
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert port.issues["C/check"].state_kind is WorkflowStateKind.COMPLETED
        assert port.issues["C/second"].state_kind is WorkflowStateKind.UNSTARTED
        killed = await lane_record(port, "C")
        # A and B ran to delivery before the kill, and neither failed doing it:
        # a contained delivery failure would have been reported here.
        assert len(wire.creates) == 2
        assert lane_failures(seen) == ()
        assert killed.branch not in {create["head"] for create in wire.creates}

        minted = mint_spy(monkeypatch)
        second = resumable(
            port=port,
            repos=repos,
            lanes=THREE_LANES,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=[
                criteria_echo(keys=C_KEYS, passed=set(C_KEYS)) for _ in range(6)
            ],
        )
        # The wire outlives the kill, so what "zero" means is "none more than
        # the reads A's and B's own deliveries made in run one": each delivery
        # reads its pull request's own state three times — at the identity
        # check that follows opening it, once more before it returns, and again
        # where the lane graph completes.
        before_re_entry = len(wire.pr_reads)
        assert before_re_entry == 6
        reads_at_first_session: list[int] = []
        sessions = second.executor.stream

        def recording(**arguments):
            reads_at_first_session.append(len(wire.pr_reads))
            return sessions(**arguments)

        monkeypatch.setattr(second.executor, "stream", recording)
        # Bounded: a walk that offered a lane forever would hang here instead
        # of failing, and a hang is not an assertion. This run is four ticks.
        async with asyncio.timeout(60):
            events = [
                event
                async for event in drive(second, job="second-job", origin=FORGE_ORIGIN)
            ]

        assert [
            event.lane_key
            for event in events
            if isinstance(event, ScopeLaneEvent)
            and isinstance(event.event, WorkflowIterationEvent)
        ] == ["C"]
        assert not [
            prompt
            for prompt in second.executor.execution_prompts
            for key in ("A", "B")
            if f"Exact native subject {key}" in prompt
        ]
        # Exactly the lanes that are left, read off the walk's own report
        # rather than off which lanes emitted an iteration: a lane that
        # entered and then failed would appear here.
        final = [
            event.observation for event in events if isinstance(event, ScopeWalkEvent)
        ][-1]
        assert final.dispatched == ("C",)
        assert final.failed_lanes == ()
        assert minted == []
        # One branch was checked out in this whole run, and it is the one the
        # record names.
        assert {
            acquisition["branch_name"]
            for acquisition in second.workspace.acquisitions
            if acquisition["branch_name"]
        } == {killed.branch}
        opened = second.workspace.acquisitions[0]
        assert opened["branch_name"] == opened["ref"] == killed.branch
        assert opened["create_branch"] is False
        prompt = second.executor.execution_prompts[0]
        assert "C/second live Check  bytes" in prompt
        assert "C live Check  bytes" not in prompt
        # No pull request's own state was read to decide any of that: the entry
        # reads the record and the remote head, and a delivery's own read comes
        # after the lane has already worked. The open-delivery LISTING is read
        # before the session and is counted separately here; removing it from
        # selection is slice 2c's criterion, not this one's.
        assert reads_at_first_session
        assert reads_at_first_session[0] == before_re_entry
        # And C's own delivery does make its three, after the lane has worked.
        assert len(wire.pr_reads) == before_re_entry + 3
        assert len(wire.creates) == 3
    finally:
        await forge.close()


# ---------------------------------------------------------------------------
# KOD-844 — a recorded lane with every criterion Done and no pull request is
# dispatched for its delivery alone.
# ---------------------------------------------------------------------------


async def stopped_at_consolidation(port, repos, *, origin, forge=None):
    """Run one of lane A, ended the instant its loop branch was consolidated.

    The stream is closed where a process dies, and generator close is a
    ``BaseException`` the lane boundary does not contain, so the run really
    ends there. What it leaves is exactly the state this criterion is about:
    the criterion crossed off, a record naming the lane's branches, and no
    pull request anywhere.
    """
    harness = resumable(
        port=port,
        repos=repos,
        origin=origin,
        forge=forge,
        trunk="main",
        evaluations=one_check_echoes("A", rounds=4),
    )
    stream = drive(harness, job="first-job", origin=origin)
    async for event in stream:
        if isinstance(event, ScopeLaneEvent) and isinstance(
            event.event, WorkflowConsolidationEvent
        ):
            break
    await stream.aclose()
    assert port.issues["A/check"].state_kind is WorkflowStateKind.COMPLETED
    record = await lane_record(port, "A")
    assert record.pr is None
    return harness, record


async def test_a_finished_lane_without_a_pull_request_is_delivered_without_a_loop():
    """The lane the kill left between its last cross-off and its delivery.

    Its gap is empty, so no reading of readiness would ever offer it as a
    candidate for work, and its record carries no pull request, so nothing it
    produced is published. Run two dispatches it for the delivery alone: no
    implementation session opens at all, the recorded branches are
    consolidated and reviewed, one pull request is opened from the branch the
    record names, and the record then carries it. Run three has nothing left
    to do with the lane and says so instead of offering it again.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A",))
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        _, killed = await stopped_at_consolidation(
            port, repos, origin=FORGE_ORIGIN, forge=forge
        )
        deliverable = recorded_branches(record=killed).deliverable_branch
        assert wire.creates == []

        second = resumable(
            port=port,
            repos=repos,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=one_check_echoes("A", rounds=4),
        )
        events = [
            event
            async for event in drive(second, job="second-job", origin=FORGE_ORIGIN)
        ]

        assert lane_failures(events) == ()
        # No iteration, and no session an iteration would have opened: the
        # lane had nothing left to execute and none was asked for.
        assert second.executor.execution_prompts == []
        assert not [
            event
            for event in events
            if isinstance(event, ScopeLaneEvent)
            and isinstance(event.event, WorkflowIterationEvent)
        ]
        # One pull request, from the branch the record names — not from the
        # loop branch the record stands on.
        assert [create["head"] for create in wire.creates] == [deliverable]
        assert killed.branch != deliverable
        delivered = await lane_record(port, "A")
        assert delivered.pr is not None
        assert delivered.pr.number == ScopeForgeWire.FIRST_NUMBER
        assert str(ScopeForgeWire.FIRST_NUMBER) in delivered.pr.url
        # The lane's delivery is what the walk reports it did.
        walked = [
            event.observation for event in events if isinstance(event, ScopeWalkEvent)
        ]
        assert walked[-1].dispatched == ("A",)

        third = resumable(
            port=port,
            repos=repos,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=one_check_echoes("A", rounds=4),
        )
        with structlog.testing.capture_logs() as logs:
            after = [
                event
                async for event in drive(third, job="third-job", origin=FORGE_ORIGIN)
            ]

        settled = [
            event.observation for event in after if isinstance(event, ScopeWalkEvent)
        ]
        assert settled[-1].dispatched == ()
        assert lane_failures(after) == ()
        assert third.executor.execution_prompts == []
        assert len(wire.creates) == 1
        # Rested by name and exactly once: the record carries the pull
        # request, so there is nothing to do and the walk cannot spin on it.
        assert [
            event["lane"]
            for event in logs
            if event.get("event") == "scope_lane_nothing_to_do"
        ] == ["A"]
    finally:
        await forge.close()


async def test_a_reopened_criterion_refuses_a_deliver_only_entry(monkeypatch):
    """A criterion reopened between the walk's selection and the fire's entry.

    The lane was selected because its subtree owed nothing, and the fire's own
    reading of the finished roster is taken again inside the graph. Between the
    two the board moves: the criterion returns to Todo the way an amendment
    puts one back. The roster read refuses and names it, before any session
    opens and before anything is published.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A",))
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        await stopped_at_consolidation(port, repos, origin=FORGE_ORIGIN, forge=forge)

        second = resumable(
            port=port,
            repos=repos,
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=one_check_echoes("A", rounds=4),
        )
        read_spec = port.read_fire_spec
        reopened: list[str] = []

        async def reopening(*, issue_key):
            spec = await read_spec(issue_key=issue_key)
            if not reopened:
                reopened.append(issue_key)
                owed_again(port)
            return spec

        monkeypatch.setattr(port, "read_fire_spec", reopening)
        events = [
            event
            async for event in drive(second, job="second-job", origin=FORGE_ORIGIN)
        ]

        assert reopened == ["A"]
        failures = lane_failures(events)
        assert [failure.issue_key for failure in failures] == ["A"]
        assert [failure.error.error_kind for failure in failures] == [
            "FireSpecEntryError"
        ]
        assert "A/check" in failures[0].error.error
        assert "not finished" in failures[0].error.error
        # Nothing was published and nothing was executed on the way to the
        # refusal: it lands before the lane does anything at all.
        assert wire.creates == []
        assert second.executor.execution_prompts == []
        assert (await lane_record(port, "A")).pr is None
    finally:
        await forge.close()


async def test_a_forge_less_origin_never_selects_a_finished_lane():
    """The same record, on an origin no pull request could be opened on.

    Such a lane could never record a pull request, so delivering it would
    change nothing about it and every invocation would consolidate and review
    it again. It is not a candidate at all there: the walk dispatches nothing,
    reports no failure, and leaves the lane for a person.
    """
    repos = WalkRepos()
    port = board(lanes=("A",))
    _, killed = await stopped_at_consolidation(port, repos, origin=ORIGIN)

    second = resumable(
        port=port,
        repos=repos,
        origin=ORIGIN,
        trunk="main",
        evaluations=one_check_echoes("A", rounds=4),
    )
    events = [event async for event in drive(second, job="second-job")]

    walked = [
        event.observation for event in events if isinstance(event, ScopeWalkEvent)
    ]
    assert walked[-1].dispatched == ()
    assert lane_failures(events) == ()
    assert second.executor.execution_prompts == []
    # Not vacuous: the record is there, with every criterion Done and no pull
    # request, which on a delivering origin is the entry the walk fires.
    assert (await lane_record(port, "A")).pr is None
    assert recorded_branches(record=killed).deliverable_branch


# ---------------------------------------------------------------------------
# KOD-328 — a dependent lane's pull request stands on its blocker's recorded
# branch, and a base the remote does not hold refuses instead of trunk.
# ---------------------------------------------------------------------------


class RefusingCreate(ScopeForgeWire):
    """A forge that will not open a pull request for one lane's branch.

    Which one is refused is decided by the head the request names, because a
    native lane's deliverable branch carries its own issue key. So the lane
    whose delivery fails is chosen, and every other lane's request — the
    dependent lane's own create included — is answered as usual.
    """

    def __init__(self, *, refuses: str, **rest) -> None:
        super().__init__(**rest)
        self.refuses = refuses

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/pulls"):
            head = json.loads(request.content)["head"]
            if head.startswith(f"kodezart/{self.refuses}-"):
                self.requests.append(request)
                return httpx.Response(422, json={"message": "no pull request here"})
        return super().__call__(request)


def bases_of(events):
    """The base each lane was actually prepared with, off the run's own event."""
    return {
        event.lane_key: event.event.base_branch
        for event in events
        if isinstance(event, ScopeLaneEvent)
        and isinstance(event.event, WorkflowScopeBaseEvent)
    }


def pull_numbers_read(wire) -> set[str]:
    """The pull requests whose own state this wire was asked about."""
    return {request.url.path.rsplit("/", 1)[-1] for request in wire.pr_reads}


async def test_a_dependent_lane_opens_its_pull_request_against_its_blockers_branch():
    """B's pull request is opened against the branch A's record names.

    A's own delivery is refused by the forge, so A holds no pull request at
    all when B delivers: nothing about B's base can have come from reading
    one. What B stands on is the deliverable branch A's record names, which is
    what B's pull request is then opened against — and the only pull request
    whose state this run reads is B's own.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    wire = RefusingCreate(refuses="A", head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        harness = resumable(
            port=port,
            repos=repos,
            lanes=("A", "B"),
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=[
                *one_check_echoes("A", rounds=4),
                *one_check_echoes("B", rounds=4),
            ],
        )
        events = [
            event
            async for event in drive(harness, job="stacked-job", origin=FORGE_ORIGIN)
        ]

        record = await lane_record(port, "A")
        deliverable = recorded_branches(record=record).deliverable_branch
        # A's turn ended in the forge's own error, and A was offered once: a
        # failed lane is rested for the rest of the invocation.
        failures = lane_failures(events)
        assert [failure.issue_key for failure in failures] == ["A"]
        assert [failure.error.error_kind for failure in failures] == ["ForgeAPIError"]
        final = [
            event.observation for event in events if isinstance(event, ScopeWalkEvent)
        ][-1]
        assert final.dispatched == ("A", "B")
        assert record.pr is None
        # One pull request exists, B's, and it stands on A's recorded branch —
        # not on A's loop branch and not on the trunk.
        assert len(wire.creates) == 1
        assert wire.creates[-1]["base"] == deliverable
        assert wire.creates[-1]["head"] == (
            recorded_branches(record=await lane_record(port, "B")).deliverable_branch
        )
        assert deliverable != record.branch
        assert bases_of(events)["B"] == deliverable
        assert bases_of(events)["B"] not in TRUNK_BRANCHES
        # Nothing read a pull request of A's to settle any of that: A has none,
        # and every state read this run made names B's.
        assert pull_numbers_read(wire) == {str(ScopeForgeWire.FIRST_NUMBER)}
        assert wire._numbers[wire.creates[-1]["head"]] == ScopeForgeWire.FIRST_NUMBER
    finally:
        await forge.close()


async def test_a_blocker_branch_absent_from_the_remote_refuses_without_trunk():
    """A base that resolved and is not on the remote is a refusal, never trunk.

    A delivers and its record names its deliverable branch; the branch is then
    gone from the remote, as a branch somebody deleted is. B's base resolution
    located the premise and cannot find it published, so B refuses. Falling
    back to the trunk would diff and deliver B against a tree A's work is not
    in, and look like a lane that simply had no blocker.
    """
    repos = WalkRepos(url=FORGE_ORIGIN)
    port = board(lanes=("A", "B"), blocked={"B": ("A",)})
    wire = ScopeForgeWire(head_sha_of=repos.head_of)
    forge = _make_client(wire)
    try:
        harness = resumable(
            port=port,
            repos=repos,
            lanes=("A", "B"),
            origin=FORGE_ORIGIN,
            forge=forge,
            trunk="main",
            evaluations=[
                *one_check_echoes("A", rounds=4),
                *one_check_echoes("B", rounds=4),
            ],
        )
        deleted: list[str] = []
        events = []
        async for event in drive(harness, job="stacked-job", origin=FORGE_ORIGIN):
            events.append(event)
            if (
                isinstance(event, ScopeLaneEvent)
                and isinstance(event.event, LaneDeliveryEvent)
                and event.lane_key == "A"
                and not deleted
            ):
                # A delivered, so its branch is published and its record names
                # it. Now the remote holds it no longer: what the repository
                # committed locally is untouched, which is what a branch
                # somebody deleted on the remote leaves behind.
                branch = recorded_branches(
                    record=await lane_record(port, "A")
                ).deliverable_branch
                repos.branches[branch].pushed = None
                deleted.append(branch)

        assert deleted
        assert (await lane_record(port, "A")).pr is not None
        failures = lane_failures(events)
        assert [failure.issue_key for failure in failures] == ["B"]
        assert [failure.error.error_kind for failure in failures] == [
            "BaseResolutionError"
        ]
        assert "absent from the remote" in failures[0].error.error
        # The branch is on the refusal itself. The walk's report carries a
        # typed error's message and not its fields, so the error is asked for
        # again here, from the resolver this walk holds and over the board and
        # remote it left: the premise it names is A's own recorded branch.
        with pytest.raises(BaseResolutionError) as caught:
            await harness.engine._scoped_arm._resolver.resolve(
                issue_key="B",
                repo_path="/tmp/walk",
                integration_workspace="/tmp/walk-integration",
                trunk="main",
                now=datetime.now(tz=UTC),
            )
        assert caught.value.branches == (deleted[0],)
        # B never reached a base at all, so nothing named the trunk for it,
        # and A's is the only pull request this run opened.
        assert "B" not in bases_of(events)
        assert [create["head"] for create in wire.creates] == [deleted[0]]
    finally:
        await forge.close()
