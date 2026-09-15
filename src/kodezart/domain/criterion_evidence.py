"""The Evidence field's explicit codec, and whether it has parts to hold.

Both answers are arithmetic over the same field shape and neither infers
anything from prose, so they read the same commit expression and live
together.
"""

import json
import re

from kodezart.domain.fire_spec import criterion_field_bodies
from kodezart.types.domain.criterion_evidence import (
    GRADED_SHA_PATTERN,
    CriterionEvidence,
)

_GRADED_SHA = re.compile(GRADED_SHA_PATTERN)


def evidence_is_fillable(
    *, graded_sha: str, runnable_test: str | None, named_observation: str | None
) -> bool:
    """Answer whether a criterion's Evidence has both of its parts to be filled with.

    The Evidence field holds a complete graded commit identity and the test
    or recorded observation that commit is judged by, so it is fillable
    exactly when a gradable commit is in hand and at least one demonstration
    is named. This is arithmetic over presence: it reads no prose, judges no
    wording and reaches no backend. A criterion nothing can demonstrate
    leaves the second part with nothing to put in it, and a criterion whose
    commit identity is a branch name leaves the first.
    """
    if _GRADED_SHA.fullmatch(graded_sha) is None:
        return False
    return any(
        part is not None and bool(part.strip())
        for part in (runnable_test, named_observation)
    )


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
