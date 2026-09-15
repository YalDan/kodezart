"""The Evidence field's explicit codec, and what an unfilled row still names.

Every answer here is arithmetic over the same field shape and none of them
infers anything from prose, so they live together.
"""

import json

from kodezart.domain.fire_spec import criterion_field_bodies
from kodezart.types.domain.criterion_evidence import CriterionEvidence


def declared_demonstration(
    *, runnable_test: str | None, named_observation: str | None
) -> str | None:
    """The demonstration an author declared, or None when it declared none.

    The Evidence field records the test or observation a graded commit is
    judged by, and at authoring time the author is the only source of that
    name. This is arithmetic over presence: it reads no prose, judges no
    wording and reaches no backend. One reading serves both the refusal and
    the row that gets written, so the two cannot disagree about what was
    declared.
    """
    for part in (runnable_test, named_observation):
        if part is not None and part.strip():
            return part.strip()
    return None


def evidence_is_fillable(
    *, runnable_test: str | None, named_observation: str | None
) -> bool:
    """Answer whether a criterion's Evidence has anything to be filled with.

    The graded sha is not asked here. At authoring no commit has been
    graded yet, and the identity of the commit that eventually will be is
    the Evidence record's own business. What authoring can settle is
    whether anything will ever demonstrate this criterion: a criterion
    nothing can demonstrate leaves the row with nothing to put in it, and no
    later grading run can supply what its author could not name.
    """
    return (
        declared_demonstration(
            runnable_test=runnable_test, named_observation=named_observation
        )
        is not None
    )


def unfilled_evidence_body(*, demonstration: str) -> str:
    """The Evidence row a criterion is created with: unfilled, and named.

    A criterion that has just been created has been graded by nothing, so
    its row keeps the placeholder shape the codec reads as unfilled. What
    the row does carry is the demonstration its author declared, in the slot
    the grading that fills it will name its test in, so the thing this
    criterion is to be graded by outlives the proposal that declared it.
    """
    return f"— (graded sha · {demonstration} · fire session/comment id)"


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
