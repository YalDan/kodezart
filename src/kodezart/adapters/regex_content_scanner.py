"""Deny-pattern scanning engine — a configured pattern set per category.

The engine holds no patterns of its own: every pattern originates in
AppConfig.  A second configured pattern set runs through this same class
with zero engine changes, which is what makes it reusable by the hygiene
scan without duplication.
"""

import re
from collections.abc import Mapping, Sequence

from kodezart.types.domain.gating import RedactionCategory, ScanHit


class RegexContentScanner:
    """``ContentScanner`` over a configured category -> patterns mapping."""

    def __init__(
        self,
        *,
        patterns: Mapping[RedactionCategory, Sequence[str]],
    ) -> None:
        self._compiled: dict[RedactionCategory, list[re.Pattern[str]]] = {
            category: [re.compile(pattern) for pattern in category_patterns]
            for category, category_patterns in patterns.items()
            if category_patterns
        }

    def scan(self, content: str) -> Sequence[ScanHit]:
        """Every deny-pattern match in *content*, in payload order."""
        hits: list[ScanHit] = []
        for category, compiled in self._compiled.items():
            for pattern in compiled:
                hits.extend(
                    ScanHit(
                        category=category,
                        start=match.start(),
                        end=match.end(),
                    )
                    for match in pattern.finditer(content)
                    if match.end() > match.start()
                )
        hits.sort(key=lambda hit: (hit.start, hit.end))
        return hits
