"""Outbound content gate — verdict assignment over an ordered scanner list.

Runs an ORDERED LIST of ``ContentScanner``s.  Deterministic scanners come
first and that ordering is load-bearing rather than cosmetic: it is what
keeps a credential caught with no network call when the judgment path is
degraded, and it is what makes the short-circuit below legal.

Verdict assignment is per configured category with max-severity-wins.
Identifier-shaped writers block on any hit regardless of the category's
declared verdict — a git ref cannot carry a placeholder.  A hit that
localizes to no span also blocks: redaction is span surgery, and there is
nothing to excise.

A scanner that cannot answer BLOCKS.  It is never skipped and its verdict is
never downgraded to CLEAN, so "did not answer" and "said it is clean" stay
two distinct observable states.
"""

from collections.abc import Mapping, Sequence

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import ContentScanner
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RedactionCategory,
    RepoVisibility,
    ScanHit,
    WriterShape,
    content_digest,
    max_verdict,
)

_PLACEHOLDER = "[REDACTED:{category}]"

#: What makes two gate calls the same question.  The declared class is part
#: of it: two payloads identical in bytes but declared with different
#: provenance are routed to different scanners, so they must not share a
#: verdict.
type _MemoKey = tuple[str, OutboundDestination, str, ContentClass]


class PatternOutboundContentGate:
    """``OutboundContentGate`` over configured scanners and category verdicts."""

    def __init__(
        self,
        *,
        scanners: Sequence[ContentScanner],
        verdicts: Mapping[RedactionCategory, GateVerdict],
        fragment_digest: str = "",
    ) -> None:
        self._scanners: tuple[ContentScanner, ...] = tuple(scanners)
        self._verdicts: Mapping[RedactionCategory, GateVerdict] = verdicts
        self._fragment_digest: str = fragment_digest
        self._memo: dict[_MemoKey, GateDecision] = {}
        self._log: BoundLogger = get_logger(__name__)

    async def gate(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
        destination: OutboundDestination,
        content_class: ContentClass,
    ) -> GateDecision:
        """Decide what may be written for *content* under *visibility*."""
        if visibility is RepoVisibility.PRIVATE:
            return GateDecision(verdict=GateVerdict.CLEAN, content=content)

        key = (
            content_digest(content),
            destination,
            self._fragment_digest,
            content_class,
        )
        memoized = self._memo.get(key)
        if memoized is not None:
            return memoized

        decision = await self._decide(
            content=content,
            shape=shape,
            destination=destination,
            content_class=content_class,
        )
        self._memo[key] = decision
        return decision

    async def _decide(
        self,
        *,
        content: str,
        shape: WriterShape,
        destination: OutboundDestination,
        content_class: ContentClass,
    ) -> GateDecision:
        """Run the routed scanners in order, stopping at the first BLOCKED."""
        hits: list[ScanHit] = []
        for scanner in self._scanners:
            if not scanner.routing.applies(
                destination=destination,
                content_class=content_class,
            ):
                continue
            result = await scanner.scan(content=content, destination=destination)
            if result.failure is not None:
                return GateDecision(
                    verdict=GateVerdict.BLOCKED,
                    content="",
                    categories=(),
                    hits=(),
                    failure=result.failure,
                )
            hits.extend(result.hits)
            if self._fold(hits, shape=shape) is GateVerdict.BLOCKED:
                break

        if not hits:
            return GateDecision(verdict=GateVerdict.CLEAN, content=content)

        hits.sort(key=lambda hit: hit.sort_key())
        verdict = self._fold(hits, shape=shape)
        categories = tuple(dict.fromkeys(hit.category for hit in hits))
        if verdict is GateVerdict.BLOCKED:
            return GateDecision(
                verdict=verdict,
                content="",
                categories=categories,
                hits=tuple(hits),
            )
        return GateDecision(
            verdict=verdict,
            content=_redact(content, hits),
            categories=categories,
            hits=tuple(hits),
        )

    def _fold(self, hits: Sequence[ScanHit], *, shape: WriterShape) -> GateVerdict:
        """Max-severity-wins over every hit's resolved verdict."""
        verdict = GateVerdict.CLEAN
        for hit in hits:
            declared = (
                self._verdicts.get(hit.category, GateVerdict.BLOCKED)
                if isinstance(hit.category, RedactionCategory)
                else GateVerdict.BLOCKED
            )
            if shape is WriterShape.IDENTIFIER or not hit.has_span:
                declared = GateVerdict.BLOCKED
            verdict = max_verdict(verdict, declared)
        return verdict


def _redact(content: str, hits: Sequence[ScanHit]) -> str:
    """Replace each matched span with a category-labelled placeholder."""
    parts: list[str] = []
    cursor = 0
    for hit in hits:
        if not hit.has_span:
            continue
        start, end = hit.start, hit.end
        if start is None or end is None or start < cursor:
            continue
        parts.append(content[cursor:start])
        parts.append(_PLACEHOLDER.format(category=hit.category.value))
        cursor = end
    parts.append(content[cursor:])
    return "".join(parts)
