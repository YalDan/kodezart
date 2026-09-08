"""Durability routing over the existing configured deny-pattern engine."""

import re
from collections.abc import Sequence

from kodezart.adapters.regex_content_scanner import RegexContentScanner, pattern_spans
from kodezart.types.domain.gating import (
    ContentClass,
    DurabilityCategory,
    OutboundDestination,
    OutboundSurface,
    ScanHit,
    ScannerRouting,
    ScanResult,
    SurfaceDurability,
    durability_of,
)


class AggregateContentScanner:
    """Find configured tracker aggregates on surfaces read as current.

    Matching belongs to ``RegexContentScanner``. This adapter only builds
    its configured pattern set and chooses whether the destination is
    durable; appended events need no aggregate scan.
    """

    def __init__(
        self,
        *,
        tracker_object_nouns: Sequence[str],
        count_token_distance: int,
        issue_identifier_pattern: str,
        identifier_separator_pattern: str,
        identifier_roster_min_length: int,
    ) -> None:
        nouns = "|".join(re.escape(noun) for noun in tracker_object_nouns)
        noun = rf"\b(?:{nouns})\b"
        # Identifier suffixes and decimal fragments are not standalone counts.
        numeral = r"(?<![\w.-])\d+(?:[.,]\d+)*(?!\w|[.,]\d)"
        gap = rf"(?:\W+\w+){{0,{count_token_distance}}}\W+"
        identifier = rf"(?:{issue_identifier_pattern})"
        separator = rf"(?:{identifier_separator_pattern})"
        repetitions = identifier_roster_min_length - 1
        self._identifier_pattern = re.compile(issue_identifier_pattern)
        self._scanner = RegexContentScanner(
            patterns={
                DurabilityCategory.OBJECT_COUNT: [
                    rf"(?i){numeral}{gap}{noun}",
                    rf"(?i){noun}{gap}{numeral}",
                ],
                DurabilityCategory.IDENTIFIER_ROSTER: [
                    rf"{identifier}(?:{separator}{identifier}){{{repetitions},}}",
                ],
            },
        )

    @property
    def routing(self) -> ScannerRouting:
        """Legacy derived values retain coverage until typed writer adoption."""
        return ScannerRouting(
            surfaces=frozenset(OutboundSurface),
            content_classes=frozenset({ContentClass.DERIVED}),
        )

    async def scan(
        self,
        *,
        content: str,
        destination: OutboundDestination,
    ) -> ScanResult:
        if durability_of(destination) is SurfaceDurability.POINT_IN_TIME:
            return ScanResult()
        result = await self._scanner.scan(content=content, destination=destination)
        identifiers = tuple(pattern_spans(self._identifier_pattern, content))
        # Only aggregate hits carry their source text into a repair error.
        # The privacy scanner never copies its matched secrets into a hit.
        return result.model_copy(
            update={
                "hits": tuple(
                    hit.model_copy(
                        update={"matched_text": content[hit.start : hit.end]},
                    )
                    for hit in result.hits
                    if not _count_is_reference(hit, content, identifiers)
                ),
            },
        )


def _count_is_reference(
    hit: ScanHit,
    content: str,
    identifiers: Sequence[tuple[int, int]],
) -> bool:
    """Exclude an identifier's number, not a genuine count beside a reference.

    The count grammar has its numeral at one endpoint. Only that endpoint
    matters: a wider configured gap may contain an intervening identifier,
    which does not turn the separate count into an identifier.
    """
    if hit.category is not DurabilityCategory.OBJECT_COUNT:
        return False
    start, end = hit.start, hit.end
    if start is None or end is None or not hit.has_span:
        return False
    numeral_position = start if content[start].isdecimal() else end - 1
    return any(begin <= numeral_position < finish for begin, finish in identifiers)
