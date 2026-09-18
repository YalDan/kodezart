"""The one decision that turns a lane's tracker and remote facts into an entry."""

from collections.abc import Sequence
from dataclasses import dataclass

from kodezart.domain.errors import LaneEntryError
from kodezart.types.domain.branch import BranchRole
from kodezart.types.domain.lane_entry import (
    DeliverOnlyLane,
    LaneEntry,
    NewLane,
    ResumedLane,
)
from kodezart.types.domain.run_state import LaneRunState


@dataclass(frozen=True, slots=True)
class RecordedBranches:
    """The three refs a record names for the branch it stands on."""

    loop_branch: str
    deliverable_branch: str
    recorded_base: str


def recorded_branches(*, record: LaneRunState) -> RecordedBranches:
    """Resolve the record's branches by ROLE, never by name.

    The LOOP associations whose branch is ``record.branch`` name their
    deliverable in ``derived_from``; the DELIVERABLE associations for that
    branch name the base in theirs.  A name is a name (the ref module's own
    rule), so nothing here reads "ralph" out of a branch to decide what it is.

    Exactly one distinct deliverable and one distinct base, else the lane
    cannot be entered: two of either would leave the resumed loop cut from a
    base, or delivered onto a branch, the record does not settle.
    """
    deliverables = {
        item.derived_from
        for item in record.associations
        if item.role is BranchRole.LOOP
        and item.branch == record.branch
        and item.derived_from is not None
    }
    if len(deliverables) != 1:
        raise LaneEntryError(
            issue_key=record.lane_key,
            reason=(
                "the recorded loop branch names "
                f"{len(deliverables)} deliverable branches, not one"
            ),
            branches=sorted(deliverables),
        )
    deliverable = deliverables.pop()
    bases = {
        item.derived_from
        for item in record.associations
        if item.role is BranchRole.DELIVERABLE
        and item.branch == deliverable
        and item.derived_from is not None
    }
    if len(bases) != 1:
        raise LaneEntryError(
            issue_key=record.lane_key,
            reason=(
                f"the recorded deliverable branch names {len(bases)} bases, not one"
            ),
            branches=sorted(bases),
        )
    return RecordedBranches(
        loop_branch=record.branch,
        deliverable_branch=deliverable,
        recorded_base=bases.pop(),
    )


def decide_lane_entry(
    *,
    issue_key: str,
    record: LaneRunState | None,
    remote_loop_head: str | None,
    open_criteria: Sequence[str],
    resolved_base: str,
) -> LaneEntry | None:
    """The one place a lane's entry is decided, from those facts alone.

    ``None`` is "nothing to do": a lane with no record and no open criterion
    was finished outside kodezart, and a lane whose record already carries a
    pull request has nothing left for this walk to add.

    A record whose branch the remote no longer holds refuses instead of
    starting again: minting a second branch beside a recorded one loses the
    work the record names (KOD-684, and the record's own re-entry text).  A
    record whose base is not the base that resolves now refuses too — the
    branch was cut from a base the lane would no longer be diffed or
    delivered against.

    A record head that differs from the remote head is NOT a refusal: the
    lane resumes at the remote head, because the remote is the truth about
    what the branch contains and the known cause is a commit pushed while its
    record write failed. Refusing would strand exactly that lane; the next
    commit's record write brings the record level again.
    """
    if record is None:
        return NewLane() if open_criteria else None
    branches = recorded_branches(record=record)
    if remote_loop_head is None:
        raise LaneEntryError(
            issue_key=issue_key,
            reason="the recorded branch is absent from the remote",
            branches=(branches.loop_branch,),
        )
    if branches.recorded_base != resolved_base:
        raise LaneEntryError(
            issue_key=issue_key,
            reason=(
                f"the recorded base {branches.recorded_base!r} is not the "
                f"base {resolved_base!r} that resolves now"
            ),
            branches=(branches.deliverable_branch,),
        )
    if open_criteria:
        return ResumedLane(
            deliverable_branch=branches.deliverable_branch,
            loop_branch=branches.loop_branch,
            head_sha=remote_loop_head,
        )
    if record.pr is not None:
        return None
    return DeliverOnlyLane(
        deliverable_branch=branches.deliverable_branch,
        loop_branch=branches.loop_branch,
        head_sha=remote_loop_head,
    )
