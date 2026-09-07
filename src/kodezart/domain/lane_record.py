"""One readable representation of the lane's branch-state record."""

from collections.abc import Mapping

from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.tracker_writes import marked_comment_body
from kodezart.types.domain.run_state import LaneRunState


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
        body=f"```json\n{record.model_dump_json(by_alias=True, indent=2)}\n```",
    )
