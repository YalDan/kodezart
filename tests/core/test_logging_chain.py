"""The configured log chain renders exceptions (KOD-146, defect 2).

Measured on the first live fire: ``job_failed`` carried ``exc_info`` as a
list of reprs — ``["<class '…NoStructuredOutputError'>", "…('…')",
"<traceback object at 0x109ec8f00>"]``.  The traceback OBJECT was printed,
so not one frame survived, and a two-minute engine dispatch left one
sentence to diagnose from.

The cause was located at the chain rather than at any call site: three
sites in the service pass ``exc_info`` and none of them could render,
because ``configure_logging`` installed no exception processor.  These
tests run the REAL chain — the same ``configure_logging`` the lifespan
calls — and read what a log consumer would actually receive.
"""

import asyncio
import io
import json
import logging
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

import structlog

from kodezart.adapters.asyncio_job_queue import AsyncioJobQueue
from kodezart.core.config import AppConfig
from kodezart.core.logging import configure_logging, get_logger
from kodezart.services.pass_scheduler import PassScheduler, ScheduledPass
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.requests.agent import WorkflowRequest
from tests.services.test_pass_scheduler import Metronome

FAILURE = "the creator produced no structured output"
LANE = "tracker"
INTERVAL_SECONDS = 11.5

#: Generous: every wait below is on a condition, so this only ever bounds
#: a genuine hang.  Waiting a fixed number of event-loop yields instead
#: would be a guess about how many turns a failure path costs — the guess
#: that held locally and under-ran on a slower runner.
SETTLE_TIMEOUT = 5.0


@contextmanager
def configured_chain(*, pretty: bool = False) -> Iterator[io.StringIO]:
    """The shipped chain, writing where the test can read it.

    Global state is restored on the way out — the root handlers, the level
    and structlog's own configuration — because every other test in the
    suite runs under structlog's defaults and must keep doing so.
    """
    buffer = io.StringIO()
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_stdout = sys.stdout
    sys.stdout = buffer
    try:
        configure_logging(log_level="INFO", pretty=pretty)
        yield buffer
    finally:
        sys.stdout = saved_stdout
        root.handlers = saved_handlers
        root.setLevel(saved_level)
        structlog.reset_defaults()


def json_events(buffer: io.StringIO, *, event: str) -> list[dict[str, object]]:
    """Every rendered line naming *event*, parsed."""
    found: list[dict[str, object]] = []
    for line in buffer.getvalue().splitlines():
        if not line.startswith("{"):
            continue
        payload = json.loads(line)
        if payload.get("event") == event:
            found.append(payload)
    return found


class RaisingEngine:
    """A ``WorkflowEngine`` whose run raises where the live one did."""

    def run(self, **kwargs: object) -> AsyncIterator[AgentEvent]:
        return self._frames()

    async def _frames(self) -> AsyncIterator[AgentEvent]:
        await asyncio.sleep(0)
        raise RuntimeError(FAILURE)
        yield  # pragma: no cover - unreachable, required to make this a generator


class Exploder:
    """A scheduled pass that always fails."""

    def __init__(self) -> None:
        self.calls: int = 0

    async def run(self) -> None:
        await asyncio.sleep(0)
        self.calls += 1
        raise RuntimeError(FAILURE)


async def drain(queue: AsyncioJobQueue, *, job_id: str) -> None:
    """Read the job's stream to its close.

    The exact wait for "the run is over": the queue closes the stream when
    the run ends, and ``job_failed`` is logged — and therefore written —
    before the error frame that arrives on it.
    """
    async for _ in queue.attach(job_id=job_id):
        pass


def assert_names_its_frames(rendered: object) -> None:
    """A formatted traceback, not a repr of a traceback object."""
    assert isinstance(rendered, str)
    assert rendered.startswith("Traceback (most recent call last):")
    assert "<traceback object at" not in rendered
    assert f"RuntimeError: {FAILURE}" in rendered
    assert "raise RuntimeError(FAILURE)" in rendered


async def test_an_exception_logged_through_the_chain_renders_its_frames() -> None:
    """The chain-level fix, at its smallest: one site, one exception."""
    with configured_chain() as buffer:
        log = get_logger("kodezart.tests.logging_chain")
        try:
            raise RuntimeError(FAILURE)
        except RuntimeError:
            await log.aexception("job_failed", job_id="job-0001")

    (payload,) = json_events(buffer, event="job_failed")
    assert_names_its_frames(payload["exception"])


async def test_the_queues_failure_event_carries_the_traceback() -> None:
    """The measured site: a run that raised inside the engine dispatch."""
    with configured_chain() as buffer:
        config = AppConfig()
        queue = AsyncioJobQueue(
            engine=RaisingEngine(),
            max_concurrent_runs_per_lane=1,
            max_depth_per_lane=config.queue_max_depth_per_lane,
            terminal_retention_seconds=config.queue_terminal_retention_seconds,
            event_buffer_retention_seconds=(
                config.queue_event_buffer_retention_seconds
            ),
            event_buffer_capacity=config.queue_event_buffer_capacity,
        )
        await queue.start()
        try:
            record = await queue.submit(
                lane=LANE,
                request=WorkflowRequest(prompt="do the thing", repo_path="/tmp/fake"),
            )
            await asyncio.wait_for(
                drain(queue, job_id=record.job_id),
                timeout=SETTLE_TIMEOUT,
            )
        finally:
            await queue.stop()

    (payload,) = json_events(buffer, event="job_failed")
    assert payload["error_kind"] == "RuntimeError"
    assert_names_its_frames(payload["exception"])


async def test_the_schedulers_failure_event_carries_the_traceback() -> None:
    """The other site, over the same chain.

    The scheduler formats into its own ``traceback`` key at the call site
    (KOD-145) and passes no ``exc_info``, so its frames appear exactly
    once; the queue's ride the chain processor added here.  Both events
    reach a log consumer naming the frames that produced them, which is
    the property this pair exists to hold.
    """
    exploder = Exploder()
    metronome = Metronome(limit=1)
    with configured_chain() as buffer:
        scheduler = PassScheduler(
            passes=[
                ScheduledPass(
                    name="dispatch",
                    interval_seconds=INTERVAL_SECONDS,
                    run=exploder.run,
                ),
            ],
            sleep=metronome.sleep,
        )
        await scheduler.start()
        # The metronome parks the driver once its budget is gone, and a
        # driver sleeps BEFORE it runs — so the park is requested only
        # after the failing run and its log call have both completed.
        await asyncio.wait_for(metronome.parked.wait(), timeout=SETTLE_TIMEOUT)
        await scheduler.stop()

    (payload,) = json_events(buffer, event="scheduled_pass_failed")
    assert_names_its_frames(payload["traceback"])


async def test_the_console_renderer_also_names_the_frames() -> None:
    """The development deployment sees frames too, in its own shape.

    ``ConsoleRenderer`` draws its own boxed traceback rather than the
    stdlib text one, so this asserts the property both renderers owe — the
    raising frame is named and no traceback object is printed — and not
    the JSON renderer's formatting.
    """
    with configured_chain(pretty=True) as buffer:
        log = get_logger("kodezart.tests.logging_chain")
        try:
            raise RuntimeError(FAILURE)
        except RuntimeError:
            await log.aexception("job_failed", job_id="job-0001")

    rendered = buffer.getvalue()
    assert "test_logging_chain.py" in rendered
    assert "RuntimeError" in rendered
    assert FAILURE in rendered
    assert "<traceback object at" not in rendered
