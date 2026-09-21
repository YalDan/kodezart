"""Gather the facts one lane's entry is decided from, and nothing else."""

from collections.abc import Sequence

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import GitService
from kodezart.domain.lane_entry import (
    RecordedBranches,
    RecordedCommit,
    decide_lane_entry,
    recorded_branches,
    recorded_commit,
)
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.lane_entry import LaneEntry
from kodezart.types.domain.run_state import LaneRunState


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
        recorded: tuple[LaneRunState, RecordedBranches] | None = None
        resolved: RecordedCommit | None = None
        if record is not None:
            branches = recorded_branches(record=record)
            recorded = (record, branches)
            resolved = recorded_commit(record=record, branches=branches)
        # The remote is asked about the branch the LOOP role resolves, and the
        # comparison is against the commit the rows name — never a field read
        # as a name or as the lane's best state.
        remote_head = (
            None
            if resolved is None
            else await self._git.remote_branch_sha(
                repo_path, self._remote, resolved.branch
            )
        )
        if (
            resolved is not None
            and remote_head is not None
            and remote_head != resolved.sha
        ):
            await self._log.ainfo(
                "lane_record_head_differs",
                lane=issue_key,
                branch=resolved.branch,
                recorded_head=resolved.sha,
                remote_head=remote_head,
            )
        return decide_lane_entry(
            issue_key=issue_key,
            recorded=recorded,
            remote_loop_head=remote_head,
            open_criteria=open_criteria,
            resolved_base=resolved_base,
        )
