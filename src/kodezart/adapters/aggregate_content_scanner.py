"""Durability routing over the existing configured deny-pattern engine."""

import re
from collections.abc import Sequence

from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.types.domain.gating import (
    UNCONDITIONAL_ROUTING,
    DurabilityCategory,
    OutboundDestination,
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
        numeral = r"(?<![\w.-])\d+(?:[.,]\d+)*(?![\w.-])"
        gap = rf"(?:\W+\w+){{0,{count_token_distance}}}\W+"
        identifier = rf"(?:{issue_identifier_pattern})"
        separator = rf"(?:{identifier_separator_pattern})"
        repetitions = identifier_roster_min_length - 1
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
        """Computed and authored aggregates use the same deterministic rule."""
        return UNCONDITIONAL_ROUTING

    async def scan(
        self,
        *,
        content: str,
        destination: OutboundDestination,
    ) -> ScanResult:
        if durability_of(destination) is SurfaceDurability.POINT_IN_TIME:
            return ScanResult()
        return await self._scanner.scan(content=content, destination=destination)
