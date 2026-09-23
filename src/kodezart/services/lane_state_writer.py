"""The lane writes its own state: one record, rewritten where it stands."""

from collections.abc import Sequence

from pydantic import ValidationError

from kodezart.core.logging import get_logger
from kodezart.core.outbound_write import gated_exact
from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import (
    ForgeQuery,
    GitService,
    LaneStateTracker,
    OutboundContentGate,
)
from kodezart.domain.comment_markers import (
    compose_comment_marker,
    configured_marker_prefix,
)
from kodezart.domain.criterion_cross_off import (
    HELD_CRITERION_STATE,
    require_tickable,
    tick_anchor,
)
from kodezart.domain.criterion_evidence import apply_evidence
from kodezart.domain.errors import (
    LaneRecordReadError,
    LaneRecordWriteError,
    StaleWriteError,
)
from kodezart.domain.git_url import is_forge_less_origin
from kodezart.domain.lane_record import (
    RUN_STATE_PURPOSE,
    lane_record_body,
    next_lane_record,
    record_with_pull_request,
)
from kodezart.domain.run_event_stream import (
    RUN_EVENT_PURPOSE,
    LaneRunEvent,
    lane_run_events,
)
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.criteria import TrackerCriterion
from kodezart.types.domain.criterion_lifecycle import (
    CriterionCrossOff,
    CrossOffState,
    UndemonstratedReason,
)
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
)
from kodezart.types.domain.operation import LifecycleStage, OperationConfig
from kodezart.types.domain.persist import PersistResult
from kodezart.types.domain.run_event import (
    EVIDENCE_ROW_WRITES,
    UNDEMONSTRATED_EVENT_KINDS,
    RunEventKind,
)
from kodezart.types.domain.run_state import LaneBinding, LanePR, LaneRunState
from kodezart.types.domain.tracker import TrackerComment, TrackerIssue

#: The cross-off states whose write can announce something on the lane's
#: run-event stream: a pass announces the grading it stamped, a fail
#: announces a refutation and an undemonstrated reading announces which
#: reading failed. A lapse announces nothing, so an act holding only lapses
#: reads no stream.
ANNOUNCING_STATES = frozenset(
    {CrossOffState.passed, CrossOffState.failed, CrossOffState.undemonstrated}
)


class TrackerLaneStateWriter:
    """Write what a lane's own work leaves on its board: the record and the ticks.

    The two writes are the lane's two acts. The record is written in the
    same act as a commit; a criterion's state and its graded sha are
    written in the same act as the verdict that produced them, at the one
    site below, so no second copy of a sha exists anywhere to drift from
    the one on the sub-issue.

    The record's facts are read from the workspace the commit was made in,
    so the writer never reports a head, a push or a count it did not
    observe, and a record it cannot read first is never overwritten. The
    board is read once per commit: what this lane has already recorded and
    what its event stream already holds are two readings of one listing.

    The record goes through the outbound gate and is refused if a byte of
    it changes; the first-push event does not, because its body is rendered
    by the port out of a closed event kind and the lane key, and this
    service cannot gate bytes it does not compose.
    """

    def __init__(
        self,
        *,
        tracker: LaneStateTracker,
        operation: OperationConfig,
        git: GitService,
        git_remote: str,
        forge: ForgeQuery | None,
        gate: OutboundContentGate,
    ) -> None:
        self._tracker = tracker
        self._prefixes = dict(operation.marker_prefixes)
        # One locate-and-parse of the record, shared with every other reader
        # of it: a second copy here would drift from the refusals that one
        # makes, and would hand a reply or a foreign comment to the write.
        self._records = LaneRecordReader(tracker=tracker, operation=operation)
        self._git = git
        self._git_remote = git_remote
        self._forge = forge
        self._gate = gate
        self._log = get_logger(__name__)

    def require_writable(self, *, lane: LaneBinding) -> None:
        """Resolve everything this lane's writes need from configuration alone.

        Both marker identities and the recorded branch address are decided
        by the binding and the operation, so an operation missing either
        purpose, and a lane naming no repository, are faults the caller can
        be told about before it opens a session and before it pushes.
        """
        self._markers(lane.lane_key)
        self._branch_url(lane)

    async def record_commit(
        self, *, lane: LaneBinding, workspace_path: str, receipt: PersistResult
    ) -> LaneRunState:
        """Record the pushed commit, editing the lane's one record in place.

        Everything configuration decides is resolved first, before any git
        or tracker call, so a lane that cannot be recorded refuses without
        having written half of it. The three git reads are then observations
        of the workspace this commit was made in: the head the receipt
        names, the remote branch tip as its own three-state value, and the
        base..head changeset. The prior record is parsed before the new one
        is composed, so a damaged record refuses rather than being replaced
        by a fresh one.
        """
        marker, _ = self._markers(lane.lane_key)
        branch_url = self._branch_url(lane)
        head_sha = await self._git.current_sha(workspace_path)
        if head_sha != receipt.commit_sha:
            raise LaneRecordWriteError(
                lane_key=lane.lane_key,
                reason="the workspace head is not the commit the receipt names",
            )
        pushed_head_sha = await self._git.remote_branch_sha(
            workspace_path, self._git_remote, lane.loop_branch
        )
        changeset = await self._git.diff_summary(
            workspace_path, lane.base_ref, head_sha
        )
        comments = await self._board(lane)
        first_push = self._first_push(comments=comments, lane=lane)
        try:
            located = self._records.locate(
                comments=comments, issue_key=lane.lane_key, lane_key=lane.lane_key
            )
        except LaneRecordReadError as exc:
            raise self._unreadable(lane_key=lane.lane_key, exc=exc) from exc
        prior_comment, prior = located if located is not None else (None, None)
        record = next_lane_record(
            prior=prior,
            lane=lane,
            branch_url=branch_url,
            head_sha=head_sha,
            pushed_head_sha=pushed_head_sha,
            changeset=changeset,
            subject=receipt.message.partition("\n")[0],
        )
        body = await self._gate_exact(
            body=lane_record_body(record=record),
            lane_key=lane.lane_key,
            visibility=lane.visibility,
        )
        await settle(
            self._tracker.upsert_comment(
                target=lane.lane_key,
                marker=marker,
                body=body,
                holder=None,
                expected=prior_comment,
            )
        )
        if first_push:
            # The record is rewritten in place and says nothing about when a
            # lane first reached the remote; the first push is that instant,
            # so it is posted once, as an event nobody edits afterwards. The
            # stream itself says whether it was: a record already written
            # would report the event as posted when the post had failed.
            await settle(
                self._tracker.post_run_event(
                    issue_key=lane.lane_key,
                    event=LaneRunEvent(
                        kind=RunEventKind.FIRST_PUSH, lane_key=lane.lane_key
                    ),
                )
            )
        return record

    async def record_pull_request(
        self, *, lane_key: str, pr: LanePR, visibility: RepoVisibility
    ) -> LaneRunState:
        """Put the delivered pull request on this lane's record, in place.

        The record is read through the one reader every other reader of it
        uses, so a damaged or duplicated record refuses here rather than being
        replaced by a record composed out of a delivery; a lane no comment
        addresses refuses for the same reason — a delivery is no basis for a
        first record. A pull request the record already carries writes nothing
        at all, so a second delivery of the same head is not a second write.

        The bytes go through the gate under the run's resolved *visibility*,
        the one the commit write of the same record body asked under: this
        body carries the lane's branch page and the pull request's own
        address, and a private deployment whose forge host it declares
        private would have that body admitted at the commit write and refused
        at this one if this write asked a different question of it.
        """
        marker, _ = self._markers(lane_key)
        try:
            located = await self._records.find(issue_key=lane_key, lane_key=lane_key)
        except LaneRecordReadError as exc:
            raise self._unreadable(lane_key=lane_key, exc=exc) from exc
        if located is None:
            raise LaneRecordWriteError(
                lane_key=lane_key,
                reason="no record of this lane exists to carry a pull request",
            )
        prior_comment, prior = located
        if prior.pr == pr:
            return prior
        record = record_with_pull_request(prior=prior, pr=pr)
        body = await self._gate_exact(
            body=lane_record_body(record=record),
            lane_key=lane_key,
            visibility=visibility,
        )
        await settle(
            self._tracker.upsert_comment(
                target=lane_key,
                marker=marker,
                body=body,
                holder=None,
                expected=prior_comment,
            )
        )
        return record

    async def _board(self, lane: LaneBinding) -> Sequence[TrackerComment]:
        """The lane issue's comments, read once for both facts this write needs.

        A listing that does not validate as comments is this write's own
        refusal. The transport's own failures travel as themselves, the way
        they do from the two writes below, but a validation failure has no
        name a caller could act on and would arrive after the push.
        """
        try:
            return await self._tracker.list_comments(issue_key=lane.lane_key)
        except ValidationError as exc:
            raise LaneRecordWriteError(
                lane_key=lane.lane_key,
                reason=f"the lane's comments could not be read: {exc}",
            ) from exc

    def _events(
        self, *, comments: Sequence[TrackerComment], lane: LaneBinding
    ) -> tuple[LaneRunEvent, ...]:
        """This lane's posted events, as the stream's own reader reads them.

        A stream that will not parse is this write's own refusal: it is read
        after a push when a commit is recorded, and before any write when a
        criterion is taken back. In both a raw parse failure has no name a
        caller could act on.
        """
        try:
            return lane_run_events(
                comments=comments,
                lane_key=lane.lane_key,
                marker_prefixes=self._prefixes,
            )
        except ValueError as exc:
            raise LaneRecordWriteError(
                lane_key=lane.lane_key,
                reason=f"the lane's event stream could not be read: {exc}",
            ) from exc

    def _first_push(
        self, *, comments: Sequence[TrackerComment], lane: LaneBinding
    ) -> bool:
        """Whether this lane's stream is still without its first-push event.

        Read from the stream and not from the absence of a record: a record
        already written would report the event as posted when the post had
        failed.
        """
        return not any(
            event.kind is RunEventKind.FIRST_PUSH
            for event in self._events(comments=comments, lane=lane)
        )

    def _markers(self, lane_key: str) -> tuple[str, str]:
        """This lane's record marker and the prefix its event stream is under.

        Both are resolved together because both are written in the same act:
        resolving only the one the first write needs would leave the other
        purpose's absence to be found after a push and after a comment. The
        lane key is all either needs, so every write of this lane's record
        addresses it from here and no second composition of the marker exists
        to drift from the one the reader addresses.
        """
        return (
            compose_comment_marker(
                prefixes=self._prefixes, purpose=RUN_STATE_PURPOSE, lane=lane_key
            ),
            configured_marker_prefix(self._prefixes, purpose=RUN_EVENT_PURPOSE),
        )

    @staticmethod
    def _unreadable(*, lane_key: str, exc: LaneRecordReadError) -> LaneRecordWriteError:
        """The write refusal a damaged or duplicated record turns into.

        Every write of a lane's record reads the prior record first and
        refuses the same way when that read fails, so the conversion is
        composed once and each writer raises what it returns.
        """
        return LaneRecordWriteError(
            lane_key=lane_key,
            reason=f"the recorded lane state could not be read: {exc.reason}",
        )

    def _branch_url(self, lane: LaneBinding) -> str:
        """The page a person opens for this branch, or the repository itself.

        An origin with no forge behind it has no branch page, so the record
        carries the address that origin is reachable at instead of an
        address composed for a forge that was never asked.
        """
        if (
            self._forge is not None
            and lane.repo_url is not None
            and not is_forge_less_origin(lane.repo_url)
        ):
            return self._forge.branch_web_url(
                repo_url=lane.repo_url, branch=lane.loop_branch
            )
        address = lane.repo_url or lane.repo_path
        if not address:
            raise LaneRecordWriteError(
                lane_key=lane.lane_key,
                reason="the lane names no repository to record its branch under",
            )
        return address

    async def write_cross_offs(
        self,
        *,
        lane: LaneBinding,
        dispatched: Sequence[TrackerCriterion],
        cross_offs: Sequence[CriterionCrossOff],
    ) -> None:
        """Write this attempt's verdict onto the criterion sub-issues it graded.

        The verdict answers the roster it was dispatched against, one for
        one and in order; anything else is a reading of some other roster
        and is refused before a single sub-issue is touched. A criterion
        this attempt neither passed nor had already finished is written
        nowhere on its own sub-issue: it keeps whatever an earlier attempt
        left on it, because there is no verdict to write there.
        A criterion this attempt FAILED and this fire had already finished is
        a regression, and is taken back. A criterion whose earlier grading
        this attempt found LAPSED is taken back the same way and announced
        nowhere: it is owed again, which is not a regression to report. A
        criterion this attempt read nothing about has the reading that failed
        appended to the lane's run-event stream, keyed to that sub-issue and
        at the sha the verdict would have been stamped with.

        The stream is read once for the whole act, before any sub-issue is
        touched, and only when the roster holds something the act could
        announce — a pass, a fail or an undemonstrated reading: a pass, a
        refutation and an undemonstrated reading in one attempt share that
        one reading, a take-back announcing nothing asks the board nothing
        extra, and a stream that will not parse refuses while every
        sub-issue still reads as whatever the last attempt left on it.
        """
        addressed = tuple(str(cross_off.criterion) for cross_off in cross_offs)
        if addressed != tuple(str(criterion.id) for criterion in dispatched):
            raise LaneRecordWriteError(
                lane_key=lane.lane_key,
                reason="the cross-offs do not answer the dispatched criteria",
            )
        events: tuple[LaneRunEvent, ...] = ()
        if any(cross_off.state in ANNOUNCING_STATES for cross_off in cross_offs):
            events = self._events(comments=await self._board(lane), lane=lane)
        for criterion, cross_off in zip(dispatched, cross_offs, strict=True):
            if cross_off.state is CrossOffState.passed:
                await self._write_one(
                    lane=lane, criterion=criterion, cross_off=cross_off, events=events
                )
                continue
            reason = cross_off.undemonstrated_reason
            await self._log.ainfo(
                "criterion_not_crossed_off",
                lane=lane.lane_key,
                criterion=cross_off.criterion,
                state=cross_off.state.value,
                reason=None if reason is None else reason.value,
                graded_sha=cross_off.evidence.graded_sha,
            )
            # On the reason rather than on the state: the cross-off's own
            # invariant makes the two equivalent, and narrowing on it needs
            # no refusal for a state this branch cannot be reached in.
            if reason is not None:
                await self._undemonstrated(
                    lane=lane,
                    criterion=criterion,
                    cross_off=cross_off,
                    reason=reason,
                    events=events,
                )
            elif cross_off.state is CrossOffState.failed:
                await self._take_back(
                    lane=lane,
                    criterion=criterion,
                    cross_off=cross_off,
                    event=LaneRunEvent(
                        kind=RunEventKind.CRITERION_REFUTED,
                        lane_key=lane.lane_key,
                        subject_key=criterion.id,
                        graded_sha=cross_off.evidence.graded_sha,
                    ),
                    events=events,
                )
            elif cross_off.state is CrossOffState.lapsed:
                await self._take_back(
                    lane=lane,
                    criterion=criterion,
                    cross_off=cross_off,
                    event=None,
                    events=events,
                )

    async def _undemonstrated(
        self,
        *,
        lane: LaneBinding,
        criterion: TrackerCriterion,
        cross_off: CriterionCrossOff,
        reason: UndemonstratedReason,
        events: Sequence[LaneRunEvent],
    ) -> None:
        """Record which reading failed for one criterion, once.

        The kind is the reason, so the stream says which reading failed with
        no field of its own to keep in step.  Nothing on the sub-issue is
        touched: no state move, no Evidence row, no description edit, because
        there is no verdict to write there.

        Identity is the whole value, so a graph-level retry of the same
        grading at the same head adds nothing and the same criterion graded
        again at a later head is its own event — the rule the refutation
        beside it already follows.
        """
        event = LaneRunEvent(
            kind=UNDEMONSTRATED_EVENT_KINDS[reason],
            lane_key=lane.lane_key,
            subject_key=criterion.id,
            graded_sha=cross_off.evidence.graded_sha,
        )
        if event not in events:
            await settle(
                self._tracker.post_run_event(issue_key=lane.lane_key, event=event)
            )

    async def _write_one(
        self,
        *,
        lane: LaneBinding,
        criterion: TrackerCriterion,
        cross_off: CriterionCrossOff,
        events: Sequence[LaneRunEvent],
    ) -> None:
        """Finish one criterion at the sha it was graded at.

        The sub-issue is read back through the port here and not remembered
        from the dispatch, so what the write asserts about it is what it
        holds now. The body edit goes first under its own compare-and-set
        precondition and the transition only after: the port's own order,
        so a transition can never ride on a body write that never landed.

        Each of the two writes reads its own sub-issue: the transition has
        no compare-and-set of its own, so it is the second read that stands
        in for one. A sub-issue the board moved between the stamp and the
        transition keeps the Evidence row of the grading that reached it,
        and that grading's entry, and is not finished.

        The grading is announced on the LANE's stream as soon as the stamp
        lands, at the sha the row now names. That entry is what makes the
        stream the Evidence row's own write history rather than a
        refutation-only subset of it: a passing grading restamps the row, and
        a reader asking which grading the row's commit came from has to find
        the passing ones there too, or every ordinary refuted-then-passed
        lifecycle reads as a row pointing behind its last recorded grading
        (KOD-506). The announcement addresses the lane issue, not this
        criterion, so a criterion sub-issue still carries no comment of any
        kind.

        The entry follows the row write it records and comes before the
        transition, so the row and its history move together: a stamp that
        refuses posts nothing and leaves both at the earlier grading, and a
        transition that refuses after it leaves the row and its last entry
        naming the same commit. Posted after the transition, a criterion
        already finished and graded again at a later head whose transition
        failed would keep a row at the new commit with no entry naming it,
        and nothing repairs that: finished, it is in no later attempt's
        roster. Posted before the stamp, a refused stamp would leave an entry
        at a commit the row does not name.

        It is announced once for one row write: the same verdict written
        again at the same head restamps the same row, so it is posted unless
        the criterion's LAST row-write entry in *events* is already this pass.
        *events* is the act's one reading of the stream, taken before
        anything is written — the reading a take-back looks its refutation
        up in — so a stream that will not parse has refused before this
        sub-issue was read. An equal entry anywhere earlier would not do: a
        head that returns to a commit the criterion passed at before a later
        refutation restamps the row there again, and without a fresh entry
        the history would end at the refutation while the row names the
        earlier commit. A post that fails stops the act before the
        transition: a criterion not yet finished stays owed and the next
        attempt announces its own grading, while one already finished keeps
        its restamped row unannounced, which is the same partial state a lost
        refutation leaves and is repaired by nobody.
        """
        issue = await self._tracker.read_issue(issue_key=criterion.id)
        require_tickable(issue=issue, criterion=criterion)
        event = LaneRunEvent(
            kind=RunEventKind.CRITERION_PASSED,
            lane_key=lane.lane_key,
            subject_key=criterion.id,
            graded_sha=cross_off.evidence.graded_sha,
        )
        written = [
            entry
            for entry in events
            if entry.subject_key == criterion.id and entry.kind in EVIDENCE_ROW_WRITES
        ]
        posted = written[-1:] == [event]
        await self._stamp(
            lane=lane, criterion=criterion, issue=issue, cross_off=cross_off
        )
        if not posted:
            await settle(
                self._tracker.post_run_event(issue_key=lane.lane_key, event=event)
            )
        stamped = await self._tracker.read_issue(issue_key=criterion.id)
        require_tickable(issue=stamped, criterion=criterion)
        await settle(
            self._tracker.set_workflow_state(
                issue_key=criterion.id, stage=LifecycleStage.DONE
            )
        )

    async def _take_back(
        self,
        *,
        lane: LaneBinding,
        criterion: TrackerCriterion,
        cross_off: CriterionCrossOff,
        event: LaneRunEvent | None,
        events: Sequence[LaneRunEvent],
    ) -> None:
        """Take back a criterion this fire finished, once, for either reason.

        Two readings end a criterion's finished state and they end it the
        same way: a fresh grading refuted it, or the grading that finished
        it no longer stands. Only the announcement differs, so it arrives as
        *event*: a refutation is a regression and says so on the stream,
        while a lapse is the criterion being owed again and says nothing,
        which is also what makes a repeat lapse write nothing at all — the
        early return below already reads the board as unfinished.

        A criterion the fresh read finds unfinished is no regression: it was
        never this fire's claim to take back. A criterion found finished is
        taken back on either reading whoever moved it there — the roster carries no
        state and the Evidence row no run, so one a person moved into that
        state reads exactly like one this fire finished, and nothing but the
        evaluation step moves a criterion there, which makes that a protocol
        violation on the board rather than a case to tell apart here.

        Everything knowable is read before anything is written: the state the
        board holds, the body the writes depend on, and — when there is an
        event to announce — the stream, read once for the whole act before
        the first sub-issue is read, so a stream that will not parse refuses
        while the sub-issue still reads as the pass it was. A take-back
        announcing nothing asks the board nothing extra at all.
        The move back is then the FIRST write, because a
        criterion left finished is in no later fire's roster and would never
        be graded again: a failure after it leaves the criterion unstarted and
        owed, and the next fire re-grades it, rather than certified at a sha
        nothing passed at. Which grading the Evidence row carries then depends
        on what was lost — a lost stamp leaves the earlier grading, a lost
        event leaves the refuting one — and neither partial state carries an
        event. Nothing repairs the event for such a criterion: unstarted, it
        is no longer this fire's claim to take back.

        The sub-issue is read once more after the move and asked the same
        precondition, because the Evidence row is set against THAT body: a
        criterion a third party amended under the move is one this verdict no
        longer addresses, and the act stops there rather than writing the
        refuting grading onto it — unstarted, owed, carrying the earlier
        grading, with no event, which is the partial state a lost stamp
        leaves. The owning issue reopens by the tracker's own rollup over its
        criteria and is written by nobody.
        """
        issue = await self._tracker.read_issue(issue_key=criterion.id)
        if issue.state_kind is not HELD_CRITERION_STATE:
            return
        require_tickable(issue=issue, criterion=criterion)
        # The row the act ends by setting is a precondition of the write it
        # starts with: a body this grading's Evidence row cannot be set on
        # refuses here, while the sub-issue still reads as the pass it was,
        # rather than after the move back has already taken it.
        self._evidence_body(issue=issue, criterion=criterion, cross_off=cross_off)
        posted = event is not None and event in events
        await settle(self._tracker.reset_criterion_pending(expected=issue, holder=None))
        moved = await self._tracker.read_issue(issue_key=criterion.id)
        require_tickable(issue=moved, criterion=criterion)
        await self._stamp(
            lane=lane, criterion=criterion, issue=moved, cross_off=cross_off
        )
        if event is not None and not posted:
            await settle(
                self._tracker.post_run_event(issue_key=lane.lane_key, event=event)
            )

    async def _stamp(
        self,
        *,
        lane: LaneBinding,
        criterion: TrackerCriterion,
        issue: TrackerIssue,
        cross_off: CriterionCrossOff,
    ) -> None:
        """Put one grading's facts on a criterion's Evidence row, and nothing else.

        The one write of that row in the source. The row says what the last
        grading of this criterion read and at which commit, so a second
        writer of it would be a second answer to that one question; the body
        it sets is composed by the one function below.
        """
        body = await self._gate_exact(
            body=self._evidence_body(
                issue=issue, criterion=criterion, cross_off=cross_off
            ),
            lane_key=lane.lane_key,
            visibility=lane.visibility,
            destination=OutboundDestination.TRACKER_DESCRIPTION,
        )
        await settle(
            self._tracker.edit_description(
                target=criterion.id, expected=issue.body, replacement=body
            )
        )

    def _evidence_body(
        self,
        *,
        issue: TrackerIssue,
        criterion: TrackerCriterion,
        cross_off: CriterionCrossOff,
    ) -> str:
        """The body this grading's Evidence row leaves, or this write's refusal.

        The only function in the source that applies Evidence, and the whole
        precondition of setting that row: the codec refuses a body whose
        Evidence row it cannot read back as exactly one row, and a body
        carrying an unclosed fence or an unclosed HTML comment before that
        row is such a body — the row the edit writes is hidden by them and
        reads back as no row at all. That is knowable from the body alone,
        so it is asked as the same stale-write refusal every other condition
        of this write makes, before a byte is written and not as an untyped
        failure out of the codec once the grading session has already run.
        """
        try:
            return apply_evidence(body=issue.body, evidence=cross_off.evidence)
        except ValueError as exc:
            raise StaleWriteError(
                target=criterion.id, expected=tick_anchor(criterion)
            ) from exc

    async def _gate_exact(
        self,
        *,
        body: str,
        lane_key: str,
        visibility: RepoVisibility,
        destination: OutboundDestination = OutboundDestination.TRACKER_COMMENT,
    ) -> str:
        return await gated_exact(
            gate=self._gate,
            log=self._log,
            content=body,
            visibility=visibility,
            destination=destination,
            content_class=ContentClass.DERIVED,
            # Every commit row of a record carries this lane's own key, and
            # commitsAhead/filesChanged are repository counts, not tracker
            # objects; the Evidence body is a graded sha and a test name.
            aggregates=(),
            refusal=lambda: LaneRecordWriteError(
                lane_key=lane_key,
                reason="the outbound gate changed the recorded lane facts",
            ),
        )
