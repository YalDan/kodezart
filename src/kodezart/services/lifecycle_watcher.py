"""Following a dispatched fire's run, so the lifecycle write-back happens.

``TrackerLifecycleWriter`` states WHAT each transition writes.  This states
WHEN: it reads one job's event stream — the same stream an HTTP client
attaches to — and calls the writer as the run reaches each stage.  Without
this the writer is a capability nothing invokes, and the issue a pass
claimed sits in its pre-fire state for the whole run.

**Why the first frame is the in-progress signal.**  ``JobQueue.attach``
replays a job's buffer and then streams it, and a job that has not been
dequeued has produced nothing to replay: a frame exists only because a
worker took the job and started the run.  The arrival of a first frame IS
therefore the dequeue, so attaching at enqueue time and waiting is exact —
no polling interval, no second surface to watch.  The property is a
contract of the port, and it is asserted against the shipped queue adapter
in ``tests/services/test_lifecycle_watcher.py``.

The stream is the only input.  Nothing here reads a pull request's state or
a forge API: an open pull request is observed as ``WorkflowPREvent`` and a
verified merge as ``WorkflowCompleteEvent.merged``, both produced by the
workflow that did the work.

**The failure arm.**  A stream that ends without a ``WorkflowCompleteEvent``
is a run that reached no terminal outcome, and the last thing the tracker
was told — the in-progress stage — is contradicted by reality with nothing
saying so.  The end of the stream is the exact signal: the queue closes it
whether the run finished or raised, so no timeout and no second surface is
involved (KOD-146).

**The claim heartbeat rides here** for the same reason (KOD-147).  A claim
has to stay live for exactly as long as the job does, and this watch is the
one component whose lifetime already IS the job's: it begins when the
dispatch pass enqueues, it ends when the stream ends, and it ends by both
paths.  The renewal starts before the first frame rather than after it,
because a job sitting in the queue longer than the lease loses its issue
just as surely as a job running longer than one.
"""

import asyncio

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import JobQueue
from kodezart.services.claim_heartbeat import ClaimHeartbeat
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.agent import (
    AgentEvent,
    ErrorEvent,
    WorkflowCompleteEvent,
    WorkflowPREvent,
)


class LifecycleWatcher:
    """Drives one issue's lifecycle write-back off its job's event stream."""

    def __init__(
        self,
        *,
        queue: JobQueue,
        writer: TrackerLifecycleWriter,
        heartbeat: ClaimHeartbeat,
    ) -> None:
        self._queue: JobQueue = queue
        self._writer: TrackerLifecycleWriter = writer
        self._heartbeat: ClaimHeartbeat = heartbeat
        self._following: set[asyncio.Task[None]] = set()
        self._log: BoundLogger = get_logger(__name__)

    def follow(self, *, issue_key: str, job_id: str, pre_claim_state: str) -> None:
        """Watch *job_id* in the background, for the life of the run.

        The dispatch pass that calls this returns immediately — a tick may
        not block for the run it started, or the next tick never happens.
        The task reference is held here so the loop cannot collect a watch
        mid-run and drop the transitions it had left to write.

        ``pre_claim_state`` rides in rather than being read: by the time
        a run has failed the tracker's copy holds the in-progress stage,
        so the only reading of the state the issue held BEFORE the claim
        is the one the dispatch pass already took, in the scan that
        selected it.
        """
        task = asyncio.create_task(
            self.watch(
                issue_key=issue_key,
                job_id=job_id,
                pre_claim_state=pre_claim_state,
            ),
        )
        self._following.add(task)
        task.add_done_callback(self._following.discard)

    @property
    def following(self) -> frozenset[asyncio.Task[None]]:
        """The watches currently in flight."""
        return frozenset(self._following)

    async def watch(
        self,
        *,
        issue_key: str,
        job_id: str,
        pre_claim_state: str,
    ) -> None:
        """Read the job's stream to its end, writing each stage as it arrives."""
        started = False
        terminal = False
        failure: ErrorEvent | None = None
        async with self._heartbeat.renewing(issue_key=issue_key):
            async for event in self._queue.attach(job_id=job_id):
                if not started:
                    started = True
                    await self._writer.on_dequeue(issue_key=issue_key)
                if isinstance(event, WorkflowCompleteEvent):
                    terminal = True
                if isinstance(event, ErrorEvent):
                    failure = event
                await self._apply(issue_key=issue_key, job_id=job_id, event=event)
            # A run that never started moved nothing, so there is nothing to
            # put back: the failure arm answers for a run that WAS dequeued —
            # the write that moved it to the in-progress stage — and that
            # reached no terminal outcome.
            if started and not terminal:
                await self._writer.on_run_failed(
                    issue_key=issue_key,
                    job_id=job_id,
                    pre_claim_state=pre_claim_state,
                    failure_class=None if failure is None else failure.error_kind,
                    step=None if failure is None else failure.raise_site,
                )
        await self._log.ainfo(
            "lifecycle_watch_finished",
            issue_key=issue_key,
            job_id=job_id,
            run_started=started,
            terminal_outcome=terminal,
        )

    async def _apply(
        self,
        *,
        issue_key: str,
        job_id: str,
        event: AgentEvent,
    ) -> None:
        if isinstance(event, WorkflowPREvent):
            await self._writer.on_pull_request(issue_key=issue_key)
            return
        if isinstance(event, WorkflowCompleteEvent):
            # Order matters: the terminal comment reports the outcome of a
            # run whose state transitions have already landed, so a reader
            # who sees the comment never sees a stale state beside it.
            if event.merged:
                await self._writer.on_verified_merge(issue_key=issue_key)
            await self._writer.on_terminal_outcome(
                issue_key=issue_key,
                job_id=job_id,
                outcome=event.outcome,
            )
