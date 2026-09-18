"""Gather the facts one lane's entry is decided from, and nothing else."""

from collections.abc import Sequence

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import GitService
from kodezart.domain.lane_entry import decide_lane_entry
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
        remote_head = (
            None
            if record is None
            else await self._git.remote_branch_sha(
                repo_path, self._remote, record.branch
            )
        )
        if (
            record is not None
            and remote_head is not None
            and remote_head != record.head_sha
        ):
            await self._log.ainfo(
                "lane_record_head_differs",
                lane=issue_key,
                branch=record.branch,
                recorded_head=record.head_sha,
                remote_head=remote_head,
            )
        return decide_lane_entry(
            issue_key=issue_key,
            record=record,
            remote_loop_head=remote_head,
            open_criteria=open_criteria,
            resolved_base=resolved_base,
        )
