"""Fixed outbound admission: credentials, native references, authored judgment."""

import re
from collections.abc import Sequence

from kodezart.adapters.reference_content_scanner import ReferenceContentScanner
from kodezart.core.protocols import ContentJudgment
from kodezart.types.domain.credentials import CREDENTIAL_SHAPES
from kodezart.types.domain.gating import (
    REDACTION_VERDICTS,
    ContentClass,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    OutboundSurface,
    RedactionCategory,
    RepoVisibility,
    ScanHit,
    ScanResult,
    SurfaceDurability,
    WriterShape,
    content_digest,
    durability_of,
    max_verdict,
    surface_of,
)

_PLACEHOLDER = "[REDACTED:{category}]"

#: A decision depends on the exact text, source facts, destination, provenance
#: and writer shape. Prose redaction must never authorize an identifier.
type _MemoKey = tuple[str, OutboundDestination, str, ContentClass, WriterShape]


_CREDENTIAL_PATTERNS = tuple(re.compile(shape.pattern) for shape in CREDENTIAL_SHAPES)


def credential_hits(content: str) -> tuple[ScanHit, ...]:
    """Local matches from the same credential table used by error egress."""
    return tuple(
        ScanHit(
            category=RedactionCategory.CREDENTIALS, start=match.start(), end=match.end()
        )
        for pattern in _CREDENTIAL_PATTERNS
        for match in pattern.finditer(content)
        if match.end() > match.start()
    )


class OutboundAdmission:
    """Admit an actual write using the fixed local and semantic checks."""

    def __init__(
        self,
        *,
        references: ReferenceContentScanner,
        judgment: ContentJudgment,
        fragment_digest: str = "",
    ) -> None:
        self._references = references
        self._judgment = judgment
        self._fragment_digest = fragment_digest
        self._memo: dict[_MemoKey, GateDecision] = {}

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
            shape,
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
        """Credentials block locally before references or any model call."""
        hits = list(credential_hits(content))
        if not hits:
            references = await self._references.scan(
                content=content, destination=destination
            )
            if references.failure is not None:
                return _failed(references)
            hits.extend(references.hits)
            if (
                self._fold(hits, shape=shape) is not GateVerdict.BLOCKED
                and (
                    content_class is ContentClass.AUTHORED
                    or destination is OutboundDestination.BRANCH_NAME
                )
                and (
                    durability_of(destination) is SurfaceDurability.DURABLE
                    or surface_of(destination) is not OutboundSurface.REPOSITORY
                )
            ):
                judgment = await self._judgment.scan(
                    content=content, destination=destination
                )
                if judgment.failure is not None:
                    return _failed(judgment)
                hits.extend(judgment.hits)

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
                REDACTION_VERDICTS[hit.category]
                if isinstance(hit.category, RedactionCategory)
                else GateVerdict.BLOCKED
            )
            if shape is WriterShape.IDENTIFIER or not hit.has_span:
                declared = GateVerdict.BLOCKED
            verdict = max_verdict(verdict, declared)
        return verdict


def _failed(result: ScanResult) -> GateDecision:
    return GateDecision(verdict=GateVerdict.BLOCKED, content="", failure=result.failure)


def _redact(content: str, hits: Sequence[ScanHit]) -> str:
    """Replace each matched span with a category-labelled placeholder."""
    parts: list[str] = []
    cursor = 0
    for hit in hits:
        if not hit.has_span:
            continue
        start, end = hit.start, hit.end
        if start is None or end is None:
            continue
        if start < cursor:
            # A previous placeholder already covers this overlap, but its
            # shorter span must not leave the rest of this finding visible.
            cursor = max(cursor, end)
            continue
        parts.append(content[cursor:start])
        parts.append(_PLACEHOLDER.format(category=hit.category.value))
        cursor = end
    parts.append(content[cursor:])
    return "".join(parts)
