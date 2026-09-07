"""One readable representation of the lane's branch-state record."""

from collections.abc import Mapping

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.tracker_writes import marked_comment_body
from kodezart.types.domain.run_state import LaneRunState

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


def render_lane_record(
    *, record: LaneRunState, marker_prefixes: Mapping[str, str]
) -> str:
    """Render a configured marker and one explicit, human-readable JSON record.

    The code block is the sole representation of the facts in this comment.
    Criterion satisfaction stays on the owning criterion issues.
    """
    marker = compose_comment_marker(
        prefixes=marker_prefixes, purpose="run_state", lane=record.lane_key
    )
    return marked_comment_body(
        marker=marker,
        body=(
            f"```json\n{record.model_dump_json(by_alias=True, indent=2)}\n```"
            f"\n\n{REENTRY_SECTION}"
        ),
    )
