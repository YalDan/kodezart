"""The lane writes its own state: one record, rewritten where it stands."""

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
from kodezart.domain.errors import LaneRecordReadError, LaneRecordWriteError
from kodezart.domain.git_url import is_forge_less_origin
from kodezart.domain.lane_record import (
    RUN_STATE_PURPOSE,
    lane_record_body,
    next_lane_record,
)
from kodezart.domain.run_event_stream import RUN_EVENT_PURPOSE, LaneRunEvent
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.gating import ContentClass, OutboundDestination
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.persist import PersistResult
from kodezart.types.domain.run_event import RunEventKind
from kodezart.types.domain.run_state import LaneBinding, LaneRunState


class TrackerLaneStateWriter:
    """Write the lane's run-state record in the same act as its commit.

    The record's facts are read from the workspace the commit was made in,
    so the writer never reports a head, a push or a count it did not
    observe, and a record it cannot read first is never overwritten.

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
        self._markers(lane)
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
        marker, _ = self._markers(lane)
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
        first_push = not any(
            event.kind is RunEventKind.FIRST_PUSH
            for event in await self._tracker.lane_run_events(
                issue_key=lane.lane_key, lane_key=lane.lane_key
            )
        )
        try:
            located = await self._records.find(
                issue_key=lane.lane_key, lane_key=lane.lane_key
            )
        except LaneRecordReadError as exc:
            raise LaneRecordWriteError(
                lane_key=lane.lane_key,
                reason=f"the recorded lane state could not be read: {exc.reason}",
            ) from exc
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
        body = await self._gate_exact(body=lane_record_body(record=record), lane=lane)
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

    def _markers(self, lane: LaneBinding) -> tuple[str, str]:
        """This lane's record marker and the prefix its event stream is under.

        Both are resolved together because both are written in the same act:
        resolving only the one the first write needs would push the other
        operation's absence past a push and past a comment.
        """
        return (
            compose_comment_marker(
                prefixes=self._prefixes, purpose=RUN_STATE_PURPOSE, lane=lane.lane_key
            ),
            configured_marker_prefix(self._prefixes, purpose=RUN_EVENT_PURPOSE),
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

    async def _gate_exact(self, *, body: str, lane: LaneBinding) -> str:
        return await gated_exact(
            gate=self._gate,
            log=self._log,
            content=body,
            visibility=lane.visibility,
            destination=OutboundDestination.TRACKER_COMMENT,
            content_class=ContentClass.DERIVED,
            refusal=lambda: LaneRecordWriteError(
                lane_key=lane.lane_key,
                reason="the outbound gate changed the recorded lane facts",
            ),
        )
