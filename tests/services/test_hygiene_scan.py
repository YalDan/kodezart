"""The pre-promotion hygiene scan: one engine, a second pattern set.

The reuse claim is the point of this module, so it is checked three ways:
the scan reaches a body through the port's own ``scan`` entry point (a
recording double proves the call and its arguments), the shipped patterns
are exercised through the shipped ``RegexContentScanner`` rather than a
copy, and the service module is read for any sign of a scanning engine of
its own.
"""

import ast
from collections.abc import Sequence
from pathlib import Path

from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.core.config import AppConfig
from kodezart.services.hygiene_scan import HygieneScan
from kodezart.types.domain.gating import (
    UNCONDITIONAL_ROUTING,
    HygieneCategory,
    OutboundDestination,
    RedactionCategory,
    ScanFailureKind,
    ScanHit,
    ScannerRouting,
    ScanResult,
)

SERVICE_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "kodezart"
    / "services"
    / "hygiene_scan.py"
)

DESTINATION = OutboundDestination.TRACKER_COMMENT

#: One body per shipped category, each written to trip exactly that
#: category.  A body listed here that stops tripping its category is a
#: pattern set that quietly stopped covering what it claims to cover.
OFFENDING_BODIES: dict[HygieneCategory, str] = {
    HygieneCategory.ORCHESTRATION_VOCABULARY: (
        "Move the label to queue:approved once the work is understood."
    ),
    HygieneCategory.TRACKER_SHORTHAND: "Finish this the way KOD-42 was finished.",
    HygieneCategory.EVALUATOR_MATERIAL: (
        "Acceptance criteria: the parser rejects a trailing comma."
    ),
}

CLEAN_BODY = (
    "The importer accepts a trailing comma in a list literal and should "
    "reject it, naming the offending line."
)


class RecordingScanner:
    """A ``ContentScanner`` that records the entry point it was called on."""

    def __init__(self, *, result: ScanResult) -> None:
        self.calls: list[tuple[str, OutboundDestination]] = []
        self._result: ScanResult = result

    @property
    def routing(self) -> ScannerRouting:
        return UNCONDITIONAL_ROUTING

    async def scan(
        self,
        *,
        content: str,
        destination: OutboundDestination,
    ) -> ScanResult:
        self.calls.append((content, destination))
        return self._result


def shipped_scan() -> HygieneScan:
    """The scan as boot builds it: the shipped engine, the shipped set."""
    return HygieneScan(
        scanner=RegexContentScanner(patterns=AppConfig().hygiene_patterns),
    )


async def test_the_scan_reaches_the_body_through_the_port_entry_point() -> None:
    """AC-18: the quality set runs through ``ContentScanner.scan``, not a copy."""
    scanner = RecordingScanner(result=ScanResult())
    report = await HygieneScan(scanner=scanner).inspect(
        body=CLEAN_BODY,
        destination=DESTINATION,
    )

    assert scanner.calls == [(CLEAN_BODY, DESTINATION)]
    assert report.promotable


async def test_every_shipped_category_stops_a_body_that_trips_it() -> None:
    """The pattern set is exercised through the engine it is registered on."""
    for category, body in OFFENDING_BODIES.items():
        report = await shipped_scan().inspect(body=body, destination=DESTINATION)
        assert not report.promotable, category
        assert category in report.categories, category


async def test_a_body_an_implementer_can_act_on_alone_is_promotable() -> None:
    """The scan is not a blanket refusal: clean prose passes."""
    report = await shipped_scan().inspect(body=CLEAN_BODY, destination=DESTINATION)

    assert report.promotable
    assert report.hits == ()
    assert report.categories == ()


async def test_one_body_tripping_two_categories_reports_both_once() -> None:
    """Categories are the distinct reasons, in the order the body raised them."""
    body = "Acceptance criteria for KOD-42: the parser rejects a trailing comma."
    report = await shipped_scan().inspect(body=body, destination=DESTINATION)

    assert not report.promotable
    assert set(report.categories) == {
        HygieneCategory.EVALUATOR_MATERIAL,
        HygieneCategory.TRACKER_SHORTHAND,
    }
    assert len(report.categories) == len(set(report.categories))


async def test_a_scanner_that_could_not_answer_refuses_promotion() -> None:
    """Three states, none silent: "no answer" is not "clean"."""
    scanner = RecordingScanner(
        result=ScanResult(failure=ScanFailureKind.TRANSPORT_ERROR),
    )
    report = await HygieneScan(scanner=scanner).inspect(
        body=CLEAN_BODY,
        destination=DESTINATION,
    )

    assert not report.promotable
    assert report.failure is ScanFailureKind.TRANSPORT_ERROR
    assert report.hits == ()


def test_the_service_module_carries_no_scanning_engine_of_its_own() -> None:
    """AC-18: a second scanner implementation is the failure mode guarded here."""
    tree = ast.parse(SERVICE_SOURCE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    assert "re" not in imported
    assert not [name for name in imported if name.startswith("kodezart.adapters")]


def test_no_hygiene_category_collides_with_a_redaction_category() -> None:
    """``ScanCategory`` is a union; two enums sharing a value would be ambiguous."""
    hygiene = {member.value for member in HygieneCategory}
    redaction = {member.value for member in RedactionCategory}

    assert hygiene & redaction == set()


def test_a_hit_round_trips_under_either_half_of_the_union() -> None:
    """A serialized hit must come back as the category it went out as."""
    for member in (HygieneCategory.TRACKER_SHORTHAND, RedactionCategory.CREDENTIALS):
        restored = ScanHit.model_validate(ScanHit(category=member).model_dump())
        assert restored.category is member


def test_the_shipped_set_covers_every_hygiene_category() -> None:
    """A category with no patterns is a rule nobody enforces."""
    patterns: dict[HygieneCategory, Sequence[str]] = AppConfig().hygiene_patterns

    assert set(patterns) == set(HygieneCategory)
    assert all(patterns[category] for category in HygieneCategory)
