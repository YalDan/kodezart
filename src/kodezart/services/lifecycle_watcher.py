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
way out, reading each one's state off the registry while that state is
still a fact.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

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
from kodezart.types.domain.job import JobState
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.run_records import RunOutcome, RunRecord

#: How a finished fire's outcome reaches the pass that started it: the
#: issue, how the run ended, and the class it died of when an error frame
#: named one.  Shaped after ``ScheduledPass.report`` — the seam a pass's
#: own outcome already travels — because a fire's end is the same kind of
#: fact, and a second shape for it would be a second vocabulary to keep in
#: parity with the first.
type FireReport = Callable[[str, RunOutcome, str | None], Awaitable[None]]


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
        #: started: the state a job holds is what says whether an
        #: unfinished one had been dequeued (KOD-178).
        self._registry: JobRegistry = registry
        self._writer: TrackerLifecycleWriter = writer
        self._heartbeat: ClaimHeartbeat = heartbeat
        self._recorder: RunRecorder = recorder
        #: Where a finished fire's outcome goes — the dispatchers firing
        #: onto this lane, so a run that died is remembered instead of
        #: being re-selected whole at the next tick (KOD-174).
        self._report: FireReport = report
        self._following: set[asyncio.Task[None]] = set()
        #: Every fire this process started, by job, until its watch has
        #: recorded it.  A watch records at its END, so a shutdown that
        #: arrives first — or a watch that raises on the way — leaves the
        #: run with no row at all, and the measured boot's Fire Log held
        #: one row for three fires (KOD-178).
        self._unrecorded: dict[str, str] = {}
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
        self._unrecorded[job_id] = issue_key
        task = asyncio.create_task(
            self.watch(
                issue_key=issue_key,
                job_id=job_id,
                pre_claim_state=pre_claim_state,
                visibility=visibility,
            ),
        )
        self._following.add(task)
        task.add_done_callback(self._following.discard)

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
        exists to let happen; a watch that raises is reported here rather
        than ending the shutdown, which has other things to close.
        """
        outcomes = await asyncio.gather(*self._following, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                await self._log.aerror(
                    "lifecycle_watch_failed",
                    error=str(outcome),
                    error_kind=type(outcome).__name__,
                )

    async def record_unfinished(self) -> None:
        """Give every fire this process started and did not finish its row.

        The shutdown half of :meth:`_record_fire`.  A watch records at its
        END, so a job that is still queued or still running when the
        process goes down has no row and never will: the measured boot ran
        three fires — one finished, one killed mid-run, one never started —
        and the Fire Log held one line (KOD-178).

        Called BEFORE the queue is stopped, because that is the last moment
        the distinction is a FACT: stopping the queue marks every job it
        holds terminal, and after it a run that was mid-flight and a run
        that never left the queue read identically.  Which one a job was is
        the whole difference between ``failed`` and ``never_started``, and
        this reads it off the registry rather than inferring it.

        A job the registry reports TERMINAL is skipped: its watch reached
        its own end and recorded there, and a second row for one run is the
        defect the runner's verify-before-write exists to prevent.  A fire
        whose watch is still in flight is recorded here AND at its end,
        which is the same case — the recorder finds the row this pass wrote
        inside the run's window and writes nothing beside it.

        The registry is in-process, so this answers for a shutdown and for
        nothing else: a process that is killed outright records none of
        this, and the durable registry that would is v0.3's.
        """
        for job_id, issue_key in list(self._unrecorded.items()):
            # Taken per fire, and at the sweep rather than at the job's
            # submission, because the destination answers "is there a row
            # since T?" about the WHOLE log: a window opened when this job
            # was submitted is answered by every other fire's line, and
            # every run after the first would be verified away by a row
            # that is not about it.  Nothing has recorded THIS run — the
            # registry has just said it never finished — so the window
            # that is true of it opens here.
            now = datetime.now(UTC)
            record = await self._registry.get(job_id=job_id)
            if record is None or record.state is JobState.TERMINAL:
                continue
            outcome = _fire_outcome(
                started=record.state is JobState.RUNNING,
                terminal=False,
            )
            await self._log.ainfo(
                "unfinished_fire_recorded",
                issue_key=issue_key,
                job_id=job_id,
                job_state=record.state.value,
                outcome=outcome.value,
            )
            await self._record_fire(
                issue_key=issue_key,
                outcome=outcome,
                duration_seconds=(now - record.submitted_at).total_seconds(),
                started_at=record.submitted_at,
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
        watch_started_at = datetime.now(UTC)
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
        await self._record_fire(
            issue_key=issue_key,
            outcome=outcome,
            duration_seconds=loop.time() - watch_started,
            started_at=watch_started_at,
        )
        self._unrecorded.pop(job_id, None)

    async def _record_fire(
        self,
        *,
        issue_key: str,
        outcome: RunOutcome,
        duration_seconds: float,
        started_at: datetime,
    ) -> None:
        """The fire's structural run record — the RUNNER's obligation.

        Written after the release, because the record describes a run that
        is over.  A recording failure is its own loud event rather than a
        failure of the watch: the lifecycle write-back and the claim
        release already happened, and re-raising here would report a
        finished run as a broken one (KOD-170).
        """
        try:
            await self._recorder.record(
                RunRecord(
                    kind=RunKind.FIRE,
                    name=issue_key,
                    outcome=outcome,
                    duration_seconds=duration_seconds,
                    started_at=started_at,
                    recorded_at=datetime.now(UTC),
                ),
            )
        except Exception as exc:
            await self._log.aerror(
                "run_record_write_failed",
                name=issue_key,
                outcome=outcome.value,
                error_type=type(exc).__name__,
                error=str(exc),
            )

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
