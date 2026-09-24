"""In-process asyncio queue + dispatcher — the only execution entry point.

NOT PERSISTENT.  The queue lives in the serving process: a restart drops
every job still waiting and terminates every job in flight.  An
HTTP-submitted fire lost to a restart is re-submitted by its caller —
documented behavior, not a silent one, and no persistence machinery
stands behind it.

Concurrency is enforced by worker count and nothing else: N tasks pull
from one ``asyncio.Queue`` per lane, where N is the configured
per-lane concurrency.  There is no semaphore and no lock; the configured
default of 1 is the only thing making runs serial.

A job may be given a time limit.  With one configured, a run still going
when it expires is cancelled and its job ends ``job_timed_out``, so a
session stuck on a stream that never ends frees its lane instead of
holding it until a restart.  Unset, a run has no limit at all.
"""

import asyncio
import uuid
from collections import deque
from collections.abc import AsyncIterator, Coroutine, Sequence
from datetime import UTC, datetime

from kodezart.adapters.job_registry import InMemoryJobRegistry
from kodezart.core.constants import DEFAULT_LANE
from kodezart.core.error_egress import build_error_event
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import WorkflowEngine
from kodezart.domain.errors import QueueFullError
from kodezart.types.domain.agent import AgentEvent, WorkflowCompleteEvent
from kodezart.types.domain.job import JobRecord, JobState
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.scope_terminal import ScopeTerminalEvent
from kodezart.types.domain.workflow import WorkflowSubmission


class _JobStream:
    """Bounded replay buffer plus live fan-out for one job."""

    def __init__(self, capacity: int) -> None:
        self._buffer: deque[AgentEvent] = deque(maxlen=capacity)
        self._subscribers: list[asyncio.Queue[AgentEvent | None]] = []
        self._closed: bool = False

    @property
    def closed(self) -> bool:
        return self._closed

    def publish(self, event: AgentEvent) -> bool:
        """Append *event*, returning True when it evicted the oldest frame."""
        dropped = len(self._buffer) == self._buffer.maxlen
        self._buffer.append(event)
        for subscriber in self._subscribers:
            subscriber.put_nowait(event)
        return dropped

    def close(self) -> None:
        self._closed = True
        for subscriber in self._subscribers:
            subscriber.put_nowait(None)

    def discard_buffer(self) -> int:
        """Release every buffered frame, returning how many were dropped.

        Frees the expensive part of a terminal job — its frames — while
        the cheap record stays addressable for its own, longer window.
        """
        dropped = len(self._buffer)
        self._buffer.clear()
        return dropped

    async def stream(self) -> AsyncIterator[AgentEvent]:
        """Replay the buffer, then go live until the job reaches TERMINAL."""
        replay = list(self._buffer)
        if self._closed:
            for event in replay:
                yield event
            return
        subscriber: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self._subscribers.append(subscriber)
        try:
            for event in replay:
                yield event
            while True:
                item = await subscriber.get()
                if item is None:
                    return
                yield item
        finally:
            self._subscribers.remove(subscriber)


class _Lane:
    """One lane's queue, arrival order and worker tasks."""

    def __init__(self, max_depth: int) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max_depth)
        self.pending: list[str] = []
        self.workers: list[asyncio.Task[None]] = []


class AsyncioJobQueue:
    """Satisfies both ``JobQueue`` and ``JobRegistry``.

    The records it writes live in an object built BEFORE it, and it delegates
    both reads of the role to that object: a scope run's entry reads liveness
    from the same store while this queue is in the middle of calling the
    engine that reaches it, and a store owned by the queue could not be
    handed to the engine the queue is constructed from.

    NOT PERSISTENT.  The queue lives in the serving process: a restart
    drops every job still waiting and terminates every job in flight.
    An HTTP-submitted fire lost to a restart is re-submitted by its
    caller — documented behavior, not a silent one.

    A terminal job is retained on TWO independent windows, because its
    two parts cost different amounts: ``terminal_retention_seconds``
    governs the ~1-2 KB ``JobRecord``, while the far shorter
    ``event_buffer_retention_seconds`` governs the replay buffer, whose
    frames run to megabytes.  Dropping the buffer marks the record
    ``truncated`` — the same flag overflow sets, because it is the same
    fact: frames a client can no longer replay.
    """

    def __init__(
        self,
        *,
        engine: WorkflowEngine,
        max_concurrent_runs_per_lane: int,
        max_depth_per_lane: int,
        terminal_retention_seconds: float,
        event_buffer_retention_seconds: float,
        event_buffer_capacity: int,
        registry: InMemoryJobRegistry | None = None,
        run_timeout_seconds: float | None = None,
    ) -> None:
        self._engine: WorkflowEngine = engine
        self._run_timeout_seconds: float | None = run_timeout_seconds
        self._max_concurrent_runs_per_lane: int = max_concurrent_runs_per_lane
        self._max_depth_per_lane: int = max_depth_per_lane
        self._terminal_retention_seconds: float = terminal_retention_seconds
        self._event_buffer_retention_seconds: float = event_buffer_retention_seconds
        self._event_buffer_capacity: int = event_buffer_capacity
        self._lanes: dict[str, _Lane] = {}
        #: Public, and read-only by convention: it is what a test enumerates
        #: and what a scope run's entry was handed before this queue existed.
        self.registry: InMemoryJobRegistry = (
            InMemoryJobRegistry() if registry is None else registry
        )
        self._requests: dict[str, WorkflowSubmission] = {}
        self._streams: dict[str, _JobStream] = {}
        self._evictions: set[asyncio.Task[None]] = set()
        self._accepting: bool = False
        self._log: BoundLogger = get_logger(__name__)

    # -- Lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Open the dispatcher for submissions and spawn the default lane."""
        self._accepting = True
        if self._max_concurrent_runs_per_lane > 1:
            await self._log.awarning(
                "job_queue_concurrency_above_one",
                max_concurrent_runs_per_lane=self._max_concurrent_runs_per_lane,
                detail=(
                    "Runs in the same lane will interleave; branch and worktree "
                    "contention is the operator's to accept."
                ),
            )
        self._ensure_lane(DEFAULT_LANE)

    async def stop(self) -> None:
        """Stop accepting, cancel workers, mark in-flight jobs TERMINAL.

        Jobs still queued at shutdown are dropped — the documented
        durability stance, made observable.  The sweep writes
        ``shutdown_abandoned`` on every record it moves, in flight or
        still waiting: both leave nothing to resume, and this is the one
        path that publishes no event at all, so the record is the only
        place the fate can be read.
        """
        self._accepting = False
        workers = [worker for lane in self._lanes.values() for worker in lane.workers]
        for worker in workers:
            worker.cancel()
        for eviction in self._evictions:
            eviction.cancel()
        for outcome in await asyncio.gather(*workers, return_exceptions=True):
            if isinstance(outcome, BaseException) and not isinstance(
                outcome,
                asyncio.CancelledError,
            ):
                await self._log.aerror(
                    "job_queue_worker_failed",
                    error=str(outcome),
                    error_kind=type(outcome).__name__,
                )
        self._evictions.clear()
        for lane in self._lanes.values():
            lane.workers.clear()
            lane.pending.clear()
        abandoned: list[str] = []
        for record in self.registry.open():
            self.registry.amend(
                record.job_id,
                state=JobState.TERMINAL,
                queue_position=None,
                outcome=WorkflowOutcome.shutdown_abandoned,
            )
            abandoned.append(record.job_id)
            stream = self._streams.get(record.job_id)
            if stream is not None:
                stream.close()
        await self._log.ainfo(
            "job_queue_stopped",
            lanes=len(self._lanes),
            abandoned=len(abandoned),
            outcome=WorkflowOutcome.shutdown_abandoned.value,
        )

    # -- JobQueue ------------------------------------------------------------

    async def submit(self, *, lane: str, request: WorkflowSubmission) -> JobRecord:
        """Enqueue *request* on *lane*. Raises ``QueueFullError`` at capacity."""
        if not self._accepting:
            msg = f"lane {lane!r} is not accepting submissions"
            raise QueueFullError(msg)
        runtime = self._ensure_lane(lane)
        job_id = uuid.uuid4().hex
        record = JobRecord(
            job_id=job_id,
            lane=lane,
            state=JobState.QUEUED,
            queue_position=len(runtime.pending) + 1,
            submitted_at=datetime.now(tz=UTC),
            scope=request.scope,
        )
        try:
            runtime.queue.put_nowait(job_id)
        except asyncio.QueueFull as exc:
            msg = (
                f"lane {lane!r} is at capacity "
                f"({self._max_depth_per_lane} queued submissions)"
            )
            raise QueueFullError(msg) from exc
        runtime.pending.append(job_id)
        self.registry.add(record)
        self._requests[job_id] = request
        self._streams[job_id] = _JobStream(self._event_buffer_capacity)
        await self._log.ainfo(
            "job_submitted",
            job_id=job_id,
            lane=lane,
            queue_position=record.queue_position,
        )
        return record

    def attach(self, *, job_id: str) -> AsyncIterator[AgentEvent]:
        """Replay the job's bounded event buffer, then stream live events."""
        stream = self._streams.get(job_id)
        if stream is None:
            msg = f"unknown job: {job_id}"
            raise KeyError(msg)
        return stream.stream()

    # -- JobRegistry ---------------------------------------------------------

    async def get(self, *, job_id: str) -> JobRecord | None:
        """The job's current record, or ``None`` when unknown or evicted."""
        return await self.registry.get(job_id=job_id)

    async def live_for_scope(self, *, scope: ScopeRef) -> Sequence[JobRecord]:
        """Every job addressed at *scope* that is not TERMINAL, oldest first."""
        return await self.registry.live_for_scope(scope=scope)

    # -- Dispatcher internals ------------------------------------------------

    def _ensure_lane(self, lane: str) -> _Lane:
        runtime = self._lanes.get(lane)
        if runtime is not None:
            return runtime
        runtime = _Lane(self._max_depth_per_lane)
        self._lanes[lane] = runtime
        for _ in range(self._max_concurrent_runs_per_lane):
            runtime.workers.append(asyncio.create_task(self._worker(lane)))
        return runtime

    async def _worker(self, lane: str) -> None:
        runtime = self._lanes[lane]
        while True:
            job_id = await runtime.queue.get()
            try:
                await self._run_job(lane, job_id)
            finally:
                runtime.queue.task_done()

    async def _run_job(self, lane: str, job_id: str) -> None:
        runtime = self._lanes[lane]
        if job_id in runtime.pending:
            runtime.pending.remove(job_id)
        self._reindex(lane)
        record = self.registry.amend(
            job_id, state=JobState.RUNNING, queue_position=None
        )
        request = self._requests.pop(job_id)
        await self._log.ainfo("job_started", job_id=job_id, lane=lane)

        outcome: WorkflowOutcome | None = None
        # A limit of ``None`` sets no deadline at all, which is the queue as
        # it was before the limit existed.  Whether the limit ended the run
        # is asked of the deadline itself, so a ``TimeoutError`` the engine
        # raises on its own stays an engine error.
        deadline = asyncio.timeout(self._run_timeout_seconds)
        try:
            async with deadline:
                async for event in self._engine.run(
                    prompt=request.prompt,
                    issue_key=request.issue_key,
                    run_identity=(
                        RunIdentity(
                            kind=RunKind.FIRE,
                            name=request.issue_key,
                            started_at=record.submitted_at,
                        )
                        if request.issue_key is not None
                        else None
                    ),
                    repo_path=request.repo_path,
                    repo_url=request.repo_url,
                    base_spec=request.base_spec,
                    scope=request.scope,
                    implied_base=request.implied_base,
                    permission_mode=request.permission_mode,
                    allowed_tools=request.allowed_tools,
                    cache_key=job_id,
                ):
                    await self._publish(job_id, event)
                    if isinstance(event, WorkflowCompleteEvent | ScopeTerminalEvent):
                        outcome = event.outcome
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if deadline.expired():
                outcome = WorkflowOutcome.job_timed_out
                await self._time_out(job_id, lane)
            else:
                # The run raised before it could classify itself, so the
                # queue names the fate it observed.  Without this the record
                # reaches TERMINAL with a null outcome, which is also what a
                # shutdown sweep and a clean-but-silent run leave behind —
                # three different fates read as one absence.
                outcome = WorkflowOutcome.engine_error
                await self._log.aexception(
                    "job_failed",
                    job_id=job_id,
                    lane=lane,
                    error=str(exc),
                    error_kind=type(exc).__name__,
                )
                await self._publish(job_id, build_error_event(exc))
        await self._finish(job_id, lane, outcome)

    async def _time_out(self, job_id: str, lane: str) -> None:
        """Say that the limit ended the run: in the log, and on the stream.

        The error frame is what a client attached to the job reads, and what
        the lifecycle watch of a fire reads as the class the run died of, so
        the tracker's failure note names the timeout rather than no class.
        """
        await self._log.aerror(
            "job_timed_out",
            job_id=job_id,
            lane=lane,
            run_timeout_seconds=self._run_timeout_seconds,
        )
        reason = TimeoutError(
            f"job ran past the queue's run time limit of "
            f"{self._run_timeout_seconds} seconds and was cancelled"
        )
        await self._publish(job_id, build_error_event(reason))

    async def _publish(self, job_id: str, event: AgentEvent) -> None:
        stream = self._streams[job_id]
        if not stream.publish(event):
            return
        if self.registry.records[job_id].truncated:
            return
        self.registry.amend(job_id, truncated=True)
        await self._log.awarning(
            "job_event_buffer_truncated",
            job_id=job_id,
            capacity=self._event_buffer_capacity,
        )

    async def _finish(
        self,
        job_id: str,
        lane: str,
        outcome: WorkflowOutcome | None,
    ) -> None:
        self.registry.amend(
            job_id,
            state=JobState.TERMINAL,
            queue_position=None,
            outcome=outcome,
        )
        self._streams[job_id].close()
        await self._log.ainfo(
            "job_finished",
            job_id=job_id,
            lane=lane,
            outcome=outcome.value if outcome is not None else None,
        )
        self._schedule(self._drop_buffer_later(job_id))
        self._schedule(self._drop_record_later(job_id))

    def _schedule(self, coroutine: Coroutine[object, object, None]) -> None:
        eviction = asyncio.create_task(coroutine)
        self._evictions.add(eviction)
        eviction.add_done_callback(self._evictions.discard)

    async def _drop_buffer_later(self, job_id: str) -> None:
        """Release the job's frames on the buffer's own, shorter window."""
        await asyncio.sleep(self._event_buffer_retention_seconds)
        stream = self._streams.get(job_id)
        if stream is None:
            return
        dropped = stream.discard_buffer()
        if dropped == 0:
            return
        record = await self.registry.get(job_id=job_id)
        if record is not None and not record.truncated:
            self.registry.amend(job_id, truncated=True)
        await self._log.ainfo(
            "job_event_buffer_dropped",
            job_id=job_id,
            frames=dropped,
            retention_seconds=self._event_buffer_retention_seconds,
        )

    async def _drop_record_later(self, job_id: str) -> None:
        """Evict the record itself, so the registry cannot grow unbounded."""
        await asyncio.sleep(self._terminal_retention_seconds)
        self.registry.forget(job_id)
        self._streams.pop(job_id, None)

    def _reindex(self, lane: str) -> None:
        for position, pending_id in enumerate(self._lanes[lane].pending, start=1):
            record = self.registry.records.get(pending_id)
            if record is not None and record.state is JobState.QUEUED:
                self.registry.amend(pending_id, queue_position=position)
