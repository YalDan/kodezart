"""Outbound content gate — verdict assignment over an ordered scanner list.

Runs an ORDERED LIST of ``ContentScanner``s so a later increment (an LLM
audit pass over prose surfaces) registers as one more scanner with zero
changes here.

Verdict assignment is per configured category with max-severity-wins.
Identifier-shaped writers block on any hit regardless of the category's
declared verdict — a git ref cannot carry a placeholder.
"""

from collections.abc import Mapping, Sequence

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import ContentScanner
from kodezart.types.domain.gating import (
    GateDecision,
    GateVerdict,
    RedactionCategory,
    RepoVisibility,
    ScanHit,
    WriterShape,
    max_verdict,
)

_PLACEHOLDER = "[REDACTED:{category}]"


class PatternOutboundContentGate:
    """``OutboundContentGate`` over configured scanners and category verdicts."""

    def __init__(
        self,
        *,
        scanners: Sequence[ContentScanner],
        verdicts: Mapping[RedactionCategory, GateVerdict],
    ) -> None:
        self._scanners: tuple[ContentScanner, ...] = tuple(scanners)
        self._verdicts: Mapping[RedactionCategory, GateVerdict] = verdicts
        self._log: BoundLogger = get_logger(__name__)

    def gate(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
    ) -> GateDecision:
        """Decide what may be written for *content* under *visibility*."""
        if visibility is RepoVisibility.PRIVATE:
            return GateDecision(verdict=GateVerdict.CLEAN, content=content)

        hits: list[ScanHit] = []
        for scanner in self._scanners:
            hits.extend(scanner.scan(content))
        if not hits:
            return GateDecision(verdict=GateVerdict.CLEAN, content=content)

        hits.sort(key=lambda hit: (hit.start, hit.end))
        verdict = GateVerdict.CLEAN
        for hit in hits:
            declared = self._verdicts.get(hit.category, GateVerdict.BLOCKED)
            if shape is WriterShape.IDENTIFIER:
                declared = GateVerdict.BLOCKED
            verdict = max_verdict(verdict, declared)

        categories = tuple(dict.fromkeys(hit.category for hit in hits))
        if verdict is GateVerdict.BLOCKED:
            return GateDecision(
                verdict=verdict,
                content="",
                categories=categories,
            )
        return GateDecision(
            verdict=verdict,
            content=_redact(content, hits),
            categories=categories,
        )


def _redact(content: str, hits: Sequence[ScanHit]) -> str:
    """Replace each matched span with a category-labelled placeholder."""
    parts: list[str] = []
    cursor = 0
    for hit in hits:
        if hit.start < cursor:
            continue
        parts.append(content[cursor : hit.start])
        parts.append(_PLACEHOLDER.format(category=hit.category.value))
        cursor = hit.end
    parts.append(content[cursor:])
    return "".join(parts)
