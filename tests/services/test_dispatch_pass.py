"""One scheduled tick: the gate, then the real dispatcher behind it.

Nothing is stubbed between the tick and the queue.  ``GatedDispatchPass``
is driven over the shipped ``PassGate`` and the shipped ``FireDispatcher``,
so "the pass ran" is observed as a job on the queue and "the pass was
skipped" as an untouched dispatcher — not as a call count on a double
standing in for the thing under test.

The second half drives ``build_dispatch_passes`` — the composition root's
own builder — and asserts that what boot hands the scheduler is this same
object, one per declared repository, carrying the configured cadence.
"""

import asyncio
from collections.abc import Callable

import structlog.testing

from kodezart.adapters.no_forge_delivery import NoForgeDeliveryProbe
from kodezart.composition.passes import build_dispatch_passes, delivery_probe_for
from kodezart.core.config import AppConfig
from kodezart.domain.git_url import extract_owner_repo
from kodezart.services.base_resolver import BaseResolver
from kodezart.services.claim_heartbeat import ClaimHeartbeat
from kodezart.services.dispatch_pass import GatedDispatchPass
from kodezart.services.fire_context import FireContextAssembler
from kodezart.services.fire_dispatcher import FireDispatcher
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.pass_gate import PassGate
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.agent import WorkflowCompleteEvent
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.gating import OutboundDestination, RepoVisibility
from kodezart.types.domain.operation import (
    CheckStep,
    DocumentEntry,
    DocumentSystem,
    Initiative,
    LifecycleStage,
    OperationConfig,
    Principal,
    PrincipalRole,
    QueueState,
    RecordDestination,
    RepoEntry,
    TeamEntry,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.tracker import IssuePriority
from tests.fakes import (
    FakeDeliveryProbe,
    FakeGitService,
    FakeJobQueue,
    FakeRepoCache,
    FakeTrackerPort,
    PassThroughGate,
    make_tracker_issue,
)

APPROVER = "the-approver"
PRIMARY_REPO = "https://example.invalid/owner/primary"
SECOND_REPO = "https://example.invalid/owner/second"
#: A local bare repository — the sanctioned smoke origin, and the one the
#: first live run's dispatch tick died on every interval (KOD-145).
FILE_ORIGIN = "file:///tmp/fixture-origin.git"
LANE = "tracker"
TRUNK = "trunk"
REMOTE = "fixture-remote"
INTEGRATION_DIR = "/tmp/fixture-integration"
HOLDER = "pass-a"
LEASE_SECONDS = 600.0
PAGE_SIZE = 50
ASSET_MAX_COUNT = 20
ASSET_MAX_BYTES = 262144
ASSET_FETCH_TIMEOUT_SECONDS = 30.0
RENEWAL_FRACTION = 0.25
SETTLE_TRIES = 500
SETTLE_DELAY_SECONDS = 0.01


async def settled(condition: Callable[[], bool]) -> None:
    """Wait until *condition* holds, then let the assertions do the reporting.

    The delay is real. The lifecycle write-back awaits a logger whose
    underlying call completes on a thread-pool executor, and an
    executor-backed future is not advanced by ``asyncio.sleep(0)`` — a bare
    yield only reschedules the loop, so a busy machine reaches the
    assertions before the writes they read.
    """
    for _ in range(SETTLE_TRIES):
        if condition():
            return
        await asyncio.sleep(SETTLE_DELAY_SECONDS)


#: One board per repository, in declaration order. The first key is the one
#: ``make_tracker_issue`` puts on every fixture issue.
TEAM_KEYS: tuple[str, ...] = ("engineering", "design")
#: Ticket bodies that say which board an enqueued fire came from.
ENGINEERING_BODY = "the engineering board's work"
DESIGN_BODY = "the design board's work"


def teams_for(repos: tuple[str, ...]) -> dict[str, TeamEntry]:
    """A board per declared repository, bound to it (KOD-157).

    A single-repository operation declares no binding at all — every team
    binds to the only candidate implicitly — so the fixture exercises both
    shapes off the same argument.
    """
    return {
        key: TeamEntry(
            name=f"fixture-{key}",
            key=key[:3].upper(),
            repository=None if len(repos) == 1 else url,
        )
        for key, url in zip(TEAM_KEYS[: len(repos)], repos, strict=True)
    }


def operation_config(
    *,
    repos: tuple[str, ...] = (PRIMARY_REPO,),
    teams: dict[str, TeamEntry] | None = None,
) -> OperationConfig:
    return OperationConfig(
        operation_name="fixture",
        workspace="fixture-workspace",
        principals=[
            Principal(
                tracker_user=APPROVER,
                roles=frozenset(
                    {
                        PrincipalRole.APPROVER,
                        PrincipalRole.PRINCIPAL,
                        PrincipalRole.ASSIGNEE,
                    },
                ),
                handle="@approver",
            ),
        ],
        agent_identities=[],
        teams=teams_for(repos) if teams is None else teams,
        queue_states={member.value: f"queue:{member.value}" for member in QueueState},
        workflow_states={
            LifecycleStage.IN_PROGRESS: "In Progress",
            LifecycleStage.IN_REVIEW: "In Review",
            LifecycleStage.DONE: "Done",
        },
        repos=[
            RepoEntry(
                url=url,
                trunk=TRUNK,
                checks=[CheckStep(name="check", command="make check")],
            )
            for url in repos
        ],
        documents={
            "checkpoint": DocumentEntry(
                system=DocumentSystem.TRACKER,
                name="checkpoint",
                id="doc-1",
            ),
        },
        records={
            "run_log": RecordDestination(
                system=DocumentSystem.KNOWLEDGE,
                name="Run log",
                id="record-1",
                append_only=True,
            ),
        },
        knowledge={},
        endpoints={},
        initiatives=[Initiative(id="init-1")],
    )


def tick(tracker: FakeTrackerPort) -> tuple[GatedDispatchPass, FakeJobQueue]:
    """The shipped tick over the shipped gate and the shipped dispatcher."""
    queue = FakeJobQueue()
    pass_ = GatedDispatchPass(
        lifecycle=LifecycleWatcher(
            queue=queue,
            writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
            heartbeat=ClaimHeartbeat(
                tracker=tracker,
                holder=HOLDER,
                lease_seconds=LEASE_SECONDS,
                renewal_fraction=RENEWAL_FRACTION,
            ),
        ),
        gate=PassGate(
            tracker=tracker,
            signals=[PassSignal.approved_changed],
            page_size=PAGE_SIZE,
        ),
        dispatcher=FireDispatcher(
            tracker=tracker,
            queue=queue,
            registry=queue,
            delivery=FakeDeliveryProbe(),
            operation=operation_config(),
            repo_url=PRIMARY_REPO,
            lane=LANE,
            holder=HOLDER,
            claim_lease_seconds=LEASE_SECONDS,
            query_page_size=PAGE_SIZE,
            assembler=FireContextAssembler(
                tracker=tracker,
                gate=PassThroughGate(),
                max_count=ASSET_MAX_COUNT,
                max_bytes=ASSET_MAX_BYTES,
                fetch_timeout_seconds=ASSET_FETCH_TIMEOUT_SECONDS,
            ),
            resolver=BaseResolver(
                tracker=tracker,
                git=FakeGitService(),
                remote=REMOTE,
            ),
            cache=FakeRepoCache(),
            trunk=TRUNK,
            integration_workspace_dir=INTEGRATION_DIR,
        ),
    )
    return pass_, queue


async def test_a_delta_runs_the_pass_and_the_work_reaches_the_queue() -> None:
    """AC-19: something moved, so the expensive half runs and produces a job."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("K-1")],
    )
    pass_, queue = tick(tracker)

    await pass_.run()

    assert [lane for lane, _ in queue.submissions] == [LANE]
    assert tracker.claims["K-1"].holder == HOLDER


async def test_a_quiet_board_never_wakes_the_dispatcher() -> None:
    """AC-19: the gate is the whole cost of a tick over a board at rest."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("K-1", queue_states=[QueueState.TRIAGE])],
    )
    pass_, queue = tick(tracker)

    await pass_.run()

    assert queue.submissions == []
    assert tracker.claims == {}
    # One scan: the gate's. The dispatcher's own query never happened.
    assert len(tracker.scans) == 1


async def test_a_second_tick_over_an_unchanged_board_costs_one_query() -> None:
    """The mark carries between ticks, so a settled board stops waking."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("K-1")],
    )
    pass_, queue = tick(tracker)
    await pass_.run()
    scans_after_work = len(tracker.scans)

    await pass_.run()

    assert len(tracker.scans) == scans_after_work + 1
    assert len(queue.submissions) == 1


async def test_the_root_builds_one_gated_pass_per_declared_repository() -> None:
    """AC-20: every repository the operation acts on gets its own pass."""
    tracker = FakeTrackerPort()
    queue = FakeJobQueue()
    built = await build_dispatch_passes(
        config=AppConfig(),
        operation=operation_config(repos=(PRIMARY_REPO, SECOND_REPO)),
        tracker=tracker,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    assert [entry.name for entry in built.passes] == [
        f"dispatch:{PRIMARY_REPO}",
        f"dispatch:{SECOND_REPO}",
    ]
    assert all(
        isinstance(entry.run.__self__, GatedDispatchPass) for entry in built.passes
    )


async def test_an_issue_only_fires_into_the_repository_its_team_is_bound_to() -> None:
    """KOD-157: two teams, two repositories, and no crossing.

    The failure this asserts against is the shape as shipped: every pass
    scanned EVERY declared team, so the pool each repository's tick chose
    from was the union of both boards and an issue went wherever the
    first tick reached it.  ``K-DES`` outranks ``K-ENG``, so under that
    shape the primary repository's tick claims the design board's issue
    outright — a fact, not a draw.
    """
    tracker = FakeTrackerPort(
        issues=[
            make_tracker_issue(
                "K-ENG",
                team_key="engineering",
                body=ENGINEERING_BODY,
            ),
            make_tracker_issue(
                "K-DES",
                team_key="design",
                body=DESIGN_BODY,
                priority=IssuePriority.URGENT,
            ),
        ],
    )
    queue = FakeJobQueue()
    built = await build_dispatch_passes(
        config=AppConfig(),
        operation=operation_config(repos=(PRIMARY_REPO, SECOND_REPO)),
        tracker=tracker,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    await built.passes[0].run()
    await built.passes[1].run()

    # The body is what identifies the issue inside the fire: it is the one
    # part of the request that came from the ticket the pass claimed.
    fired = [
        (request.repo_url, ENGINEERING_BODY in request.prompt)
        for _, request in queue.submissions
    ]
    assert fired == [(PRIMARY_REPO, True), (SECOND_REPO, False)]
    assert DESIGN_BODY in queue.submissions[1][1].prompt


async def test_a_repository_no_team_is_bound_to_gets_a_named_skip() -> None:
    """The other arm: no pass, and the log says which repository, once.

    A tick over a repository nobody fires into scans nothing every
    interval forever, which is noise rather than coverage — but a
    schedule that is silently one pass short is worse, so the state is
    named at build.
    """
    tracker = FakeTrackerPort()
    queue = FakeJobQueue()
    with structlog.testing.capture_logs() as logs:
        built = await build_dispatch_passes(
            config=AppConfig(),
            operation=operation_config(
                repos=(PRIMARY_REPO, SECOND_REPO),
                teams={
                    "engineering": TeamEntry(
                        name="fixture-engineering",
                        key="ENG",
                        repository=PRIMARY_REPO,
                    ),
                },
            ),
            tracker=tracker,
            delivery=FakeDeliveryProbe(),
            queue=queue,
            registry=queue,
            gate=PassThroughGate(),
            git=FakeGitService(),
            cache=FakeRepoCache(),
            integration_workspace_dir=INTEGRATION_DIR,
        )

    assert [entry.name for entry in built.passes] == [f"dispatch:{PRIMARY_REPO}"]
    assert [
        entry["repo_url"]
        for entry in logs
        if entry["event"] == "dispatch_pass_unbound_repository"
    ] == [SECOND_REPO]


async def test_the_root_gives_every_pass_the_configured_cadence() -> None:
    """AC-20: ``tracker_scheduler_pass_interval_seconds`` has a real consumer."""
    unusual = 41.0
    config = AppConfig(tracker_scheduler_pass_interval_seconds=unusual)
    assert (
        config.tracker_scheduler_pass_interval_seconds
        != AppConfig().tracker_scheduler_pass_interval_seconds
    )

    tracker = FakeTrackerPort()
    queue = FakeJobQueue()
    built = await build_dispatch_passes(
        config=config,
        operation=operation_config(repos=(PRIMARY_REPO, SECOND_REPO)),
        tracker=tracker,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    assert [entry.interval_seconds for entry in built.passes] == [unusual, unusual]


async def test_a_pass_the_root_built_dispatches_the_repository_it_names() -> None:
    """AC-20: the built object is wired, not merely shaped like one."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("K-1")],
    )
    queue = FakeJobQueue()
    built = await build_dispatch_passes(
        config=AppConfig(),
        operation=operation_config(repos=(SECOND_REPO,)),
        tracker=tracker,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    await built.passes[0].run()

    assert len(queue.submissions) == 1
    lane, request = queue.submissions[0]
    assert lane == AppConfig().dispatch_lane
    assert request.repo_url == SECOND_REPO
    assert tracker.claims["K-1"].holder == AppConfig().dispatch_holder


async def test_a_pass_the_root_built_follows_the_run_it_enqueued() -> None:
    """The lifecycle write-back is reached from the composition root.

    Nothing here calls ``TrackerLifecycleWriter``.  The only input is a
    dispatch pass built by ``build_dispatch_passes`` running over a queue
    whose job emits a completed run, and the observable is the transition
    on the tracker — so a root that constructs the writer without wiring
    it fails this.
    """
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue("K-1")],
    )
    queue = FakeJobQueue(
        events=[
            WorkflowCompleteEvent(
                feature_branch="feature",
                ralph_branch="ralph",
                total_iterations=1,
                accepted=True,
                outcome=WorkflowOutcome.ci_passed,
                merged=True,
            ),
        ],
    )
    built = await build_dispatch_passes(
        config=AppConfig(),
        operation=operation_config(),
        tracker=tracker,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    await built.passes[0].run()
    # The write-back runs in a background watch, so the test waits for the
    # terminal chain it asserts on: the DONE transition, then the comment
    # that ``LifecycleWatcher`` posts after it.
    await settled(
        lambda: (
            ("K-1", LifecycleStage.DONE) in tracker.workflow_writes
            and bool(tracker.comments)
        ),
    )

    assert queue.attached == ["job-0001"]
    assert tracker.workflow_writes == [
        ("K-1", LifecycleStage.IN_PROGRESS),
        ("K-1", LifecycleStage.DONE),
    ]
    assert tracker.queue_writes == [("K-1", QueueState.DONE)]
    assert [comment.issue_key for comment in tracker.comments] == ["K-1"]


async def test_the_pass_threads_the_claimed_boards_posture_to_the_watch() -> None:
    """KOD-157: the writer gates under the posture of the winner's board.

    Asserted at the watcher boundary the way ``pre_claim_state`` is, and
    through the write the watch actually makes rather than a call count on
    a double standing in for it: the dispatcher is the only component that
    knows the claimed issue's team, and the gated write that needs its
    posture is three hops downstream.
    """
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    queue = FakeJobQueue(
        events=[
            WorkflowCompleteEvent(
                feature_branch="feature",
                ralph_branch="ralph",
                total_iterations=1,
                accepted=True,
                outcome=WorkflowOutcome.ci_passed,
                merged=True,
            ),
        ],
    )
    gate = PassThroughGate()
    built = await build_dispatch_passes(
        config=AppConfig(),
        operation=operation_config(
            teams={
                "engineering": TeamEntry(
                    name="fixture-engineering",
                    key="ENG",
                    visibility=RepoVisibility.PRIVATE,
                ),
            },
        ),
        tracker=tracker,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
        gate=gate,
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    await built.passes[0].run()
    await settled(lambda: bool(tracker.comments))

    assert [
        visibility
        for (_, visibility, _), destination in zip(
            gate.calls,
            gate.destinations,
            strict=True,
        )
        if destination is OutboundDestination.TRACKER_COMMENT
    ] == [RepoVisibility.PRIVATE]


class ForgeOnlyDeliveryProbe:
    """A probe that parses the URL first, exactly as the forge client does.

    ``GitHubAPIClient.open_delivery_exists`` opens with
    ``extract_owner_repo(repo_url)``, so this stands in for it by calling
    the SAME function rather than by counting calls: a composition that
    hands a forge-less origin to the forge probe fails here by raising the
    error the first live run crash-looped on, not by an assertion about
    what was wired (KOD-145).
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def open_delivery_exists(self, *, repo_url: str, issue_key: str) -> bool:
        extract_owner_repo(repo_url)
        self.calls.append(issue_key)
        return False


async def test_a_pass_over_a_forge_less_origin_completes_its_tick() -> None:
    """Boot 25 as a fixture: the crash-loop, reproduced and no longer fatal.

    The scheduler ticked every 300 seconds for half an hour and every tick
    died identically at the eligibility phase — ``ValueError: Cannot
    extract owner/repo from file:// URL`` — before any claim was
    attempted.  The service stayed healthy while its one purpose
    crash-looped.

    A local bare origin is the sanctioned smoke shape, so the issue is
    ELIGIBLE here: no open pull request delivers it, because on this
    origin none can exist.  The forge probe is never asked (KOD-145).
    """
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    queue = FakeJobQueue()
    forge = ForgeOnlyDeliveryProbe()
    built = await build_dispatch_passes(
        config=AppConfig(),
        operation=operation_config(repos=(FILE_ORIGIN,)),
        tracker=tracker,
        delivery=forge,
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    await built.passes[0].run()

    assert len(queue.submissions) == 1
    _, request = queue.submissions[0]
    assert request.repo_url == FILE_ORIGIN
    assert tracker.claims["K-1"].holder == AppConfig().dispatch_holder
    assert forge.calls == []


async def test_a_pass_over_a_forge_shaped_origin_still_asks_the_forge() -> None:
    """The other arm: selection, not removal. A forge origin keeps its probe."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue("K-1")])
    queue = FakeJobQueue()
    forge = ForgeOnlyDeliveryProbe()
    built = await build_dispatch_passes(
        config=AppConfig(),
        operation=operation_config(repos=(PRIMARY_REPO,)),
        tracker=tracker,
        delivery=forge,
        queue=queue,
        registry=queue,
        gate=PassThroughGate(),
        git=FakeGitService(),
        cache=FakeRepoCache(),
        integration_workspace_dir=INTEGRATION_DIR,
    )

    await built.passes[0].run()

    assert forge.calls == ["K-1"]
    assert len(queue.submissions) == 1


def test_each_repository_gets_the_probe_its_own_origin_can_answer() -> None:
    """One operation, two origins, two probes — the selection is per repo."""
    forge = ForgeOnlyDeliveryProbe()

    assert delivery_probe_for(PRIMARY_REPO, forge=forge) is forge
    assert isinstance(
        delivery_probe_for(FILE_ORIGIN, forge=forge),
        NoForgeDeliveryProbe,
    )
