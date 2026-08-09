"""Deterministic payload classification — the router for the cheap path.

Criteria are crossed off inside the loop at evaluator cadence, so outbound
writes are frequent: a tick, an evidence note, a state transition, per
criterion per iteration.  A model call on each is unaffordable, which makes
this classification load-bearing rather than an optimisation.

No model, no network, no configuration.  A payload is ``STRUCTURED`` only
when it parses as JSON, or is a single identifier / sha / enum-shaped token,
**and** every string leaf matches an identifier shape.  One free-text leaf
makes the whole payload ``AUTHORED_PROSE`` — a container being structured
does not make its contents so.
"""

import json
import re
from typing import Final

from kodezart.types.domain.gating import ContentClass

#: An identifier-shaped token: no whitespace, and only the characters that
#: appear in identifiers, shas, slugs, enum members, refs and versions.  No
#: length bound is imposed — a bound would be a number with no policy behind
#: it, and length is not what distinguishes an identifier from prose.
_TOKEN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_./:+#@-]+")


class ContentClassifier:
    """Classifies an outbound payload as structured or authored prose."""

    def classify(self, content: str) -> ContentClass:
        """Which class *content* falls into. Deterministic and total."""
        stripped = content.strip()
        if not stripped:
            return ContentClass.STRUCTURED
        try:
            parsed: object = json.loads(stripped)
        except ValueError:
            return self._classify_scalar(stripped)
        return (
            ContentClass.STRUCTURED
            if _leaves_are_identifier_shaped(parsed)
            else ContentClass.AUTHORED_PROSE
        )

    def _classify_scalar(self, stripped: str) -> ContentClass:
        """A payload that is not JSON: one token is structured, prose is not."""
        return (
            ContentClass.STRUCTURED
            if _TOKEN.fullmatch(stripped)
            else ContentClass.AUTHORED_PROSE
        )


def _leaves_are_identifier_shaped(value: object) -> bool:
    """Whether every string leaf of *value* is identifier-shaped."""
    if isinstance(value, str):
        return bool(_TOKEN.fullmatch(value)) or not value
    if isinstance(value, list):
        items: list[object] = value
        return all(_leaves_are_identifier_shaped(item) for item in items)
    if isinstance(value, dict):
        mapping: dict[str, object] = value
        return all(_leaves_are_identifier_shaped(item) for item in mapping.values())
    return True
