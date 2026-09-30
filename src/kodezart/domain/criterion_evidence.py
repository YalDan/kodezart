"""An explicit codec for the existing Evidence field, never a prose inference."""

import json

from kodezart.domain.fire_spec import criterion_field_bodies, replace_criterion_fields
from kodezart.types.domain.criterion_evidence import CriterionEvidence


def evidence_field_value(evidence: CriterionEvidence) -> str:
    """The field's own value: one fenced JSON record, on its own lines.

    The value starts on the line after the row label so the fence opens a
    line of its own, which is what the reader above locates the record by.
    """
    payload = evidence.model_dump_json(by_alias=True, indent=2)
    return f"\n```json\n{payload}\n```"


def render_evidence_field(evidence: CriterionEvidence) -> str:
    """Render one field; applying it to a description belongs to the writer."""
    return f"**Evidence:**{evidence_field_value(evidence)}"


def apply_evidence(*, body: str, evidence: CriterionEvidence) -> str:
    """Return *body* with exactly its Evidence field set to *evidence*.

    The edit is field-scoped rather than a body composition: every byte
    outside the Evidence row is the body's own, so consecutive writes of
    one criterion differ inside that row and nowhere else.
    ``parse_criterion_evidence`` reads back what was applied, which is what
    makes this the write half of the codec above rather than a second one.
    """
    return replace_criterion_fields(
        body, replacements={"Evidence": evidence_field_value(evidence)}
    )


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
