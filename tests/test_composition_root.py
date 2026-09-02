"""The composition root is a call graph, not a definition site.

`main.py` grew from 151 lines and two definitions at `92597c0` to 619 and
nine, one lane at a time, because every lane that ships an adapter has a
reason to add its builder here.  No criterion could see it: each addition
was locally correct and the file is not named by any Verification section.

That growth is not only untidiness.  The composition root is a measured
collision point — two parallel lanes were found to collide in seventeen
files, the engine construction here among them — so every builder defined
rather than imported is a merge conflict waiting for the next lane.

The rule is therefore mechanical, and this is the guard: the composition
root may define its framework hook and its factory, and nothing else.
A builder belongs in `kodezart.composition`, where it is unit-testable
without importing the application.

The lifespan's SHUTDOWN is asserted here too, in the two halves it has:
its order, read off the hook's own syntax tree, and its outcome, driven
over the shipped queue, watcher, recorder and a Fire Log double in that
same order — because "every fire leaves a row" is a property of the
sequence rather than of any component in it (KOD-178).
"""

import ast
import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import structlog.testing

from kodezart.adapters.asyncio_job_queue import AsyncioJobQueue
from kodezart.services.claim_heartbeat import ClaimHeartbeat
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.run_recorder import RunRecorder
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.agent import (
    AgentEvent,
    AssistantTextEvent,
    WorkflowCompleteEvent,
)
from kodezart.types.domain.operation import (
    DocumentSystem,
    LifecycleStage,
    RecordDestination,
    RunKind,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_records import RunOutcome
from kodezart.types.requests.agent import WorkflowRequest
from tests.fakes import (
    FakeFireReport,
    FakeTrackerPort,
    PassThroughGate,
    RecordingLogSink,
    make_tracker_issue,
)

#: The two definitions the composition root is allowed to own: the ASGI
#: lifespan hook the framework calls, and the application factory.
PERMITTED: frozenset[str] = frozenset({"lifespan", "create_app"})

ROOT: Path = Path(__file__).resolve().parents[1] / "src" / "kodezart" / "main.py"


def _top_level_definitions() -> list[str]:
    """Every function, coroutine and class defined at module level."""
    tree = ast.parse(ROOT.read_text(encoding="utf-8"))
    return [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]


def test_the_composition_root_defines_only_its_hook_and_its_factory() -> None:
    """A builder defined here is one no test can reach without the app."""
    surplus = sorted(set(_top_level_definitions()) - PERMITTED)

    assert surplus == [], (
        f"{ROOT.name} defines {surplus}, which belong in kodezart.composition. "
        "The composition root imports and wires; it does not define."
    )


def test_the_guard_reads_a_real_module() -> None:
    """An empty parse would let the rule above pass over anything."""
    defined = _top_level_definitions()

    assert set(defined) == PERMITTED


def _dotted(node: ast.expr) -> str:
    """The dotted name of an attribute chain, or "" for anything else."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return ""
    parts.append(current.id)
    return ".".join(reversed(parts))


def _lifespan_calls() -> list[str]:
    """Every dotted call the lifespan makes, in source order."""
    tree = ast.parse(ROOT.read_text(encoding="utf-8"))
    (hook,) = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    ]
    calls = [
        node
        for node in ast.walk(hook)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    # ``ast.walk`` is breadth-first; the question here is ORDER, so the
    # nodes are put back into the order they were written in.
    calls.sort(key=lambda node: (node.lineno, node.col_offset))
    return [_dotted(node.func) for node in calls]


def test_the_shutdown_records_unfinished_fires_over_a_quiescent_registry() -> None:
    """KOD-178 — the sweep's placement IS its correctness.

    After the queue's stop, because that is when nothing can finish
    underneath it: a fire completing between a registry read and the stop
    would be swept as failed and its own true row verified away (ruled
    2026-09-02). Before the drain, so a watch that ends on the stopped
    stream meets a log that already holds its run's row rather than the
    other way round, and before the knowledge session closes, because that
    session is what the rows are written through.
    """
    calls = _lifespan_calls()
    sweep = calls.index("dispatch.lifecycle.record_unfinished")

    assert sweep > calls.index("dispatch.scheduler.stop")
    assert sweep > calls.index("job_queue.stop")
    assert sweep < calls.index("dispatch.lifecycle.drain")
    assert sweep < calls.index("built_recorder.knowledge_caller.close")


# ---------------------------------------------------------------------------
# KOD-178: the shutdown's own outcome, over the components it drives
# ---------------------------------------------------------------------------

LANE = "lane"
REPO_URL = "https://forge.invalid/owner/repo"
PRE_CLAIM_STATE = "Todo"
MODEL = "fixture-model"

#: The three fires of the measured boot, by the prompt each one carries:
#: one that finishes and records itself, one killed mid-run, one that never
#: leaves the queue.
FINISHED, KILLED, NEVER_RAN = "K-1", "K-2", "K-3"

FIRE_LOG = RecordDestination(
    system=DocumentSystem.KNOWLEDGE,
    name="Fire Log",
    id="fire-log",
    append_only=True,
)

HOLDER = "pass-a"
LEASE_SECONDS = 600.0
RENEWAL_FRACTION = 0.25

#: Queue bounds no case here approaches, and a lane of one so the three
#: fires occupy the three states the shutdown must tell apart.
LANE_CONCURRENCY = 1
LANE_DEPTH = 8
RETENTION_SECONDS = 60.0
BUFFER_CAPACITY = 64

#: Generous: every wait below is on a condition the shipped queue reaches
#: in microseconds, so this only ever bounds a genuine hang.
SETTLE_TIMEOUT = 5.0
SETTLE_POLL = 0.01


class _ScriptedEngine:
    """A workflow engine whose runs finish or hang, by the prompt they carry.

    The shipped queue's own worker is what dequeues a job and publishes its
    first frame, so the three states the shutdown has to tell apart are
    produced here rather than written into a registry by hand.
    """

    def __init__(self, *, finishing: str) -> None:
        self._finishing: str = finishing

    async def _finish(self) -> AsyncIterator[AgentEvent]:
        yield WorkflowCompleteEvent(
            feature_branch="feature",
            ralph_branch="ralph",
            total_iterations=1,
            accepted=True,
            outcome=WorkflowOutcome.ci_passed,
            merged=True,
        )

    async def _hang(self) -> AsyncIterator[AgentEvent]:
        yield AssistantTextEvent(text="working", model=MODEL)
        await asyncio.Event().wait()

    def run(self, *, prompt: str, **_: object) -> AsyncIterator[AgentEvent]:
        return self._finish() if prompt == self._finishing else self._hang()


async def _until(condition: Callable[[], bool]) -> None:
    """Wait for *condition*, bounded — a condition, never a duration."""
    async with asyncio.timeout(SETTLE_TIMEOUT):
        while not condition():
            await asyncio.sleep(SETTLE_POLL)


async def test_the_shutdown_leaves_no_fire_without_its_row() -> None:
    """KOD-178 — the measured boot: three fires ran, the Fire Log held one.

    Driven over the shipped queue, the shipped watcher and the shipped
    recorder, in the lifespan's own shutdown order: the queue stops, the
    registry is swept, the watches are drained.  One fire recorded itself
    at its own end; the other two were still running and still queued, and
    the shutdown owes each of them a row naming its issue and how it ended.
    Exactly one row per fire, because the recorder's verify arm answers per
    run — whichever of the sweep and the drained watch reaches it first.
    """
    engine = _ScriptedEngine(finishing=FINISHED)
    queue = AsyncioJobQueue(
        engine=engine,
        max_concurrent_runs_per_lane=LANE_CONCURRENCY,
        max_depth_per_lane=LANE_DEPTH,
        terminal_retention_seconds=RETENTION_SECONDS,
        event_buffer_retention_seconds=RETENTION_SECONDS,
        event_buffer_capacity=BUFFER_CAPACITY,
    )
    await queue.start()
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue(key) for key in (FINISHED, KILLED, NEVER_RAN)],
    )
    log = RecordingLogSink()
    watch = LifecycleWatcher(
        queue=queue,
        registry=queue,
        writer=TrackerLifecycleWriter(tracker=tracker, gate=PassThroughGate()),
        heartbeat=ClaimHeartbeat(
            tracker=tracker,
            holder=HOLDER,
            lease_seconds=LEASE_SECONDS,
            renewal_fraction=RENEWAL_FRACTION,
        ),
        recorder=RunRecorder(
            records={RunKind.FIRE.value: FIRE_LOG},
            sinks={DocumentSystem.KNOWLEDGE: log},
        ),
        report=FakeFireReport(),
    )

    try:
        for key in (FINISHED, KILLED, NEVER_RAN):
            record = await queue.submit(
                lane=LANE,
                request=WorkflowRequest(prompt=key, repo_url=REPO_URL),
            )
            watch.follow(
                issue_key=key,
                job_id=record.job_id,
                pre_claim_state=PRE_CLAIM_STATE,
            )
        # The first fire finishes and records itself; the second is then
        # dequeued and hangs — its in-progress write IS that dequeue —
        # which leaves the third in the queue behind it.
        await _until(lambda: [row.name for row in log.writes] == [FINISHED])
        await _until(
            lambda: (KILLED, LifecycleStage.IN_PROGRESS) in tracker.workflow_writes,
        )

        with structlog.testing.capture_logs() as logs:
            await queue.stop()
            await watch.record_unfinished()
            await watch.drain()
    finally:
        await queue.stop()

    assert [(row.name, row.outcome) for row in log.writes] == [
        (FINISHED, RunOutcome.COMPLETED),
        (KILLED, RunOutcome.FAILED),
        (NEVER_RAN, RunOutcome.NEVER_STARTED),
    ]
    # Every announcement names a row that was actually written, and the
    # fire that recorded itself is announced by nobody.
    announced = [
        entry["issue_key"]
        for entry in logs
        if entry["event"] == "unfinished_fire_recorded"
    ]
    assert set(announced) <= {KILLED, NEVER_RAN}
    assert len(announced) == len(set(announced))
