"""Aggregates belong on event surfaces, never on descriptions read as current."""

import pytest
from pydantic import ValidationError

from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.core.config import AppConfig
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
    SurfaceDurability,
    WriterShape,
    durability_of,
)
from tests.fakes import FakeContentScanner


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
