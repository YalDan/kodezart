"""Deny-pattern scanning engine — a configured pattern set per category.

The engine holds no patterns of its own: every pattern originates in
AppConfig.  A second configured pattern set runs through this same class
with zero engine changes, which is what makes it reusable by the hygiene
scan without duplication.

This is the ARITHMETIC half of the outbound gate.  ``gh[posu]_`` either
matches or it does not, so it stays deterministic and stays first in the
gate's ordered list: a credential is caught with no network call, whatever
the judgment path is doing.  It conforms to the async port by awaiting
nothing, and it ignores ``destination`` because a pattern's answer cannot
depend on one — which is exactly the limitation the judgment scanner exists
to cover.
"""

import re
from collections.abc import Mapping, Sequence
from typing import TypeVar

from kodezart.types.domain.gating import (
    UNCONDITIONAL_ROUTING,
    OutboundDestination,
    ScanCategory,
    ScanHit,
    ScannerRouting,
    ScanResult,
)

#: The pattern set's own category vocabulary.  Bound rather than fixed:
#: a ``Mapping`` key is invariant, so a concrete deny set and a concrete
#: hygiene set could not both reach one fixed-key signature — which would
#: have forced the second pattern set into a second engine.
_Category = TypeVar("_Category", bound=ScanCategory)


class RegexContentScanner:
    """``ContentScanner`` over a configured category -> patterns mapping."""

    def __init__(
        self,
        *,
        patterns: Mapping[_Category, Sequence[str]],
    ) -> None:
        self._compiled: dict[ScanCategory, list[re.Pattern[str]]] = {
            category: [re.compile(pattern) for pattern in category_patterns]
            for category, category_patterns in patterns.items()
            if category_patterns
        }

    @property
    def routing(self) -> ScannerRouting:
        """Everything, everywhere: a pattern match costs nothing."""
        return UNCONDITIONAL_ROUTING

    async def scan(
        self,
        *,
        content: str,
        destination: OutboundDestination,
    ) -> ScanResult:
        """Every deny-pattern match in *content*, in payload order."""
        _ = destination  # a pattern's answer cannot depend on the surface
        hits: list[ScanHit] = []
        for category, compiled in self._compiled.items():
            for pattern in compiled:
                hits.extend(
                    ScanHit(
                        category=category,
                        start=match.start(),
                        end=match.end(),
                        rationale=None,
                    )
                    for match in pattern.finditer(content)
                    if match.end() > match.start()
                )
        hits.sort(key=lambda hit: hit.sort_key())
        return ScanResult(hits=tuple(hits))
