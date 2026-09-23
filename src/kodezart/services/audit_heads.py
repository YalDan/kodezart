"""The one commit every audit arm verifies a lane against."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import GitService
from kodezart.domain.errors import AuditClaimReadError
from kodezart.types.domain.branch import BranchRole
from kodezart.types.domain.run_state import LaneRunState


@dataclass(frozen=True, slots=True)
class VerificationHead:
    """Where a lane's recorded work stands on the remote, read once.

    ``loop_head`` is the remote head of the record's loop branch. When that
    branch is gone, ``deliverable`` is the DELIVERABLE branch the record
    associates with the loop branch's run and ``deliverable_head`` its remote
    head, each ``None`` when absent.

    ``sha`` is the verification head, the commit the audit verifies against:
    the loop head when the loop branch exists; otherwise the record's head
    when the deliverable branch contains it, which is how consolidation
    leaves a delivered lane; otherwise ``None``, and the lane's branch is
    missing. It is always a commit in the audit's cached repository.
    """

    loop_head: str | None
    deliverable: str | None
    deliverable_head: str | None
    sha: str | None


def recorded_deliverable(record: LaneRunState) -> str | None:
    """The DELIVERABLE branch of the run the recorded loop branch belongs to.

    Bounded by the record's own association list. ``None`` when that run
    records no deliverable; more than one candidate is an unreadable record,
    never a choice.
    """
    runs = {
        item.run_id
        for item in record.associations
        if item.role is BranchRole.LOOP and item.branch == record.branch
    }
    branches = {
        item.branch
        for item in record.associations
        if item.role is BranchRole.DELIVERABLE and item.run_id in runs
    }
    if len(branches) > 1:
        raise AuditClaimReadError(
            "the recorded loop branch has more than one deliverable branch"
        )
    return next(iter(branches), None)


#: Reads one branch's remote head, ``None`` when the remote has no such branch.
type RemoteHead = Callable[[str], Awaitable[str | None]]


async def read_verification_head(
    *,
    git: GitService,
    repository: str,
    remote: str,
    record: LaneRunState,
    remote_head: RemoteHead | None = None,
) -> VerificationHead:
    """Read the record's verification head, settled through cancellation.

    Every audit reader takes the head it verifies at from here, and a reader
    re-checking its source compares a second read of this to its first.
    ``remote_head`` is how the caller reads one branch's remote head, with its
    own identity checks; by default the remote is asked directly.
    """

    async def ask(branch: str) -> str | None:
        return await git.remote_branch_sha(repository, remote, branch)

    head = ask if remote_head is None else remote_head

    async def read() -> VerificationHead:
        loop_head = await head(record.branch)
        if loop_head is not None:
            return VerificationHead(
                loop_head=loop_head,
                deliverable=None,
                deliverable_head=None,
                sha=loop_head,
            )
        deliverable = recorded_deliverable(record)
        deliverable_head = None
        if deliverable is not None:
            deliverable_head = await head(deliverable)
        contained = False
        if deliverable_head is not None:
            await git.fetch(repository)
            contained = await git.is_ancestor(
                repository, record.head_sha, deliverable_head
            )
        return VerificationHead(
            loop_head=None,
            deliverable=deliverable,
            deliverable_head=deliverable_head,
            sha=record.head_sha if contained else None,
        )

    return await settle(read())
