"""One readable representation of the lane's branch-state record."""

import json
from collections.abc import Mapping

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import LaneRecordWriteError
from kodezart.domain.tracker_writes import marked_comment_body
from kodezart.types.domain.branch import BranchAssociation, BranchRole
from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.run_state import LaneBinding, LaneCommit, LaneRunState

#: The marker purpose an operation configures this record's prefix under.
RUN_STATE_PURPOSE = "run_state"

REENTRY_SECTION = """## Re-entry

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
Let only failing criteria drive new work."""


def lane_record_body(*, record: LaneRunState) -> str:
    """The comment content after the marker line: the facts and the re-entry.

    The code block is the sole representation of the facts in this comment.
    Criterion satisfaction stays on the owning criterion issues.
    """
    return (
        f"```json\n{record.model_dump_json(by_alias=True, indent=2)}\n```"
        f"\n\n{REENTRY_SECTION}"
    )


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
) -> LaneRunState:
    """The record this commit leaves behind, from the prior one and this receipt.

    The only ``LaneRunState(...)`` call in the source tree (KOD-685); the
    static guard in tests/domain/test_lane_record.py asserts that, for this
    and for every other form the value could be built by. Every fact here is
    arithmetic over the prior record and the observed commit. A commit whose
    head is already the last recorded row appends no second row; the rows are
    the commit acts this lane recorded, so a head that returns to an earlier
    sha is a new act and takes a row of its own.
    """
    _require_one_binding_per_run(prior=prior, lane=lane)
    commits = list(prior.commits) if prior is not None else []
    if not commits or commits[-1].sha != head_sha:
        commits.append(
            LaneCommit(sha=head_sha, subject=subject, issue_id=lane.lane_key)
        )
    associations = list(prior.associations) if prior is not None else []
    for association in (
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
    ):
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
        # Pinned once, by the first write that had one: a later entry reading
        # a different subject is an amendment and is refused before it, so
        # nothing here re-pins the digest under a running lane.
        body_digest=(
            prior.body_digest
            if prior is not None and prior.body_digest is not None
            else lane.body_digest
        ),
        associations=associations,
    )


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
    their prose is not a substitute for the recorded fields.
    """
    marker = compose_comment_marker(
        prefixes=marker_prefixes, purpose=RUN_STATE_PURPOSE, lane=lane_key
    )
    prefix = f"{marker}\n```json\n"
    suffix = f"\n```\n\n{REENTRY_SECTION}"
    if not body.startswith(prefix) or not body.endswith(suffix):
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
