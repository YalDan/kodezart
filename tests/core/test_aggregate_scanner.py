"""Aggregates belong on event surfaces, never on descriptions read as current."""

import pytest
from pydantic import ValidationError

from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.composition.gating import outbound_scanners
from kodezart.core.config import AppConfig
from kodezart.core.protocols import ContentScanner
from kodezart.types.domain.gating import (
    DESTINATION_DURABILITY,
    UNCONDITIONAL_ROUTING,
    ContentClass,
    DurabilityCategory,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RedactionCategory,
    RepoVisibility,
    ScanCategory,
    ScanHit,
    ScanResult,
    SurfaceDurability,
    WriterShape,
    durability_of,
)
from kodezart.types.domain.skills import SkillsMode, SkillsSelection
from tests.fakes import FakeAgentExecutor, FakeContentScanner
from tests.prompts.test_prompt_wiring import load_registry


def test_every_real_writer_has_a_durability_classification() -> None:
    """Adding a destination without deciding how it is read fails this gate."""
    assert set(DESTINATION_DURABILITY) == set(OutboundDestination)


@pytest.mark.parametrize(
    "destination",
    [
        OutboundDestination.BRANCH_NAME,
        OutboundDestination.PR_TITLE,
        OutboundDestination.PR_BODY,
        OutboundDestination.ARTIFACT_TICKET_JSON,
        OutboundDestination.ARTIFACT_CRITERIA_JSON,
    ],
)
def test_current_descriptions_and_artifacts_are_durable(
    destination: OutboundDestination,
) -> None:
    assert durability_of(destination) is SurfaceDurability.DURABLE


@pytest.mark.parametrize(
    "destination",
    [
        OutboundDestination.PR_COMMENT,
        OutboundDestination.TRACKER_COMMENT,
        OutboundDestination.COMMIT_MESSAGE,
        OutboundDestination.COMMIT_MESSAGE_DIVERGENCE_REPLAY,
    ],
)
def test_appended_events_are_point_in_time(
    destination: OutboundDestination,
) -> None:
    assert durability_of(destination) is SurfaceDurability.POINT_IN_TIME


def test_a_write_without_a_destination_member_is_durable() -> None:
    assert durability_of(None) is SurfaceDurability.DURABLE


def test_scan_category_vocabularies_are_disjoint() -> None:
    """Every union member owns distinct wire values, so decoding is unambiguous."""
    from typing import get_args

    seen: set[str] = set()
    for vocabulary in get_args(ScanCategory.__value__):
        values = {member.value for member in vocabulary}
        assert values.isdisjoint(seen)
        seen.update(values)
    assert DurabilityCategory.OBJECT_COUNT.value == "object_count"
    assert DurabilityCategory.IDENTIFIER_ROSTER.value == "identifier_roster"


@pytest.mark.parametrize("category", list(DurabilityCategory))
async def test_aggregate_categories_block_without_a_redaction_option(
    category: DurabilityCategory,
) -> None:
    """The existing gate's non-redaction arm handles both added categories."""
    hit = ScanHit(category=category, start=0, end=8)
    gate = PatternOutboundContentGate(
        scanners=[FakeContentScanner([hit], routing=UNCONDITIONAL_ROUTING)],
        verdicts=dict.fromkeys(RedactionCategory, GateVerdict.REDACTED),
    )
    decision = await gate.gate(
        content="3 issues remain",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.DERIVED,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert decision.content == ""
    assert decision.categories == (category,)
    assert GateDecision.model_validate_json(decision.model_dump_json()) == decision


def test_aggregate_thresholds_bind_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KODEZART_AGGREGATE_COUNT_TOKEN_DISTANCE", "7")
    monkeypatch.setenv("KODEZART_AGGREGATE_IDENTIFIER_ROSTER_MIN_LENGTH", "5")
    config = AppConfig.from_env()
    assert config.aggregate_count_token_distance == 7
    assert config.aggregate_identifier_roster_min_length == 5


@pytest.mark.parametrize(
    "field_and_value",
    [
        {"aggregate_count_token_distance": -1},
        {"aggregate_identifier_roster_min_length": 1},
    ],
)
def test_aggregate_thresholds_cannot_match_a_single_identifier_or_negative_gap(
    field_and_value: dict[str, int],
) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(field_and_value)


def configured_scanners(config: AppConfig) -> list[ContentScanner]:
    """Exercise the same scanner assembly the application uses at boot."""
    scanners, _ = outbound_scanners(
        config=config,
        operation=None,
        executor=FakeAgentExecutor([]),
        prompts=load_registry(),
        skills=SkillsSelection(mode=SkillsMode.NONE),
    )
    return scanners


def configured_gate(config: AppConfig | None = None) -> PatternOutboundContentGate:
    selected = config or AppConfig(agentic_content_scanner_enabled=False)
    return PatternOutboundContentGate(
        scanners=configured_scanners(selected),
        verdicts=selected.deny_pattern_verdicts,
    )


async def test_registered_aggregate_scanner_uses_the_existing_regex_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scans: list[str] = []
    original_scan = RegexContentScanner.scan

    async def observed_scan(
        self: RegexContentScanner,
        *,
        content: str,
        destination: OutboundDestination,
    ) -> ScanResult:
        scans.append(content)
        return await original_scan(self, content=content, destination=destination)

    monkeypatch.setattr(RegexContentScanner, "scan", observed_scan)
    decision = await configured_gate().gate(
        content="There are 3 issues remaining.",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.DERIVED,
    )
    assert scans == [
        "There are 3 issues remaining.",
        "There are 3 issues remaining.",
    ]
    assert decision.verdict is GateVerdict.BLOCKED
    assert decision.categories == (DurabilityCategory.OBJECT_COUNT,)


async def test_aggregate_block_wins_over_an_earlier_redaction() -> None:
    config = AppConfig(
        agentic_content_scanner_enabled=False,
        deny_patterns={RedactionCategory.TRACKER_URLS: [r"example\.invalid"]},
    )
    decision = await configured_gate(config).gate(
        content="example.invalid: 5 tickets remain",
        visibility=RepoVisibility.UNKNOWN,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert set(decision.categories) == {
        RedactionCategory.TRACKER_URLS,
        DurabilityCategory.OBJECT_COUNT,
    }
    assert decision.content == ""
