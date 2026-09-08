"""Aggregates belong on event surfaces, never on descriptions read as current."""

import pytest
from pydantic import ValidationError

from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.composition.gating import outbound_scanners
from kodezart.core.config import AppConfig
from kodezart.core.logging import get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.protocols import ContentScanner
from kodezart.domain.errors import OutboundContentBlockedError
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


@pytest.mark.parametrize(
    "url",
    [
        "https://linear.app/example-workspace/issue/EX-1/a-title",
        "https://linear.app/example-workspace/project/a-project-123",
        "https://linear.app/example-workspace/initiative/a-scope-456",
        "HTTPS://LINEAR.APP/example-workspace/issue/EX-2?tab=activity#comment-abc",
        "https://app.notion.com/p/0123456789abcdef0123456789abcdef",
        "https://app.notion.com/p/01234567-89ab-cdef-0123-456789abcdef?pvs=204",
    ],
)
async def test_native_workspace_urls_use_the_existing_tracker_category(url):
    content = f"Read <{url}> before work."
    decision = await configured_gate().gate(
        content=content,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.DERIVED,
    )
    assert decision.verdict is GateVerdict.REDACTED
    assert decision.categories == (RedactionCategory.TRACKER_URLS,)
    assert url not in decision.content
    assert decision.content.startswith("Read <")
    assert decision.content.endswith("> before work.")


@pytest.mark.parametrize(
    "content",
    [
        "https://linear.app/",
        "https://linear.app/docs/issues",
        "https://linear.app.evil.invalid/example/issue/EX-1",
        "https://app.notion.com.evil.invalid/p/0123456789abcdef0123456789abcdef",
        "https://example.invalid/example/issue/EX-1",
        "example-workspace",
    ],
)
async def test_url_defaults_do_not_supply_workspace_name_patterns(content):
    decision = await configured_gate().gate(
        content=content,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.DERIVED,
    )
    assert decision.verdict is GateVerdict.CLEAN
    assert decision.content == content


@pytest.mark.parametrize("patterns", [[], ["synthetic-organisation"]])
def test_workspace_name_pattern_category_is_still_rejected(patterns):
    with pytest.raises(ValidationError):
        AppConfig(deny_patterns={RedactionCategory.ORG_PRIVATE: patterns})


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


@pytest.mark.parametrize("visibility", [RepoVisibility.PUBLIC, RepoVisibility.UNKNOWN])
@pytest.mark.parametrize("content_class", list(ContentClass))
@pytest.mark.parametrize(
    "content",
    ["3 issues remain", "ABC-1, ABC-2, ABC-3"],
)
async def test_identical_aggregate_bytes_block_only_on_current_surfaces(
    visibility: RepoVisibility,
    content_class: ContentClass,
    content: str,
) -> None:
    """One memoized gate must still distinguish the two destinations."""
    gate = configured_gate()
    durable = await gate.gate(
        content=content,
        visibility=visibility,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=content_class,
    )
    event = await gate.gate(
        content=content,
        visibility=visibility,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.TRACKER_COMMENT,
        content_class=content_class,
    )
    assert durable.verdict is GateVerdict.BLOCKED
    assert event.verdict is GateVerdict.CLEAN
    assert event.content == content


@pytest.mark.parametrize("destination", list(OutboundDestination))
async def test_single_identifier_is_a_reference_on_every_surface(
    destination: OutboundDestination,
) -> None:
    decision = await configured_gate().gate(
        content="See ABC-42 for the details.",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=destination,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.CLEAN


@pytest.mark.parametrize("distance", [0, 1, 3, 7])
@pytest.mark.parametrize("noun_first", [False, True])
async def test_object_count_distance_uses_the_configured_boundary(
    distance: int,
    noun_first: bool,
) -> None:
    gate = configured_gate(
        AppConfig(
            agentic_content_scanner_enabled=False,
            aggregate_count_token_distance=distance,
        ),
    )
    for intervening, expected in [
        (distance, GateVerdict.BLOCKED),
        (distance + 1, GateVerdict.CLEAN),
    ]:
        ends = ("issues", "5") if noun_first else ("5", "issues")
        content = " ".join([ends[0], *(["open"] * intervening), ends[1]])
        decision = await gate.gate(
            content=content,
            visibility=RepoVisibility.PUBLIC,
            shape=WriterShape.PROSE,
            destination=OutboundDestination.PR_BODY,
            content_class=ContentClass.DERIVED,
        )
        assert decision.verdict is expected, content


@pytest.mark.parametrize("minimum", [2, 4, 7])
async def test_identifier_roster_uses_the_configured_run_boundary(minimum: int) -> None:
    gate = configured_gate(
        AppConfig(
            agentic_content_scanner_enabled=False,
            aggregate_identifier_roster_min_length=minimum,
        ),
    )
    for length, expected in [
        (minimum - 1, GateVerdict.CLEAN),
        (minimum, GateVerdict.BLOCKED),
    ]:
        content = ", ".join(f"ABC-{index}" for index in range(length))
        decision = await gate.gate(
            content=content,
            visibility=RepoVisibility.PUBLIC,
            shape=WriterShape.PROSE,
            destination=OutboundDestination.PR_BODY,
            content_class=ContentClass.AUTHORED,
        )
        assert decision.verdict is expected, content


async def test_roster_grammar_is_configuration_and_prose_breaks_a_run() -> None:
    gate = configured_gate(
        AppConfig(
            agentic_content_scanner_enabled=False,
            aggregate_issue_identifier_pattern=r"WORK/\d+",
            aggregate_identifier_separator_pattern=r"\s*~\s*",
        ),
    )
    for content, expected in [
        ("WORK/1 ~ WORK/2 ~ WORK/3", GateVerdict.BLOCKED),
        ("WORK/1, WORK/2, WORK/3", GateVerdict.CLEAN),
        ("ABC-1 ~ ABC-2 ~ ABC-3", GateVerdict.CLEAN),
        ("WORK/1 depends on WORK/2 ~ WORK/3", GateVerdict.CLEAN),
    ]:
        decision = await gate.gate(
            content=content,
            visibility=RepoVisibility.PUBLIC,
            shape=WriterShape.PROSE,
            destination=OutboundDestination.PR_BODY,
            content_class=ContentClass.AUTHORED,
        )
        assert decision.verdict is expected, content


async def test_event_destination_never_enters_the_aggregate_pattern_engine(
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
        content="3 issues remain",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.TRACKER_COMMENT,
        content_class=ContentClass.DERIVED,
    )
    assert decision.verdict is GateVerdict.CLEAN
    assert scans == ["3 issues remain"]  # Only the credential/privacy pattern set.


@pytest.mark.parametrize(
    ("content", "matched"),
    [
        ("Résumé: 3 issues remain.", "3 issues"),
        ("References: ABC-1, ABC-2, ABC-3.", "ABC-1, ABC-2, ABC-3"),
    ],
)
async def test_blocked_write_names_the_exact_span_for_repair(
    content: str,
    matched: str,
) -> None:
    with pytest.raises(OutboundContentBlockedError) as excinfo:
        await gated_write(
            gate=configured_gate(),
            log=get_logger(__name__),
            content=content,
            visibility=RepoVisibility.PUBLIC,
            shape=WriterShape.PROSE,
            destination=OutboundDestination.PR_BODY,
            content_class=ContentClass.AUTHORED,
        )
    error = excinfo.value
    start = content.index(matched)
    end = start + len(matched)
    assert [(hit.start, hit.end, hit.matched_text) for hit in error.hits] == [
        (start, end, matched),
    ]
    assert f"start: {start}" in str(error)
    assert f"end: {end}" in str(error)
    assert repr(matched) in str(error)

    repaired = content[:start] + content[end:]
    assert (
        await gated_write(
            gate=configured_gate(),
            log=get_logger(__name__),
            content=repaired,
            visibility=RepoVisibility.PUBLIC,
            shape=WriterShape.PROSE,
            destination=OutboundDestination.PR_BODY,
            content_class=ContentClass.AUTHORED,
        )
        == repaired
    )


async def test_privacy_match_text_is_not_copied_into_the_blocked_error() -> None:
    secret = "ghp_" + "A" * 40
    with pytest.raises(OutboundContentBlockedError) as excinfo:
        await gated_write(
            gate=configured_gate(),
            log=get_logger(__name__),
            content=f"credential={secret}",
            visibility=RepoVisibility.PUBLIC,
            shape=WriterShape.PROSE,
            destination=OutboundDestination.PR_BODY,
            content_class=ContentClass.AUTHORED,
        )
    assert secret not in str(excinfo.value)
    assert all(hit.matched_text is None for hit in excinfo.value.hits)


@pytest.mark.parametrize(
    "content",
    [
        "3 tests passed.",
        "Changed 7 files in 2 commits.",
        "3 files fix issues.",
        "42 tests cover tickets.",
        "The commit closes ABC-42.",
        "ABC-1 depends on ABC-2 and ABC-3.",
    ],
)
async def test_shipped_patterns_leave_repository_counts_and_references_clean(
    content: str,
) -> None:
    decision = await configured_gate().gate(
        content=content,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.CLEAN
    assert decision.content == content


@pytest.mark.parametrize("noun", AppConfig().aggregate_tracker_object_nouns)
@pytest.mark.parametrize("noun_first", [False, True])
async def test_shipped_nouns_block_adjacent_counts_in_either_order(
    noun: str,
    noun_first: bool,
) -> None:
    content = f"{noun}: 3." if noun_first else f"3 {noun} remain."
    gate = configured_gate()
    durable = await gate.gate(
        content=content,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.DERIVED,
    )
    assert durable.verdict is GateVerdict.BLOCKED
    assert durable.categories == (DurabilityCategory.OBJECT_COUNT,)
    event = await gate.gate(
        content=content,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.TRACKER_COMMENT,
        content_class=ContentClass.DERIVED,
    )
    assert event.verdict is GateVerdict.CLEAN
    assert event.content == content


@pytest.mark.parametrize("number", ["1,234", "3.5", "900"])
async def test_noun_first_count_allows_sentence_punctuation(number: str) -> None:
    content = f"Issues: {number}."
    decision = await configured_gate().gate(
        content=content,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert [hit.matched_text for hit in decision.hits] == [f"Issues: {number}"]


async def test_configured_object_nouns_replace_the_shipped_vocabulary() -> None:
    config = AppConfig(
        agentic_content_scanner_enabled=False,
        aggregate_tracker_object_nouns=["work package", "work packages"],
    )
    for content, expected in [
        ("3 work packages remain", GateVerdict.BLOCKED),
        ("work packages: 3.", GateVerdict.BLOCKED),
        ("3 issues remain", GateVerdict.CLEAN),
        ("3 files remain", GateVerdict.CLEAN),
    ]:
        decision = await configured_gate(config).gate(
            content=content,
            visibility=RepoVisibility.PUBLIC,
            shape=WriterShape.PROSE,
            destination=OutboundDestination.PR_BODY,
            content_class=ContentClass.AUTHORED,
        )
        assert decision.verdict is expected, content


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("A 3-issue scope.", GateVerdict.BLOCKED),
        ("A 3-file change.", GateVerdict.CLEAN),
        ("ABC-3 issues are discussed here.", GateVerdict.CLEAN),
        ("See ISSUE-3 for the details.", GateVerdict.CLEAN),
    ],
)
async def test_count_adjectives_and_identifier_suffixes_are_distinct(
    content: str,
    expected: GateVerdict,
) -> None:
    decision = await configured_gate().gate(
        content=content,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is expected


@pytest.mark.parametrize(
    ("pattern", "reference"),
    [(r"#\d+", "#42"), (r"ISSUE/\d+", "ISSUE/42")],
)
@pytest.mark.parametrize("noun_first", [False, True])
async def test_configured_identifier_numbers_never_become_object_counts(
    pattern: str,
    reference: str,
    noun_first: bool,
) -> None:
    config = AppConfig(
        agentic_content_scanner_enabled=False,
        aggregate_issue_identifier_pattern=pattern,
    )
    content = (
        f"Issue {reference} explains the change."
        if noun_first
        else f"{reference} issue is already linked."
    )
    decision = await configured_gate(config).gate(
        content=content,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.CLEAN


@pytest.mark.parametrize(
    "content",
    ["3 ABC-42 issues", "issues ABC-42 3"],
)
async def test_a_reference_inside_the_configured_gap_does_not_hide_a_count(
    content: str,
) -> None:
    gate = configured_gate(
        AppConfig(
            agentic_content_scanner_enabled=False,
            aggregate_count_token_distance=2,
        ),
    )
    decision = await gate.gate(
        content=content,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert decision.categories == (DurabilityCategory.OBJECT_COUNT,)
    assert [hit.matched_text for hit in decision.hits] == [content]
