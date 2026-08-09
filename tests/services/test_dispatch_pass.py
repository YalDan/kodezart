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

from kodezart.core.config import AppConfig
from kodezart.main import build_dispatch_passes
from kodezart.services.dispatch_pass import GatedDispatchPass
from kodezart.services.fire_context import FireContextAssembler
from kodezart.services.fire_dispatcher import FireDispatcher
from kodezart.services.pass_gate import PassGate
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
)
from tests.fakes import (
    FakeDeliveryProbe,
    FakeJobQueue,
    FakeTracker,
    approved_by,
    make_tracker_issue,
)

APPROVER = "the-approver"
PRIMARY_REPO = "https://example.invalid/owner/primary"
SECOND_REPO = "https://example.invalid/owner/second"
LANE = "tracker"
HOLDER = "pass-a"
LEASE_SECONDS = 600.0
PAGE_SIZE = 50
ASSET_MAX_COUNT = 20
ASSET_MAX_BYTES = 262144
ASSET_FETCH_TIMEOUT_SECONDS = 30.0


def operation_config(*, repos: tuple[str, ...] = (PRIMARY_REPO,)) -> OperationConfig:
    return OperationConfig(
        operation_name="fixture",
        workspace="fixture-workspace",
        principals=[
            Principal(
                tracker_user=APPROVER,
                role=PrincipalRole.APPROVER,
                handle="@approver",
            ),
        ],
        agent_identities=[],
        teams={"engineering": "fixture-team"},
        queue_states={member.value: f"queue:{member.value}" for member in QueueState},
        workflow_states={
            LifecycleStage.IN_PROGRESS: "In Progress",
            LifecycleStage.IN_REVIEW: "In Review",
            LifecycleStage.DONE: "Done",
        },
        repos=[
            RepoEntry(
                url=url,
                check_commands=[CheckStep(name="check", command="make check")],
            )
            for url in repos
        ],
        documents={
            "checkpoint": DocumentEntry(system=DocumentSystem.TRACKER, id="doc-1"),
        },
        records={
            "run_log": RecordDestination(
                system=DocumentSystem.KNOWLEDGE,
                id="record-1",
                append_only=True,
            ),
        },
        knowledge={},
        endpoints={},
        initiatives=[Initiative(id="init-1")],
    )


def tick(tracker: FakeTracker) -> tuple[GatedDispatchPass, FakeJobQueue]:
    """The shipped tick over the shipped gate and the shipped dispatcher."""
    queue = FakeJobQueue()
    pass_ = GatedDispatchPass(
        gate=PassGate(
            tracker=tracker,
            queue_state=QueueState.APPROVED,
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
                max_count=ASSET_MAX_COUNT,
                max_bytes=ASSET_MAX_BYTES,
                fetch_timeout_seconds=ASSET_FETCH_TIMEOUT_SECONDS,
            ),
        ),
    )
    return pass_, queue


async def test_a_delta_runs_the_pass_and_the_work_reaches_the_queue() -> None:
    """AC-19: something moved, so the expensive half runs and produces a job."""
    tracker = FakeTracker(
        issues=[make_tracker_issue("K-1")],
        provenance=dict([approved_by("K-1", APPROVER)]),
    )
    pass_, queue = tick(tracker)

    await pass_.run()

    assert [lane for lane, _ in queue.submissions] == [LANE]
    assert tracker.claims["K-1"].holder == HOLDER


async def test_a_quiet_board_never_wakes_the_dispatcher() -> None:
    """AC-19: the gate is the whole cost of a tick over a board at rest."""
    tracker = FakeTracker(
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
    tracker = FakeTracker(
        issues=[make_tracker_issue("K-1")],
        provenance=dict([approved_by("K-1", APPROVER)]),
    )
    pass_, queue = tick(tracker)
    await pass_.run()
    scans_after_work = len(tracker.scans)

    await pass_.run()

    assert len(tracker.scans) == scans_after_work + 1
    assert len(queue.submissions) == 1


def test_the_root_builds_one_gated_pass_per_declared_repository() -> None:
    """AC-20: every repository the operation acts on gets its own pass."""
    tracker = FakeTracker()
    queue = FakeJobQueue()
    passes = build_dispatch_passes(
        config=AppConfig(),
        operation=operation_config(repos=(PRIMARY_REPO, SECOND_REPO)),
        tracker=tracker,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
    )

    assert [entry.name for entry in passes] == [
        f"dispatch:{PRIMARY_REPO}",
        f"dispatch:{SECOND_REPO}",
    ]
    assert all(isinstance(entry.run.__self__, GatedDispatchPass) for entry in passes)


def test_the_root_gives_every_pass_the_configured_cadence() -> None:
    """AC-20: ``dispatch_pass_interval_seconds`` has a real consumer."""
    unusual = 41.0
    config = AppConfig(dispatch_pass_interval_seconds=unusual)
    assert (
        config.dispatch_pass_interval_seconds
        != AppConfig().dispatch_pass_interval_seconds
    )

    tracker = FakeTracker()
    queue = FakeJobQueue()
    passes = build_dispatch_passes(
        config=config,
        operation=operation_config(repos=(PRIMARY_REPO, SECOND_REPO)),
        tracker=tracker,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
    )

    assert [entry.interval_seconds for entry in passes] == [unusual, unusual]


async def test_a_pass_the_root_built_dispatches_the_repository_it_names() -> None:
    """AC-20: the built object is wired, not merely shaped like one."""
    tracker = FakeTracker(
        issues=[make_tracker_issue("K-1")],
        provenance=dict([approved_by("K-1", APPROVER)]),
    )
    queue = FakeJobQueue()
    passes = build_dispatch_passes(
        config=AppConfig(),
        operation=operation_config(repos=(SECOND_REPO,)),
        tracker=tracker,
        delivery=FakeDeliveryProbe(),
        queue=queue,
        registry=queue,
    )

    await passes[0].run()

    assert len(queue.submissions) == 1
    lane, request = queue.submissions[0]
    assert lane == AppConfig().dispatch_lane
    assert request.repo_url == SECOND_REPO
    assert tracker.claims["K-1"].holder == AppConfig().dispatch_holder
