"""Outbound content gate: verdicts, redaction form, scanners (KOD-47)."""

import pytest

from kodezart.adapters.outbound_admission import OutboundAdmission
from kodezart.types.domain.gating import (
    REDACTION_VERDICTS,
    ContentClass,
    GateVerdict,
    OutboundDestination,
    RedactionCategory,
    RepoVisibility,
    ScanHit,
    WriterShape,
    max_verdict,
)
from tests.fakes import FakeContentJudgment
from tests.outbound import LiteralJudgment, make_admission

REDACT_PATTERNS = {
    RedactionCategory.TRACKER_URLS: ["TRACKER-42", "TRACKER-1", "TRACKER-2"]
}
BLOCK_PATTERNS = {RedactionCategory.INFRA_ENDPOINTS: ["infra.internal"]}


def make_gate(literals) -> OutboundAdmission:
    """The fixed gate with a recorded semantic finding at a fixture literal."""
    return make_admission(LiteralJudgment(literals))


# ---------------------------------------------------------------------------
# AC-3a — per-category verdicts, max-severity-wins
# ---------------------------------------------------------------------------


async def test_no_hits_is_clean() -> None:
    """A payload with no matches is CLEAN and passes through unchanged."""
    decision = await make_gate(REDACT_PATTERNS).gate(
        content="nothing to see",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.CLEAN
    assert decision.content == "nothing to see"
    assert decision.categories == ()


async def test_redact_category_hit_alone_yields_redacted() -> None:
    """A redact-category hit redacts, it does not block."""
    decision = await make_gate(REDACT_PATTERNS).gate(
        content="see TRACKER-42 for context",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.REDACTED
    assert decision.categories == (RedactionCategory.TRACKER_URLS,)


async def test_block_category_hit_alone_yields_blocked() -> None:
    """A block-category hit blocks and nothing survives to be written."""
    decision = await make_gate(BLOCK_PATTERNS).gate(
        content="ping infra.internal now",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert decision.content == ""
    assert decision.categories == (RedactionCategory.INFRA_ENDPOINTS,)


async def test_both_categories_yield_blocked() -> None:
    """Max severity wins across the whole payload."""
    decision = await make_gate({**REDACT_PATTERNS, **BLOCK_PATTERNS}).gate(
        content="TRACKER-1 and infra.internal",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
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


async def test_identifier_writer_blocks_on_a_redact_category_hit() -> None:
    """A git ref cannot carry a placeholder, so any hit blocks."""
    decision = await make_gate(REDACT_PATTERNS).gate(
        content="fix-TRACKER-42-thing",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.IDENTIFIER,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert "[REDACTED:" not in decision.content


# ---------------------------------------------------------------------------
# AC-3b — the redacted form
# ---------------------------------------------------------------------------


async def test_redacted_form_is_a_category_labelled_placeholder_per_span() -> None:
    """Each matched span becomes exactly one [REDACTED:<category>] token."""
    decision = await make_gate(REDACT_PATTERNS).gate(
        content="a TRACKER-1 b TRACKER-2 c",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
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
async def test_gate_engages_on_public_and_unknown_only(
    visibility: RepoVisibility,
    expected: GateVerdict,
) -> None:
    """Private targets see no behavioral change; UNKNOWN takes the public path."""
    decision = await make_gate(REDACT_PATTERNS).gate(
        content="see TRACKER-42",
        visibility=visibility,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is expected


@pytest.mark.parametrize("visibility", list(RepoVisibility))
async def test_unconfigured_deployment_is_clean_on_every_visibility(
    visibility: RepoVisibility,
) -> None:
    """Ordinary text still passes the populated credential and URL defaults."""
    gate = make_admission()
    decision = await gate.gate(
        content="feat: add the widget\n\nCloses the reported gap.",
        visibility=visibility,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.CLEAN


async def test_shipped_credential_category_still_blocks() -> None:
    """Shipped credential protection remains independent of workspace URL patterns."""
    gate = make_admission()
    token = "ghp_" + "A" * 40
    decision = await gate.gate(
        content=f"push failed for {token}",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert decision.categories == (RedactionCategory.CREDENTIALS,)


async def test_the_shipped_credential_category_blocks_the_knowledge_token() -> None:
    """Egress redaction and the gate are two surfaces over one credential class.

    A pattern added to only the redaction helper would leave this surface
    blind to the credential the knowledge layer introduces.
    """
    gate = make_admission()
    token = "ntn_" + "A" * 44
    decision = await gate.gate(
        content=f"knowledge call failed for {token}",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert decision.categories == (RedactionCategory.CREDENTIALS,)


# ---------------------------------------------------------------------------
# AC-5 — engine reuse and the scanner-ordering seam
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AC-9 — every pattern originates in AppConfig
# ---------------------------------------------------------------------------


def test_shipped_category_verdicts_match_the_pinned_defaults() -> None:
    """Cross-repo / tracker / email redact; infra and credentials block."""
    verdicts = REDACTION_VERDICTS
    assert verdicts[RedactionCategory.CROSS_REPO_NAMES] is GateVerdict.REDACTED
    assert verdicts[RedactionCategory.TRACKER_URLS] is GateVerdict.REDACTED
    assert verdicts[RedactionCategory.EMAIL_HANDLES] is GateVerdict.REDACTED
    assert verdicts[RedactionCategory.INFRA_ENDPOINTS] is GateVerdict.BLOCKED
    assert verdicts[RedactionCategory.CREDENTIALS] is GateVerdict.BLOCKED
    assert verdicts[RedactionCategory.ORG_PRIVATE] is GateVerdict.REDACTED
    assert set(verdicts) == set(RedactionCategory)


@pytest.mark.parametrize("category", list(RedactionCategory))
@pytest.mark.parametrize("shape", list(WriterShape))
async def test_all_fixed_privacy_rows_are_applied(category, shape):
    judgment = FakeContentJudgment([ScanHit(category=category, start=0, end=4)])
    decision = await make_admission(judgment).gate(
        content="fact remaining",
        visibility=RepoVisibility.UNKNOWN,
        shape=shape,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    expected = (
        GateVerdict.BLOCKED
        if shape is WriterShape.IDENTIFIER
        else REDACTION_VERDICTS[category]
    )
    assert decision.verdict is expected
    assert decision.content == (
        "[REDACTED:" + category.value + "] remaining"
        if expected is GateVerdict.REDACTED
        else ""
    )


async def test_memo_keeps_identifier_admission_distinct_from_prose():
    gate = make_admission(
        FakeContentJudgment(
            [ScanHit(category=RedactionCategory.ORG_PRIVATE, start=0, end=4)]
        )
    )
    results = []
    for shape in [WriterShape.PROSE, WriterShape.IDENTIFIER]:
        results.append(
            await gate.gate(
                content="fact",
                visibility=RepoVisibility.PUBLIC,
                shape=shape,
                destination=OutboundDestination.PR_BODY,
                content_class=ContentClass.AUTHORED,
            )
        )
    assert [result.verdict for result in results] == [
        GateVerdict.REDACTED,
        GateVerdict.BLOCKED,
    ]
