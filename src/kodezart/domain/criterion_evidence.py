"""An explicit codec for the existing Evidence field, never a prose inference."""

import json

from kodezart.domain.fire_spec import criterion_field_bodies
from kodezart.types.domain.criterion_evidence import CriterionEvidence


def render_evidence_field(evidence: CriterionEvidence) -> str:
    """Render one field; applying it to a description belongs to the writer."""
    payload = evidence.model_dump_json(by_alias=True, indent=2)
    return f"**Evidence:**\n```json\n{payload}\n```"


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate Evidence field {key!r}")
        result[key] = value
    return result


def parse_criterion_evidence(body: str) -> CriterionEvidence:
    """Read the sole fenced JSON value; historical prose requires migration."""
    fields = criterion_field_bodies(body, field="Evidence")
    if len(fields) != 1:
        raise ValueError("exactly one Evidence field is required")
    value = fields[0]
    prefix, suffix = "```json\n", "\n```"
    if not value.startswith(prefix) or not value.endswith(suffix):
        raise ValueError("Evidence requires one explicit fenced JSON record")
    payload = value[len(prefix) : -len(suffix)]
    json.loads(payload, object_pairs_hook=_unique_object)
    return CriterionEvidence.model_validate_json(payload, strict=True)
