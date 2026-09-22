"""Gather the facts one lane's entry is decided from, and nothing else."""

from collections.abc import Sequence

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import GitService
from kodezart.domain.lane_entry import decide_lane_entry, recorded_lane
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.lane_entry import LaneEntry


class LaneEntryReader:
    """Reads the record and the remote head; the decision itself is pure.

    Nothing is remembered between calls: the walker asks again before EVERY
    fire, a second fire of the same lane inside one invocation included, so
    cross-process re-entry and in-process re-fire are one code path and a
    killed process changes nothing about the next decision.
    """

    def __init__(
        self, *, records: LaneRecordReader, git: GitService, remote: str
    ) -> None:
        self._records = records
        self._git = git
        self._remote = remote
        self._log: BoundLogger = get_logger(__name__)

    async def read(
        self,
        *,
        issue_key: str,
        open_criteria: Sequence[str],
        repo_path: str,
        resolved_base: str,
    ) -> LaneEntry | None:
        """The lane's entry, or ``None`` when this walk has nothing to do for it.

        A lane no comment addresses has no record; every other way the read
        can be wrong stays the reader's refusal, so an unreadable record is
        never mistaken for an absent one and no branch is minted beside it.
        """
        located = await self._records.find(issue_key=issue_key, lane_key=issue_key)
        record = located[1] if located is not None else None
        # Which branches a record's associations resolve to, and which commit
        # its rows name, are facts of the record alone, and resolving them
        # refuses. Asked here, so a record that settles no deliverable, no
        # base or no commit act refuses before the remote is asked anything
        # about the branch it names.
        recorded = recorded_lane(record=record) if record is not None else None
        # One remote read per branch the record's roles resolve, so each level
        # is answered by a sha of its own and no level's answer is a branch
        # name. The loop level is asked about the branch the LOOP role
        # resolves, and its answer is compared with the head the rows name:
        # it says whether that branch still stands there, and it is never the
        # head itself. The deliverable level is asked about the branch the
        # DELIVERABLE role resolves, and the comparison is against the tip of
        # the base that record's associations name: a deliverable branch that
        # has taken nothing from its loop still stands exactly where that base
        # does.
        remote_head: str | None = None
        deliverable_head: str | None = None
        if recorded is not None:
            named = recorded.branches
            remote_head = await self._git.remote_branch_sha(
                repo_path, self._remote, named.loop_branch
            )
            deliverable_head = await self._git.remote_branch_sha(
                repo_path, self._remote, named.deliverable_branch
            )
            base_head = await self._git.remote_branch_sha(
                repo_path, self._remote, named.recorded_base
            )
            if remote_head is not None and remote_head != recorded.head.sha:
                await self._log.ainfo(
                    "lane_record_head_differs",
                    lane=issue_key,
                    branch=named.loop_branch,
                    recorded_head=recorded.head.sha,
                    remote_head=remote_head,
                )
            # A deliverable branch off its base tip is said out loud and
            # entered anyway: it carries work of its own, which is the lane's
            # next question rather than a reason to strand it. A branch the
            # remote holds neither of is compared with nothing.
            if (
                deliverable_head is not None
                and base_head is not None
                and deliverable_head != base_head
            ):
                await self._log.ainfo(
                    "lane_deliverable_head_differs",
                    lane=issue_key,
                    branch=named.deliverable_branch,
                    base=named.recorded_base,
                    base_head=base_head,
                    deliverable_head=deliverable_head,
                )
        return decide_lane_entry(
            issue_key=issue_key,
            recorded=recorded,
            remote_loop_head=remote_head,
            remote_deliverable_head=deliverable_head,
            open_criteria=open_criteria,
            resolved_base=resolved_base,
        )
