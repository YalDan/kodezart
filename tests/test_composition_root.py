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
import inspect
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path

import pytest
import structlog.testing
from pydantic import SecretStr
from structlog.typing import EventDict

from kodezart.adapters.asyncio_job_queue import AsyncioJobQueue
from kodezart.adapters.http_mcp_tool_caller import HttpMcpToolCaller
from kodezart.composition.passes import (
    build_dispatch_runtime,
    build_gate,
    build_prompt_passes,
)
from kodezart.composition.records import _knowledge_caller
from kodezart.composition.tracker import DialledTracker, make_mcp_tool_caller
from kodezart.core.config import AppConfig
from kodezart.core.errors import McpSessionClosedError
from kodezart.services.claim_heartbeat import ClaimHeartbeat
from kodezart.services.lifecycle_watcher import LifecycleWatcher
from kodezart.services.run_recorder import RunRecorder
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.agent import (
    AgentEvent,
    AssistantTextEvent,
    WorkflowCompleteEvent,
)
from kodezart.types.domain.dispatch import PassSignal
from kodezart.types.domain.operation import (
    DocumentSystem,
    LifecycleStage,
    RecordDestination,
    RunKind,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_records import RunOutcome, RunRecord
from kodezart.types.domain.tracker import TrackerIssue
from kodezart.types.requests.agent import WorkflowRequest
from tests.fakes import (
    FakeFireReport,
    FakeTrackerPort,
    ManagedFakeLinearMcpServer,
    PassThroughGate,
    RecordingLogSink,
    make_tracker_issue,
)
from tests.services.test_prompt_pass import example_config

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
    2026-09-02). After the drain, because that is when nothing records
    beside it: a watch ending on the stopped stream verifies the log and
    then writes, exactly as the sweep does, and the two interleaved over
    one run are two rows. And before the knowledge session closes, because
    that session is what the rows are written through.
    """
    calls = _lifespan_calls()
    sweep = calls.index("dispatch.lifecycle.record_unfinished")

    assert sweep > calls.index("dispatch.scheduler.stop")
    assert sweep > calls.index("job_queue.stop")
    assert sweep > calls.index("dispatch.lifecycle.drain")
    assert sweep < calls.index("built_recorder.knowledge_caller.close")


# ---------------------------------------------------------------------------
# KOD-289: a tracker and its self-write ledger are one value, or neither
# ---------------------------------------------------------------------------


def _build_dispatch_runtime_keywords() -> set[str]:
    """The keywords the root hands :func:`build_dispatch_runtime`."""
    tree = ast.parse(ROOT.read_text(encoding="utf-8"))
    (call,) = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_dispatch_runtime"
    ]
    return {keyword.arg for keyword in call.keywords if keyword.arg is not None}


class TestATrackerTravelsWithItsOwnWriteLedger:
    """Half a tracker is refused by name, never served as a working one.

    Measured at ``084f762``: the port and the ledger of this process's own
    writes arrived as two nullable arguments, so a ledger missing beside a
    live port made :func:`build_gate` return ``None`` — every prompt pass
    ran ungated at full session cost — and made
    :func:`build_dispatch_runtime` skip every dispatch pass, while the log
    it wrote said the tracker was present (KOD-289).
    """

    def test_a_gate_asked_for_without_the_ledger_is_refused_by_name(self) -> None:
        """The missing half is named, where it used to be a silent ``None``.

        Called through an untyped reference, because the refusal under test
        is what a caller MEETS: the type checker refuses this call outright,
        and the run refuses it too rather than building a gate that cannot
        recognise its own writes.
        """
        builder: Callable[..., object] = build_gate

        with pytest.raises(TypeError, match="ledger"):
            builder(
                config=AppConfig(),
                tracker=FakeTrackerPort(),
                signals=[PassSignal.approved_changed],
                team_keys=["KOD"],
                repo_urls=[REPO_URL],
            )

    def test_a_dialled_tracker_without_its_ledger_is_refused_by_name(self) -> None:
        """The one value the runtime takes cannot be built as half of itself.

        :func:`build_dispatch_runtime` takes the tracker only as a
        :class:`DialledTracker`, so "tracker present, ledger absent" is a
        construction of that value — and it is refused where it is
        attempted, naming the half that is missing.
        """
        factory: Callable[..., object] = DialledTracker

        with pytest.raises(TypeError, match="ledger"):
            factory(
                tracker=FakeTrackerPort(),
                caller=ManagedFakeLinearMcpServer(),
                operation=example_config(),
            )

    def test_the_pair_reaches_the_runtime_as_one_value(self) -> None:
        """No builder takes a ledger the boot could forget to pass.

        The two halves are one fact, so they travel as the one value boot
        produced: there is no argument left for half of a tracker to arrive
        in.
        """
        runtime = inspect.signature(build_dispatch_runtime).parameters
        prompt = inspect.signature(build_prompt_passes).parameters

        assert "ledger" not in runtime
        assert "tracker" not in runtime
        assert "ledger" not in prompt
        assert "tracker" not in prompt
        assert runtime["dialled"].annotation == DialledTracker | None
        assert prompt["dialled"].annotation == DialledTracker | None

    def test_the_composition_root_hands_the_tracker_over_whole(self) -> None:
        """The boot's own call, read off the root: one value, not two halves."""
        keywords = _build_dispatch_runtime_keywords()

        assert "dialled" in keywords
        assert "ledger" not in keywords
        assert "tracker" not in keywords

    def test_a_pass_that_declares_no_signal_is_the_one_ungated_arm(self) -> None:
        """The paired positive and the only remaining absence.

        A whole tracker plus a declared signal builds a gate; the same
        tracker with no signal declared builds none, which is the pass
        saying it wants none rather than the wiring losing one.
        """
        tracker = FakeTrackerPort()
        built = build_gate(
            config=AppConfig(),
            tracker=tracker,
            ledger=tracker.self_writes,
            signals=[PassSignal.approved_changed],
            team_keys=["KOD"],
            repo_urls=[REPO_URL],
        )

        assert built is not None
        assert (
            build_gate(
                config=AppConfig(),
                tracker=tracker,
                ledger=tracker.self_writes,
                signals=[],
                team_keys=["KOD"],
                repo_urls=[REPO_URL],
            )
            is None
        )


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


class _TrackerGoneAtShutdown(FakeTrackerPort):
    """A tracker whose session is gone by the time one failure arm writes.

    The measured class (KOD-177), met by the one watch whose put-back
    needs the tracker at shutdown: that watch raises on its way to its own
    record, which is the fire a drain alone cannot account for and the one
    the sweep exists for.  Every other write still lands.
    """

    def __init__(self, *, issues: Sequence[TrackerIssue], gone_for: str) -> None:
        super().__init__(issues=issues)
        self._gone_for: str = gone_for

    async def restore_workflow_state(
        self,
        *,
        issue_key: str,
        state_name: str,
    ) -> TrackerIssue:
        if issue_key == self._gone_for:
            raise McpSessionClosedError(
                "the tracker session is gone",
                server_name="fixture-tracker",
                tool_name="save_issue",
            )
        return await super().restore_workflow_state(
            issue_key=issue_key,
            state_name=state_name,
        )


async def _shutdown(
    tracker: FakeTrackerPort,
) -> tuple[list[RunRecord], list[EventDict]]:
    """The measured boot, then the lifespan's own shutdown, over *tracker*.

    Three fires through the shipped queue, watcher and recorder into a Fire
    Log double: the first finishes and records itself, the second is
    dequeued and hangs, the third waits behind it.  Then the shutdown in
    the lifespan's order — the queue stops, the watches drain, the registry
    is swept — answered as the rows the log holds and the events emitted
    on the way out.
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
            await watch.drain()
            await watch.record_unfinished()
    finally:
        await queue.stop()

    return log.writes, logs


def _rows_by_fire(rows: Sequence[RunRecord]) -> dict[str, RunOutcome]:
    return {row.name: row.outcome for row in rows}


async def test_the_shutdown_leaves_no_fire_without_its_row() -> None:
    """KOD-178 — the measured boot: three fires ran, the Fire Log held one.

    The killed fire's watch meets a tracker whose session is gone and
    raises on its way to its own record — the one fire the drain cannot
    account for.  The sweep gives it its row, after the drain and through
    the same recorder, and announces exactly that row: the never-started
    fire's watch reached its own end and recorded, so the sweep finds that
    row and says nothing about it.  One row per fire, each naming its
    issue and how it ended.
    """
    tracker = _TrackerGoneAtShutdown(
        issues=[make_tracker_issue(key) for key in (FINISHED, KILLED, NEVER_RAN)],
        gone_for=KILLED,
    )

    rows, logs = await _shutdown(tracker)

    assert _rows_by_fire(rows) == {
        FINISHED: RunOutcome.COMPLETED,
        KILLED: RunOutcome.FAILED,
        NEVER_RAN: RunOutcome.NEVER_STARTED,
    }
    assert len(rows) == len(_rows_by_fire(rows))
    # The sweep's row is the last to land: it runs once the drain is over.
    assert rows[-1].name == KILLED
    assert [
        entry["error_kind"]
        for entry in logs
        if entry["event"] == "lifecycle_watch_failed"
    ] == [McpSessionClosedError.__name__]
    assert [
        (entry["issue_key"], entry["outcome"])
        for entry in logs
        if entry["event"] == "unfinished_fire_recorded"
    ] == [(KILLED, RunOutcome.FAILED.value)]


async def test_a_shutdown_whose_watches_all_record_is_announced_by_nobody() -> None:
    """The paired case: every watch reaches its end on the stopped stream.

    Each records its own fire at its end and forgets it, so the sweep that
    follows the drain has no fire left to ask about — one row per fire,
    nothing verified a second time, and no announcement of a row the sweep
    did not write.
    """
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue(key) for key in (FINISHED, KILLED, NEVER_RAN)],
    )

    rows, logs = await _shutdown(tracker)

    assert _rows_by_fire(rows) == {
        FINISHED: RunOutcome.COMPLETED,
        KILLED: RunOutcome.FAILED,
        NEVER_RAN: RunOutcome.NEVER_STARTED,
    }
    assert len(rows) == len(_rows_by_fire(rows))
    assert [
        entry["event"]
        for entry in logs
        if entry["event"] in {"run_record_verified", "unfinished_fire_recorded"}
    ] == []


class TestBothTransportsReadOnTheirOwnConfiguredBound:
    """One field per constructing composition, wired by that composition.

    Measured at `d842513` (KOD-299): the stream's read bound came from a
    private vendor constant, so both HTTP callers ran on a number no
    deployment could state and neither composition passed anything.
    """

    #: The credential shape boot accepts; its value is never presented
    #: here, because nothing in these cases opens a session.
    FIXTURE_TOKEN = "lin_api_" + "T" * 40

    TRACKER_BOUND = 111.0
    KNOWLEDGE_BOUND = 222.0

    def _config(self) -> AppConfig:
        return AppConfig(
            tracker_mcp_sse_read_timeout_seconds=self.TRACKER_BOUND,
            knowledge_mcp_sse_read_timeout_seconds=self.KNOWLEDGE_BOUND,
            knowledge_mcp_server_url="https://knowledge.invalid/mcp",
            knowledge_mcp_token=SecretStr("ntn_" + "K" * 44),
        )

    def test_the_tracker_composition_passes_its_field(self) -> None:
        caller = make_mcp_tool_caller(config=self._config(), token=self.FIXTURE_TOKEN)

        assert isinstance(caller, HttpMcpToolCaller)
        assert caller._sse_read_timeout_seconds == self.TRACKER_BOUND

    def test_the_knowledge_composition_passes_its_own(self) -> None:
        """The paired positive: two transports, two fields, no sharing.

        One number for both would make a knowledge server that streams
        slowly a reason to loosen the tracker's bound.
        """
        caller = _knowledge_caller(self._config(), ["records.fire_prep"])

        assert isinstance(caller, HttpMcpToolCaller)
        assert caller._sse_read_timeout_seconds == self.KNOWLEDGE_BOUND
