"""The configured comment representation of a pinned fire-time ruling."""

import json
from collections.abc import Mapping

from kodezart.domain.agent import mint_ruling_id
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.tracker_writes import marked_comment_body
from kodezart.types.domain.agent import Ruling, RulingId


def ruling_marker(
    *, ruling_id: RulingId, lane_key: str, marker_prefixes: Mapping[str, str]
) -> str:
    """Use the question identity as the occurrence key within its lane."""
    return compose_comment_marker(
        prefixes=marker_prefixes,
        purpose="ruling",
        lane=lane_key,
        occurrence_key=ruling_id,
    )


def render_ruling(
    *, ruling: Ruling, lane_key: str, marker_prefixes: Mapping[str, str]
) -> str:
    """Render every required field, including authorship, in the pinned text."""
    _require_identity(ruling)
    marker = ruling_marker(
        ruling_id=ruling.ruling_id,
        lane_key=lane_key,
        marker_prefixes=marker_prefixes,
    )
    return marked_comment_body(
        marker=marker,
        body=f"```json\n{ruling.model_dump_json(by_alias=True, indent=2)}\n```",
    )


def parse_ruling(
    *, body: str, lane_key: str, marker_prefixes: Mapping[str, str]
) -> Ruling:
    """Decode the complete record without guessing authorship from prose."""
    marker, separator, payload = body.partition("\n```json\n")
    if not separator or not payload.endswith("\n```"):
        raise ValueError("the ruling comment framing is invalid")
    payload = payload[: -len("\n```")]
    json.loads(payload, object_pairs_hook=_unique_object)
    ruling = Ruling.model_validate_json(payload, strict=True)
    _require_identity(ruling)
    if marker != ruling_marker(
        ruling_id=ruling.ruling_id,
        lane_key=lane_key,
        marker_prefixes=marker_prefixes,
    ):
        raise ValueError("the ruling marker does not match its lane and identity")
    return ruling


def _require_identity(ruling: Ruling) -> None:
    if ruling.ruling_id != mint_ruling_id(
        issue_ref=ruling.issue_ref, question=ruling.question
    ):
        raise ValueError("the ruling identity does not address its exact question")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError(f"duplicate ruling field {key!r}")
        fields[key] = value
    return fields
