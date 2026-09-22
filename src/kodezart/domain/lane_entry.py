"""The one decision that turns a lane's tracker and remote facts into an entry."""

from collections.abc import Sequence
from dataclasses import dataclass

from kodezart.domain.errors import LaneEntryError, SubjectAmendedError
from kodezart.domain.fire_spec import body_digest
from kodezart.types.domain.branch import BranchRole
from kodezart.types.domain.fire_spec import TrackerSpec
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


@dataclass(frozen=True, slots=True)
class RecordedCommit:
    """A commit the record names, on the branch its LOOP role resolves it on."""

    branch: str
    sha: str


def recorded_commit(
    *, record: LaneRunState, branches: RecordedBranches
) -> RecordedCommit:
    """The best commit this lane recorded, resolved through the loop level.

    At re-entry the record is the only source of what this lane committed.
    The rows ARE the commit acts (KOD-681), so the last of them is the best
    state the lane reached, and the branch it is reachable on is the one the
    LOOP associations resolve — never a ref composed from another ref's
    text.  A remote tip that has moved past it, or been reset behind it, does
    not change which commit the record names.

    A record naming no commit act refuses: a lane resumed against no
    recorded commit has nothing to grade, and guessing a sha off the head
    field would answer with a commit no row accounts for.
    """
    if not record.commits:
        raise LaneEntryError(
            issue_key=record.lane_key,
            reason="the record names no commit act",
            branches=(branches.loop_branch,),
        )
    return RecordedCommit(branch=branches.loop_branch, sha=record.commits[-1].sha)


def recorded_entry(entry: LaneEntry | None) -> ResumedLane | DeliverOnlyLane | None:
    """The entry when it stands on a record, and ``None`` when it does not.

    One narrowing for "did this lane enter from a record", so the two facts
    such an entry carries — the head the branch stood at and the digest its
    record pinned — are read off the same answer instead of each through its
    own type test.
    """
    return entry if isinstance(entry, (ResumedLane, DeliverOnlyLane)) else None


def decide_lane_entry(
    *,
    issue_key: str,
    recorded: tuple[LaneRunState, RecordedBranches] | None,
    remote_loop_head: str | None,
    remote_deliverable_head: str | None,
    open_criteria: Sequence[str],
    resolved_base: str,
) -> LaneEntry | None:
    """The one place a lane's entry is decided, from those facts alone.

    ``recorded`` is a record together with the branches its associations
    resolve to, and the two travel as one value: resolving them refuses — no
    single deliverable, no single base — and that refusal is a fact of the
    record alone, so the caller makes it BEFORE it asks a remote anything.

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

    ``remote_deliverable_head`` is the other level's own reading, and it is
    carried onto the entry rather than compared with anything here: where the
    deliverable branch stands is a fact about that branch, and the entry is
    the one value this lane's facts are read off. A deliverable branch the
    remote does not hold arrives as ``None`` and refuses nothing — a lane that
    has pushed a loop branch and no deliverable one is exactly the lane a
    resumed entry exists for.
    """
    if recorded is None:
        return NewLane() if open_criteria else None
    record, branches = recorded
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
            deliverable_head_sha=remote_deliverable_head,
            body_digest=record.body_digest,
        )
    if record.pr is not None:
        return None
    return DeliverOnlyLane(
        deliverable_branch=branches.deliverable_branch,
        loop_branch=branches.loop_branch,
        head_sha=remote_loop_head,
        deliverable_head_sha=remote_deliverable_head,
        body_digest=record.body_digest,
    )


def require_unamended_subject(
    *, issue_key: str, entry: LaneEntry | None, spec: TrackerSpec
) -> None:
    """Refuse a lane whose subject was edited since its record pinned it.

    The fire reads the subject text once, at its entry, and this is that
    reading compared with the one fact the record keeps about it. A record
    with no digest — written before the pin existed — is not compared and is
    pinned by its next write. The text is never silently re-read into a
    resumed lane: the criteria the lane owes were graded against what the
    digest names, so a difference is an amendment and refuses here, before
    any session opens.

    Beside the entry rather than inside the fire: it is arithmetic over the
    entry and the captured subject, and the entry is this module's own value.
    """
    recorded = recorded_entry(entry)
    if recorded is None or recorded.body_digest is None:
        return
    current = body_digest(spec.body)
    if current != recorded.body_digest:
        raise SubjectAmendedError(
            issue_key=issue_key,
            recorded_digest=recorded.body_digest,
            current_digest=current,
        )
