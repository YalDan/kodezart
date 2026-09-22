"""One readable representation of the lane's branch-state record."""

import json
from collections.abc import Mapping

from pydantic import ValidationError

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import LaneRecordWriteError
from kodezart.domain.tracker_writes import marked_comment_body
from kodezart.types.domain.branch import BranchAssociation, BranchRole
from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.run_state import (
    LaneBinding,
    LaneCommit,
    LanePR,
    LaneRunState,
)

#: The marker purpose an operation configures this record's prefix under.
RUN_STATE_PURPOSE = "run_state"

#: The subject of the row a stall landing leaves behind. Composed here, beside
#: the one constructor, so the act reads the same on every lane and no caller
#: can name it something a reader of the record would not recognise.
LANDING_ROW_SUBJECT = "land: the best iteration this run reached"

#: The re-entry section every record is written with. A writer renders this
#: one and no other, so the next write of a record read under an earlier
#: section migrates that comment to it.
REENTRY_SECTION = """## Re-entry

Resume at the record's last commit act, the sha of its final commits row, and
never at a remote tip the record does not name. Find the loop branch by the
LOOP role and the record's branch field. When the remote holds it at that sha,
check it out and continue it. When it stands anywhere else, a lane that still
owes criteria cuts a fresh loop branch from the record's last commit act and
keeps the old association, and the old branch stays where it stands; a lane
that owes nothing and carries no pull request is refused rather than delivered
from a branch standing elsewhere. When the remote no longer holds it, recover
it before continuing: never mint a new branch in place of a recorded
association. pushedHeadSha is where the remote held the loop branch when a
commit last observed it, not the head to resume at. Follow the explicit roles
and derivedFrom links to the deliverable, other loop and recovery branches; do
not infer their roles from their names. Associations survive reaping, so verify
current remote liveness before checkout.

Grade the existing commits against each criterion sub-issue's own Check and
verification instructions, reading satisfaction and Evidence on that sub-issue.
Let only failing criteria drive new work."""


#: Every re-entry section a record was written with before the current one,
#: exact to the byte. A comment rendered under one of them is still this lane's
#: record: it is read as such, and its next write renders ``REENTRY_SECTION``.
#: Nothing else is recognised, so an arbitrary text stays a refused framing.
PREVIOUS_REENTRY_SECTIONS: tuple[str, ...] = (
    # Written from 26dd593e (2026-09-14) until cd4eb635 (2026-09-23).
    """## Re-entry

Resume the branch identified by the LOOP role and the record's branch field.
When pushedHeadSha is present, that branch exists on the remote at the recorded
head; check out the existing branch. When pushedHeadSha is null, no remote copy
was recorded: recover the existing branch before continuing. Never mint a new
branch in place of a recorded association. Follow the explicit roles and
derivedFrom links to the deliverable, other loop and recovery branches; do not
infer their roles from their names. Associations survive reaping, so verify
current remote liveness before checkout.

Grade the existing commits against each criterion sub-issue's own Check and
verification instructions, reading satisfaction and Evidence on that sub-issue.
Let only failing criteria drive new work.""",
    # Written from cd4eb635 (2026-09-23) until the text above stated which
    # lanes cut a fresh loop branch and which are refused (2026-09-23).
    """## Re-entry

Resume at the record's last commit act, the sha of its final commits row, and
never at a remote tip the record does not name. Find the loop branch by the
LOOP role and the record's branch field. When the remote holds it at that sha,
check it out and continue it. When it stands anywhere else, cut a fresh loop
branch from that sha and keep the old association; the old branch stays where
it stands. When the remote no longer holds it, recover it before continuing:
never mint a new branch in place of a recorded association. pushedHeadSha is
where the remote held the loop branch when a commit last observed it, not the
head to resume at. Follow the explicit roles and derivedFrom links to the
deliverable, other loop and recovery branches; do not infer their roles from
their names. Associations survive reaping, so verify current remote liveness
before checkout.

Grade the existing commits against each criterion sub-issue's own Check and
verification instructions, reading satisfaction and Evidence on that sub-issue.
Let only failing criteria drive new work.""",
)


def lane_record_body(*, record: LaneRunState) -> str:
    """The comment content after the marker line: the facts and the re-entry.

    The code block is the sole representation of the facts in this comment.
    Criterion satisfaction stays on the owning criterion issues.
    """
    return (
        f"```json\n{record.model_dump_json(by_alias=True, indent=2)}\n```"
        f"\n\n{REENTRY_SECTION}"
    )


def associated_branches(*, record: LaneRunState) -> frozenset[str]:
    """Every branch the record associates with the lane, across every run.

    The answer is a function of the record alone: a ref that consolidation
    deleted or cleanup reaped is still associated, because deleting a ref
    writes nothing to the record. Whether a branch exists now is a separate
    remote read, never a fact this query or the record carries.
    """
    return frozenset(item.branch for item in record.associations)


def render_lane_record(
    *, record: LaneRunState, marker_prefixes: Mapping[str, str]
) -> str:
    """Render a configured marker and one explicit, human-readable JSON record."""
    marker = compose_comment_marker(
        prefixes=marker_prefixes, purpose=RUN_STATE_PURPOSE, lane=record.lane_key
    )
    return marked_comment_body(marker=marker, body=lane_record_body(record=record))


def next_lane_record(
    *,
    prior: LaneRunState | None,
    lane: LaneBinding,
    branch_url: str,
    head_sha: str,
    pushed_head_sha: str | None,
    changeset: ChangesetDigest,
    subject: str,
    recovery_ref: str | None = None,
) -> LaneRunState:
    """The record this commit leaves behind, from the prior one and this receipt.

    The only ``LaneRunState(...)`` call in the source tree (KOD-685); the
    static guard in tests/domain/test_lane_record.py asserts that, for this
    and for every other form the value could be built by. Every fact here is
    arithmetic over the prior record and the observed commit. A commit whose
    head is already the last recorded row appends no second row; the rows are
    the commit acts this lane recorded, so a head that returns to an earlier
    sha is a new act and takes a row of its own. A divergence recovery's
    backup ref (``recovery_ref``) is recorded with its own role and parent,
    the loop branch, and reaping it later changes no recorded fact.

    Composing the value is the boundary that types what the model refuses of
    it: the record's own invariants — the current branch carrying a LOOP
    association, one DELIVERABLE per run, the declared shape of every field —
    belong to the model, and a caller writing a record gets this module's
    write refusal for all of them rather than a validation error out of a
    layer it never called.
    """
    _require_one_binding_per_run(prior=prior, lane=lane)
    commits = list(prior.commits) if prior is not None else []
    if not commits or commits[-1].sha != head_sha:
        commits.append(
            LaneCommit(sha=head_sha, subject=subject, issue_id=lane.lane_key)
        )
    associations = list(prior.associations) if prior is not None else []
    try:
        candidates = [
            BranchAssociation(
                branch=lane.deliverable_branch,
                role=BranchRole.DELIVERABLE,
                derived_from=lane.base_ref,
                run_id=lane.run_id,
            ),
            BranchAssociation(
                branch=lane.loop_branch,
                role=BranchRole.LOOP,
                derived_from=lane.deliverable_branch,
                run_id=lane.run_id,
            ),
        ]
        if recovery_ref is not None:
            candidates.append(
                BranchAssociation(
                    branch=recovery_ref,
                    role=BranchRole.RECOVERY,
                    derived_from=lane.loop_branch,
                    run_id=lane.run_id,
                )
            )
        for association in candidates:
            if association not in associations:
                associations.append(association)
        return LaneRunState(
            lane_key=lane.lane_key,
            branch=lane.loop_branch,
            branch_url=branch_url,
            head_sha=head_sha,
            pushed_head_sha=pushed_head_sha,
            commits_ahead=changeset.commit_count,
            files_changed=len(changeset.file_paths),
            commits=commits,
            pr=prior.pr if prior is not None else None,
            # Pinned once, by the first write that had one: a later entry
            # reading a different subject is an amendment and is refused
            # before it, so nothing here re-pins the digest under a running
            # lane.
            body_digest=(
                prior.body_digest
                if prior is not None and prior.body_digest is not None
                else lane.body_digest
            ),
            associations=associations,
        )
    except ValidationError as exc:
        raise LaneRecordWriteError(
            lane_key=lane.lane_key,
            reason=(
                "the observed facts are not a record: "
                f"{exc.error_count()} refused field(s)"
            ),
        ) from exc


def record_with_pull_request(*, prior: LaneRunState, pr: LanePR) -> LaneRunState:
    """The record this lane's delivery leaves behind: the prior one, plus the pr.

    Composed here because this module owns every form the value is built by
    (KOD-685): the delivering step has no commit receipt and no changeset to
    compose a record from, and it must not compose a first record for a lane
    it could not read one for. Nothing else changes — the head, the rows and
    the associations are still what the last commit observed.
    """
    return prior.model_copy(update={"pr": pr})


def _require_one_binding_per_run(
    *, prior: LaneRunState | None, lane: LaneBinding
) -> None:
    """Refuse a run rebound to another deliverable before composing a record.

    One run delivers onto one branch from one base. A remediation round that
    rebinds a run the record already carries is knowable from these two
    arguments alone, so it is refused here with both readings named rather
    than left to surface as a cardinality failure inside the model.
    """
    for item in prior.associations if prior is not None else ():
        if (
            item.role is BranchRole.DELIVERABLE
            and item.run_id == lane.run_id
            and (item.branch, item.derived_from)
            != (lane.deliverable_branch, lane.base_ref)
        ):
            raise LaneRecordWriteError(
                lane_key=lane.lane_key,
                reason=(
                    f"run {lane.run_id!r} is recorded as delivering "
                    f"{item.branch!r} from {item.derived_from!r} and this "
                    f"commit binds it to {lane.deliverable_branch!r} from "
                    f"{lane.base_ref!r}"
                ),
            )


def parse_lane_record(
    *, body: str, lane_key: str, marker_prefixes: Mapping[str, str]
) -> LaneRunState:
    """Read the declared record format, refusing damaged or ambiguous facts.

    Historical free-form comments require an explicit migration; guessing at
    their prose is not a substitute for the recorded fields. The fixed
    re-entry section is the current one or one of the exact earlier texts in
    ``PREVIOUS_REENTRY_SECTIONS``, so a record written before the section last
    changed stays readable until its next write renders the current one.
    """
    marker = compose_comment_marker(
        prefixes=marker_prefixes, purpose=RUN_STATE_PURPOSE, lane=lane_key
    )
    prefix = f"{marker}\n```json\n"
    suffix = next(
        (
            framed
            for section in (REENTRY_SECTION, *PREVIOUS_REENTRY_SECTIONS)
            if body.endswith(framed := f"\n```\n\n{section}")
        ),
        None,
    )
    if not body.startswith(prefix) or suffix is None:
        raise ValueError("the record framing or fixed re-entry section is invalid")
    payload = body[len(prefix) : -len(suffix)]
    # JSON otherwise accepts repeated keys by silently taking the last value.
    json.loads(payload, object_pairs_hook=_unique_object)
    record = LaneRunState.model_validate_json(payload, strict=True)
    if record.lane_key != lane_key:
        raise ValueError("the record lane does not match its configured marker")
    return record


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate record field {key!r}")
        result[key] = value
    return result
