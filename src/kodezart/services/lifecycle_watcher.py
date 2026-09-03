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
workflow that did the work.  Whether that pull request DELIVERED the issue
is likewise read off the event rather than inferred: the workflow opens one
on the accepted path and one on the stall exit, and only the first is a
deliverable.

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

**And the claim is handed back here** (KOD-152), because this is where the
job's end is known.  The end of the stream is that end by every path the
process survives — a terminal outcome, a run that reached none, and a
graceful shutdown, which closes the stream of everything still queued or
running — so ONE release, after the heartbeat has stopped, answers for all
three and cannot race a renewal writing the marker back.  A process that
DIES releases nothing, which is the arm the lease expiry exists for and the
one arm that may not change; the measured incident is the other one, an
instance stopped and its replacement locked out of the issue for the rest
of a lease nobody was working under.

**And the run record has a shutdown half** (KOD-178).  Recording at the
watch's end means a fire that is still queued or still running when the
process goes down is recorded nowhere at all: three fires ran on the
measured boot and the Fire Log held one line.  So the fires this process
started are remembered until their watches record them, and
:meth:`LifecycleWatcher.record_unfinished` gives the rest their row on the
way out — after the queue has stopped and the watches have drained, so no
run finishes underneath it and nothing records beside it — off this
watcher's own memory of which of them ever began.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Self

from kodezart.core.errors import RunRecordWriteError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import JobQueue, JobRegistry
from kodezart.services.claim_heartbeat import ClaimHeartbeat
from kodezart.services.run_recorder import RunRecorder
from kodezart.services.tracker_lifecycle import TrackerLifecycleWriter
from kodezart.types.domain.agent import (
    AgentEvent,
    ErrorEvent,
    WorkflowCompleteEvent,
    WorkflowPREvent,
)
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.run_records import (
    RunOutcome,
    RunRecord,
    RunRecordResult,
)

#: How a finished fire's outcome reaches the pass that started it: the
#: issue, how the run ended, and the class it died of when an error frame
#: named one.  Shaped after ``ScheduledPass.report`` — the seam a pass's
#: own outcome already travels — because a fire's end is the same kind of
#: fact, and a second shape for it would be a second vocabulary to keep in
#: parity with the first.
type FireReport = Callable[[str, RunOutcome, str | None], Awaitable[None]]


@dataclass(frozen=True)
class _UnrecordedFire:
    """A fire this process started, until something records it.

    ``dequeued`` is the watcher's OWN memory of the job's first frame, and
    it is here because the registry stops being able to answer: stopping
    the queue marks every job it holds terminal, and the sweep reads after
    that stop.  Whether a run was dequeued is the whole difference between
    ``failed`` and ``never_started``, so it is remembered where it is
    observed rather than inferred later (KOD-178).
    """

    issue_key: str
    dequeued: bool

    def running(self) -> Self:
        """The same fire, with its dequeue remembered."""
        return replace(self, dequeued=True)


def _fire_outcome(*, started: bool, terminal: bool) -> RunOutcome:
    """How the run ended, from the two facts its stream carries.

    Whether it was ever dequeued, and whether it reached a terminal
    outcome — nothing else is known here, and nothing else is needed:
    the three ends those two facts partition are exactly the three a fire
    can have.
    """
    if terminal:
        return RunOutcome.COMPLETED
    return RunOutcome.FAILED if started else RunOutcome.NEVER_STARTED


class LifecycleWatcher:
    """Drives one issue's lifecycle write-back off its job's event stream."""

    def __init__(
        self,
        *,
        queue: JobQueue,
        registry: JobRegistry,
        writer: TrackerLifecycleWriter,
        heartbeat: ClaimHeartbeat,
        recorder: RunRecorder,
        report: FireReport,
    ) -> None:
        self._queue: JobQueue = queue
        #: Read only at shutdown, and only about fires this process
        #: started: what the sweep needs from it is when each job was
        #: submitted, the left edge of the window its row is verified in
        #: (KOD-178).
        self._registry: JobRegistry = registry
        self._writer: TrackerLifecycleWriter = writer
        self._heartbeat: ClaimHeartbeat = heartbeat
        self._recorder: RunRecorder = recorder
        #: Where a finished fire's outcome goes — the dispatchers firing
        #: onto this lane, so a run that died is remembered instead of
        #: being re-selected whole at the next tick (KOD-174).
        self._report: FireReport = report
        self._following: set[asyncio.Task[None]] = set()
        #: Watches that ended by RAISING, captured off each task the
        #: moment it finishes.  Retrieving the exception here is what
        #: keeps a raising watch from ending as an unretrieved task
        #: exception, and holding it for the drain is what keeps a
        #: watch that finished — and so pruned itself from the
        #: in-flight set — BEFORE the drain from being missed by a
        #: drain that gathers only what is still in flight (KOD-303).
        self._raised: list[BaseException] = []
        #: Every fire this process started, by job, until its watch has
        #: recorded it.  A watch records at its END, so a shutdown that
        #: arrives first — or a watch that raises on the way — leaves the
        #: run with no row at all, and the measured boot's Fire Log held
        #: one row for three fires (KOD-178).
        self._unrecorded: dict[str, _UnrecordedFire] = {}
        self._log: BoundLogger = get_logger(__name__)

    def follow(
        self,
        *,
        issue_key: str,
        job_id: str,
        pre_claim_state: str,
        visibility: RepoVisibility = RepoVisibility.PUBLIC,
    ) -> None:
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

        ``visibility`` rides in for the same reason and defaults to the
        same arm the resolution does: the posture belongs to the board the
        claimed issue sits on, which the dispatch pass knows and this does
        not, and public is what keeps the outbound gate engaged.
        """
        self._unrecorded[job_id] = _UnrecordedFire(
            issue_key=issue_key,
            dequeued=False,
        )
        task = asyncio.create_task(
            self.watch(
                issue_key=issue_key,
                job_id=job_id,
                pre_claim_state=pre_claim_state,
                visibility=visibility,
            ),
        )
        self._following.add(task)
        task.add_done_callback(self._on_watch_done)

    def _on_watch_done(self, task: asyncio.Task[None]) -> None:
        """Account for a finished watch: prune it, and keep what it raised.

        The one place every watch passes through exactly once, whenever
        it ends.  Retrieving the exception HERE is what keeps a raising
        watch from ending as an unretrieved task exception, and holding
        it for the drain is what keeps a watch that finished before the
        drain — and so already pruned itself from the in-flight set —
        from being lost to a drain that gathers only what is still in
        flight (KOD-303).
        """
        self._following.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._raised.append(exc)

    def _remember_dequeue(self, job_id: str) -> None:
        """Note that this job's run began, for a sweep the stop has blinded.

        Nothing to remember for a watch nobody followed — a direct
        :meth:`watch` call has no shutdown half to answer to.
        """
        fire = self._unrecorded.get(job_id)
        if fire is None:
            return
        self._unrecorded[job_id] = fire.running()

    @property
    def following(self) -> frozenset[asyncio.Task[None]]:
        """The watches currently in flight."""
        return frozenset(self._following)

    async def drain(self) -> None:
        """Wait out every watch in flight — the shutdown half of ``follow``.

        Belongs AFTER the queue is stopped and BEFORE anything a watch
        writes through is closed.  Stopping the queue ends the stream of
        every job it holds, which is what each watch reads as its job's
        end, so draining here is what turns a stopped instance into a
        released claim instead of a lease the next instance waits out.

        The watches are AWAITED, never cancelled.  Cancelling them is
        precisely what would skip the write-back and the release this
        exists to let happen.  What each watch raised is read off the
        task as it finished, not off this gather, so a watch that ended
        before the drain is reported here exactly as one still in flight
        is: the gather only waits the rest out (KOD-303).
        """
        await asyncio.gather(*self._following, return_exceptions=True)
        for exc in self._raised:
            await self._log.aerror(
                "lifecycle_watch_failed",
                error=str(exc),
                error_kind=type(exc).__name__,
            )
        self._raised.clear()

    async def record_unfinished(self) -> None:
        """Give every fire this process started and did not finish its row.

        The shutdown half of :meth:`_record_fire`.  A watch records at its
        END, so a job that is still queued or still running when the
        process goes down has no row and never will: the measured boot ran
        three fires — one finished, one killed mid-run, one never started —
        and the Fire Log held one line (KOD-178).

        Called AFTER the queue is stopped and AFTER the watches are drained.
        After the stop, when the registry is quiescent: a job that finished
        between a read and the stop would otherwise be swept as failed, and
        its own true row verified away by the sweep's (KOD-178, ruled
        2026-09-02).  After the drain, so nothing records beside this: a
        watch ending on the stopped stream verifies the log and then
        writes, exactly as this does, and two of those interleaved over one
        run — each verifying before either has written — are two rows, the
        thing the verify arm exists to prevent.  What the stop costs is the
        registry's answer to "was this one running?" — it marks everything
        it holds terminal — so the dequeue is remembered HERE, off the first
        frame of each watch, which is the moment the queue handed the job
        to a worker.

        By then every watch that reached its end has recorded its fire and
        forgotten it, so what is left here is the fires whose watches
        RAISED on the way.  No job is skipped on its STATE: a watch that
        raised leaves a fire with no row whatever the registry says about
        it, so the question is put to the recorder per job — verify, then
        backfill — and a fire whose row is already there costs one read and
        no row.

        The registry is in-process, so this answers for a shutdown and for
        nothing else: a process that is killed outright records none of
        this, and the durable registry that would is v0.3's.
        """
        now = datetime.now(UTC)
        for job_id, fire in list(self._unrecorded.items()):
            started_at = await self._run_started_at(job_id)
            if started_at is None:
                # A fire this process started, that nothing recorded, and
                # that the registry has since evicted: its submission is the
                # left edge of the window a row is verified in, and without
                # it there is no row this can honestly write.  Loud, because
                # the log will never hold this fire.
                await self._log.aerror(
                    "unfinished_fire_unknown_to_registry",
                    issue_key=fire.issue_key,
                    job_id=job_id,
                    dequeued=fire.dequeued,
                )
                continue
            outcome = _fire_outcome(started=fire.dequeued, terminal=False)
            placed = await self._record_fire(
                issue_key=fire.issue_key,
                outcome=outcome,
                duration_seconds=(now - started_at).total_seconds(),
                started_at=started_at,
            )
            if placed is not RunRecordResult.WRITTEN:
                continue
            await self._log.ainfo(
                "unfinished_fire_recorded",
                issue_key=fire.issue_key,
                job_id=job_id,
                dequeued=fire.dequeued,
                outcome=outcome.value,
            )

    async def watch(
        self,
        *,
        issue_key: str,
        job_id: str,
        pre_claim_state: str,
        visibility: RepoVisibility = RepoVisibility.PUBLIC,
    ) -> None:
        """Read the job's stream to its end, writing each stage as it arrives.

        The claim is released once the stream has ended and the heartbeat
        with it, so the last word on the marker is the release and not a
        renewal that landed after it.

        A watch that RAISES releases nothing: the stream stopped without
        reaching its end, so the run behind it is not known to have
        finished, and handing the issue to another instance while it may
        still be working is the one thing the claim exists to prevent.  The
        lease lapses on its own there, exactly as it does for a process
        that died.
        """
        loop = asyncio.get_running_loop()
        watch_started = loop.time()
        started = False
        terminal = False
        failure: ErrorEvent | None = None
        async with self._heartbeat.renewing(issue_key=issue_key):
            async for event in self._queue.attach(job_id=job_id):
                if not started:
                    started = True
                    self._remember_dequeue(job_id)
                    await self._writer.on_dequeue(issue_key=issue_key)
                if isinstance(event, WorkflowCompleteEvent):
                    terminal = True
                if isinstance(event, ErrorEvent):
                    failure = event
                await self._apply(
                    issue_key=issue_key,
                    job_id=job_id,
                    event=event,
                    visibility=visibility,
                )
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
                    visibility=visibility,
                )
        await self._heartbeat.release(issue_key=issue_key)
        await self._log.ainfo(
            "lifecycle_watch_finished",
            issue_key=issue_key,
            job_id=job_id,
            run_started=started,
            terminal_outcome=terminal,
        )
        outcome = _fire_outcome(started=started, terminal=terminal)
        # The dispatcher hears first: it is in this process, it costs
        # nothing, and what it does with the news is decide whether the
        # next tick may select this issue again (KOD-174).
        #
        # Contained exactly as the run record below is, and for the same
        # reason: the put-back and the claim release have already
        # happened, so a report hop that raises would unwind a watch whose
        # work is done, lose the record that says the run is over, and
        # report a finished run as a broken one.  The dispatcher's memory
        # is an optimisation over the next tick; the record and the
        # put-back are the run's own history (KOD-276).
        try:
            await self._report(
                issue_key,
                outcome,
                None if failure is None else failure.error_kind,
            )
        except Exception as exc:
            await self._log.aerror(
                "fire_report_failed",
                issue_key=issue_key,
                outcome=outcome.value,
                error_type=type(exc).__name__,
                error=str(exc),
            )
        started_at = await self._run_started_at(job_id)
        if started_at is None:
            # The same absence the sweep names, met at the other end: a run
            # whose submission the registry no longer holds has no window,
            # and a row stamped with anything else would be a second run in
            # the log the moment the sweep wrote its own (KOD-288).
            await self._log.aerror(
                "finished_fire_unknown_to_registry",
                issue_key=issue_key,
                job_id=job_id,
                outcome=outcome.value,
            )
        else:
            await self._record_fire(
                issue_key=issue_key,
                outcome=outcome,
                duration_seconds=loop.time() - watch_started,
                started_at=started_at,
            )
        # Forgotten on either arm: a watch that reached its end has ruled on
        # its fire, and a sweep meeting the fire again would announce the
        # same absence a second time, as unfinished.
        self._unrecorded.pop(job_id, None)

    async def _run_started_at(self, job_id: str) -> datetime | None:
        """When the run this job carries BEGAN — its submission, or nothing.

        ONE reading for both producers.  A fire's record identity is its
        kind, its issue and this instant, so a watch stamping its own start
        while the shutdown sweep read the submission would title the same
        run two ways, and the log would hold it twice — which is the defect
        the exact identity was introduced to end (KOD-288, KOD-178).
        """
        record = await self._registry.get(job_id=job_id)
        return None if record is None else record.submitted_at

    async def _record_fire(
        self,
        *,
        issue_key: str,
        outcome: RunOutcome,
        duration_seconds: float,
        started_at: datetime,
    ) -> RunRecordResult | None:
        """The fire's structural run record — the RUNNER's obligation.

        Written after the release, because the record describes a run that
        is over.  A recording failure is its own loud event rather than a
        failure of the watch: the lifecycle write-back and the claim
        release already happened, and re-raising here would report a
        finished run as a broken one (KOD-170).

        The event names the whole failure — which kind, which destination,
        whose system, and which class of failure — because the measured
        boot's ``run_record_write_failed`` carried an error string and
        nothing else, and a dead knowledge session read exactly like a
        page the vendor refused (KOD-177).

        A recorder that fails with anything else is a defect in the record
        path's own wiring rather than a destination refusing, and it is
        named apart: half a field set under the record event is the muddle
        this exists to end.  Both are contained, for the reason above.

        Answers what the recorder did, so a caller that announces the rows
        it PLACED can tell them from the ones it only found, and ``None``
        for a record that never landed at all.
        """
        try:
            return await self._recorder.record(
                RunRecord(
                    kind=RunKind.FIRE,
                    name=issue_key,
                    outcome=outcome,
                    duration_seconds=duration_seconds,
                    started_at=started_at,
                    recorded_at=datetime.now(UTC),
                ),
            )
        except RunRecordWriteError as exc:
            await self._log.aerror(
                "run_record_write_failed",
                kind=exc.kind,
                name=issue_key,
                outcome=outcome.value,
                destination=exc.destination,
                system=exc.system,
                failure=exc.failure,
                error_type=exc.cause_type,
                error=str(exc),
            )
        except Exception as exc:
            await self._log.aerror(
                "run_record_reporter_failed",
                name=issue_key,
                outcome=outcome.value,
                error_type=type(exc).__name__,
                error=str(exc),
            )
        return None

    async def _apply(
        self,
        *,
        issue_key: str,
        job_id: str,
        event: AgentEvent,
        visibility: RepoVisibility,
    ) -> None:
        if isinstance(event, WorkflowPREvent):
            await self._writer.on_pull_request(
                issue_key=issue_key,
                feature_branch=event.feature_branch,
                feature_tip_sha=event.feature_tip_sha,
                delivered=event.delivered,
            )
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
                visibility=visibility,
            )
