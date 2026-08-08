"""Outbound content gate: verdicts, redaction form, scanners (KOD-47)."""

import pytest

from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.core.config import AppConfig
from kodezart.types.domain.gating import (
    GateVerdict,
    RedactionCategory,
    RepoVisibility,
    ScanHit,
    WriterShape,
    max_verdict,
)
from tests.fakes import FakeContentScanner

REDACT_PATTERNS = {RedactionCategory.TRACKER_URLS: [r"TRACKER-\d+"]}
BLOCK_PATTERNS = {RedactionCategory.INFRA_ENDPOINTS: [r"infra\.internal"]}


def make_gate(
    patterns: dict[RedactionCategory, list[str]],
) -> PatternOutboundContentGate:
    """Build a gate over one configured pattern set with shipped verdicts."""
    return PatternOutboundContentGate(
        scanners=[RegexContentScanner(patterns=patterns)],
        verdicts=AppConfig().deny_pattern_verdicts,
    )


# ---------------------------------------------------------------------------
# AC-3a — per-category verdicts, max-severity-wins
# ---------------------------------------------------------------------------


def test_no_hits_is_clean() -> None:
    """A payload with no matches is CLEAN and passes through unchanged."""
    decision = make_gate(REDACT_PATTERNS).gate(
        content="nothing to see",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
    )
    assert decision.verdict is GateVerdict.CLEAN
    assert decision.content == "nothing to see"
    assert decision.categories == ()


def test_redact_category_hit_alone_yields_redacted() -> None:
    """A redact-category hit redacts, it does not block."""
    decision = make_gate(REDACT_PATTERNS).gate(
        content="see TRACKER-42 for context",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
    )
    assert decision.verdict is GateVerdict.REDACTED
    assert decision.categories == (RedactionCategory.TRACKER_URLS,)


def test_block_category_hit_alone_yields_blocked() -> None:
    """A block-category hit blocks and nothing survives to be written."""
    decision = make_gate(BLOCK_PATTERNS).gate(
        content="ping infra.internal now",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert decision.content == ""
    assert decision.categories == (RedactionCategory.INFRA_ENDPOINTS,)


def test_both_categories_yield_blocked() -> None:
    """Max severity wins across the whole payload."""
    decision = make_gate({**REDACT_PATTERNS, **BLOCK_PATTERNS}).gate(
        content="TRACKER-1 and infra.internal",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert set(decision.categories) == {
        RedactionCategory.TRACKER_URLS,
        RedactionCategory.INFRA_ENDPOINTS,
    }


def test_verdict_severity_ordering() -> None:
    """BLOCKED > REDACTED > CLEAN."""
    assert max_verdict(GateVerdict.CLEAN, GateVerdict.REDACTED) is GateVerdict.REDACTED
    assert max_verdict(GateVerdict.REDACTED, GateVerdict.BLOCKED) is GateVerdict.BLOCKED
    assert max_verdict(GateVerdict.BLOCKED, GateVerdict.CLEAN) is GateVerdict.BLOCKED


def test_identifier_writer_blocks_on_a_redact_category_hit() -> None:
    """A git ref cannot carry a placeholder, so any hit blocks."""
    decision = make_gate(REDACT_PATTERNS).gate(
        content="fix-TRACKER-42-thing",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.IDENTIFIER,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert "[REDACTED:" not in decision.content


# ---------------------------------------------------------------------------
# AC-3b — the redacted form
# ---------------------------------------------------------------------------


def test_redacted_form_is_a_category_labelled_placeholder_per_span() -> None:
    """Each matched span becomes exactly one [REDACTED:<category>] token."""
    decision = make_gate(REDACT_PATTERNS).gate(
        content="a TRACKER-1 b TRACKER-2 c",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
    )
    assert decision.content == ("a [REDACTED:tracker_urls] b [REDACTED:tracker_urls] c")


# ---------------------------------------------------------------------------
# AC-1 / AC-4 — the visibility matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("visibility", "expected"),
    [
        (RepoVisibility.PRIVATE, GateVerdict.CLEAN),
        (RepoVisibility.PUBLIC, GateVerdict.REDACTED),
        (RepoVisibility.UNKNOWN, GateVerdict.REDACTED),
    ],
)
def test_gate_engages_on_public_and_unknown_only(
    visibility: RepoVisibility,
    expected: GateVerdict,
) -> None:
    """Private targets see no behavioral change; UNKNOWN takes the public path."""
    decision = make_gate(REDACT_PATTERNS).gate(
        content="see TRACKER-42",
        visibility=visibility,
        shape=WriterShape.PROSE,
    )
    assert decision.verdict is expected


@pytest.mark.parametrize("visibility", list(RepoVisibility))
def test_unconfigured_deployment_is_clean_on_every_visibility(
    visibility: RepoVisibility,
) -> None:
    """AC-4: pattern sets ship empty except credentials, so ordinary text passes."""
    config = AppConfig()
    gate = PatternOutboundContentGate(
        scanners=[RegexContentScanner(patterns=config.deny_patterns)],
        verdicts=config.deny_pattern_verdicts,
    )
    decision = gate.gate(
        content="feat: add the widget\n\nCloses the reported gap.",
        visibility=visibility,
        shape=WriterShape.PROSE,
    )
    assert decision.verdict is GateVerdict.CLEAN


def test_shipped_credential_category_still_blocks() -> None:
    """The one category that ships populated: a credential never leaves."""
    config = AppConfig()
    gate = PatternOutboundContentGate(
        scanners=[RegexContentScanner(patterns=config.deny_patterns)],
        verdicts=config.deny_pattern_verdicts,
    )
    token = "ghp_" + "A" * 40
    decision = gate.gate(
        content=f"push failed for {token}",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert decision.categories == (RedactionCategory.CREDENTIALS,)


# ---------------------------------------------------------------------------
# AC-5 — engine reuse and the scanner-ordering seam
# ---------------------------------------------------------------------------


def test_a_second_pattern_set_runs_through_the_same_engine() -> None:
    """Reusability: only the configured pattern set changes, not the engine."""
    scanner = RegexContentScanner(
        patterns={RedactionCategory.CROSS_REPO_NAMES: [r"acme/[a-z-]+"]},
    )
    hits = scanner.scan("mirror of acme/other-repo here")
    assert [hit.category for hit in hits] == [RedactionCategory.CROSS_REPO_NAMES]


def test_a_second_registered_scanner_participates_with_no_gate_change() -> None:
    """The ordered scanner list is the zero-change seam for a later auditor."""
    extra = FakeContentScanner(
        [ScanHit(category=RedactionCategory.INFRA_ENDPOINTS, start=0, end=4)],
    )
    gate = PatternOutboundContentGate(
        scanners=[RegexContentScanner(patterns=REDACT_PATTERNS), extra],
        verdicts=AppConfig().deny_pattern_verdicts,
    )
    decision = gate.gate(
        content="host and TRACKER-9",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
    )
    assert extra.calls == ["host and TRACKER-9"]
    assert decision.verdict is GateVerdict.BLOCKED


def test_scanner_hits_are_returned_in_payload_order() -> None:
    """Ordered hits make the redaction rewrite deterministic."""
    scanner = RegexContentScanner(
        patterns={
            RedactionCategory.TRACKER_URLS: [r"TRACKER-\d+"],
            RedactionCategory.CROSS_REPO_NAMES: [r"acme/[a-z-]+"],
        },
    )
    hits = scanner.scan("acme/one then TRACKER-3")
    assert [hit.start for hit in hits] == sorted(hit.start for hit in hits)


# ---------------------------------------------------------------------------
# AC-9 — every pattern originates in AppConfig
# ---------------------------------------------------------------------------


def test_default_pattern_sets_ship_empty_except_credentials() -> None:
    """No deny-pattern literal in code beyond the shipped credential category."""
    patterns = AppConfig().deny_patterns
    assert set(patterns) == set(RedactionCategory)
    for category, entries in patterns.items():
        if category is RedactionCategory.CREDENTIALS:
            assert entries
        else:
            assert entries == []


def test_shipped_category_verdicts_match_the_pinned_defaults() -> None:
    """Cross-repo / tracker / email redact; infra and credentials block."""
    verdicts = AppConfig().deny_pattern_verdicts
    assert verdicts[RedactionCategory.CROSS_REPO_NAMES] is GateVerdict.REDACTED
    assert verdicts[RedactionCategory.TRACKER_URLS] is GateVerdict.REDACTED
    assert verdicts[RedactionCategory.EMAIL_HANDLES] is GateVerdict.REDACTED
    assert verdicts[RedactionCategory.INFRA_ENDPOINTS] is GateVerdict.BLOCKED
    assert verdicts[RedactionCategory.CREDENTIALS] is GateVerdict.BLOCKED


def test_pattern_sets_are_configurable_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All knobs via AppConfig — no magic constants."""
    monkeypatch.setenv(
        "KODEZART_DENY_PATTERNS",
        '{"tracker_urls": ["ISSUE-[0-9]+"]}',
    )
    assert AppConfig.from_env().deny_patterns == {
        RedactionCategory.TRACKER_URLS: ["ISSUE-[0-9]+"],
    }
