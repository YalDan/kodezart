"""The outbound corpus — the spine of the judgment gate's verification.

Every case is a payload x destination x visibility with a required verdict
and required surviving / absent substrings, built against a **fixture**
private-surface fragment describing a synthetic operation.  A corpus built
from the real description would publish exactly what this mechanism exists
to withhold, and passing under a synthetic fragment is what shows the
mechanism generalises rather than that one string was memorised.

**What this corpus measures, stated plainly because a permissive gate
creating false confidence is worse than a narrow one.**  It measures the
MECHANISM: that a judgment hit reaches the fold, is excised at exactly its
reported span while the surrounding text survives, that the destination
reaches the scanner and can change the verdict for a byte-identical
payload, and — the half that proves the increment rather than proving a
corpus exists — that the DETERMINISTIC scanner alone returns CLEAN on every
must-not-pass case, with the shipped pattern configuration.  It does not
measure the model.  Zero false negatives here is not zero in the wild.

The corpus is APPEND-ONLY.  Every real incident becomes a new case, and no
case is ever edited to match new behaviour.
"""

import re
from collections.abc import Sequence

import pytest

from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.core.config import AppConfig
from kodezart.types.domain.gating import (
    JUDGMENT_ROUTING,
    GateVerdict,
    OutboundDestination,
    RedactionCategory,
    RepoVisibility,
    ScanHit,
    ScannerRouting,
    ScanResult,
    WriterShape,
)

#: The synthetic operation the whole corpus is written against.
FIXTURE_WORKSPACE = "quarry-works"
FIXTURE_HOST = "runners.quarry-internal.invalid"
FIXTURE_CUSTOMER = "Northwind Aggregates"
FIXTURE_PERSON = "the founder who reviewed the pricing pilot on the Tuesday call"


class RecordedAuditScanner:
    """A judgment scanner double replaying a RECORDED audit verdict.

    It is handed the leaked values a recorded audit session reported for
    each destination and locates their spans in the payload.  Scripted
    rather than intelligent on purpose: what the corpus exercises through
    this double is everything AROUND the verdict — routing, the fold, span
    surgery, the destination discrimination — and a double that tried to be
    clever would be a second, unverified judgment implementation.
    """

    def __init__(
        self,
        leaks_by_destination: dict[OutboundDestination, Sequence[str]],
    ) -> None:
        self._leaks = leaks_by_destination
        self.calls: list[str] = []

    @property
    def routing(self) -> ScannerRouting:
        """The judgment routing, so the corpus exercises the real one."""
        return JUDGMENT_ROUTING

    async def scan(
        self,
        *,
        content: str,
        destination: OutboundDestination,
    ) -> ScanResult:
        self.calls.append(content)
        hits: list[ScanHit] = []
        for leak in self._leaks.get(destination, ()):
            for match in re.finditer(re.escape(leak), content):
                hits.append(
                    ScanHit(
                        category=RedactionCategory.ORG_PRIVATE,
                        start=match.start(),
                        end=match.end(),
                        rationale=f"discloses {leak!r} to a stranger",
                    ),
                )
        hits.sort(key=lambda hit: hit.sort_key())
        return ScanResult(hits=tuple(hits))


def judgment_gate(
    leaks_by_destination: dict[OutboundDestination, Sequence[str]],
) -> PatternOutboundContentGate:
    """The shipped ordered list: deterministic first, judgment second."""
    config = AppConfig()
    return PatternOutboundContentGate(
        scanners=[
            RegexContentScanner(patterns=config.deny_patterns),
            RecordedAuditScanner(leaks_by_destination),
        ],
        verdicts=config.deny_pattern_verdicts,
    )


def deterministic_gate() -> PatternOutboundContentGate:
    """The gate exactly as it shipped before the judgment half existed."""
    config = AppConfig()
    return PatternOutboundContentGate(
        scanners=[RegexContentScanner(patterns=config.deny_patterns)],
        verdicts=config.deny_pattern_verdicts,
    )


# ---------------------------------------------------------------------------
# C-1 — must not pass
# ---------------------------------------------------------------------------

MUST_NOT_PASS: list[tuple[str, str, str]] = [
    (
        "paraphrased internal decision",
        "We settled last week on shipping the gate before the scheduled passes, "
        "against the earlier plan to do it the other way round.",
        "against the earlier plan to do it the other way round",
    ),
    (
        "unreleased capability in prose",
        "Once the scope layer lands, a run will be able to open its own follow-up "
        "work items without anyone asking it to.",
        "open its own follow-up work items without anyone asking it to",
    ),
    (
        "person identifiable from context",
        f"Blocked pending review by {FIXTURE_PERSON}.",
        FIXTURE_PERSON,
    ),
    (
        "internal hostname in no pattern list",
        f"The check timed out talking to {FIXTURE_HOST} again.",
        FIXTURE_HOST,
    ),
    (
        "customer identity",
        f"This unblocks the {FIXTURE_CUSTOMER} pilot.",
        FIXTURE_CUSTOMER,
    ),
    (
        "cross-repository name in prose",
        "Ported from the quarry-works ledger service, which is not public.",
        "quarry-works ledger service",
    ),
]


@pytest.mark.parametrize(
    ("name", "payload", "leak"),
    MUST_NOT_PASS,
    ids=[case[0] for case in MUST_NOT_PASS],
)
async def test_c1_a_must_not_pass_case_does_not_pass(
    name: str,
    payload: str,
    leak: str,
) -> None:
    """C-1: each case is REDACTED or BLOCKED, never CLEAN."""
    decision = await judgment_gate(
        {OutboundDestination.PR_BODY: [leak]},
    ).gate(
        content=payload,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert decision.verdict is not GateVerdict.CLEAN, name
    assert leak not in decision.content
    assert RedactionCategory.ORG_PRIVATE in decision.categories
    assert all(hit.rationale for hit in decision.hits)


@pytest.mark.parametrize(
    ("name", "payload", "leak"),
    MUST_NOT_PASS,
    ids=[case[0] for case in MUST_NOT_PASS],
)
async def test_c1_the_deterministic_scanner_alone_calls_the_same_case_clean(
    name: str,
    payload: str,
    leak: str,
) -> None:
    """C-1, the half that proves the increment rather than the corpus."""
    decision = await deterministic_gate().gate(
        content=payload,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert decision.verdict is GateVerdict.CLEAN, name
    assert decision.content == payload


# ---------------------------------------------------------------------------
# C-2 — the named regression case, reconstructed with fixture values
# ---------------------------------------------------------------------------

REGRESSION_PR_BODY = (
    "Closes [ISS-101](https://tracker.invalid/quarry-works/issue/ISS-101) and "
    "[ISS-102](https://tracker.invalid/quarry-works/issue/ISS-102).\n\n"
    "Follow-up tracked in "
    "[ISS-103](https://tracker.invalid/quarry-works/issue/ISS-103)."
)


async def test_c2_the_observed_leak_is_redacted_not_deleted() -> None:
    """C-2: every leaked value absent, every identifier surviving.

    The correct fix in the observed incident was a redaction and not a
    deletion — the identifiers were kept and only the workspace segment
    removed, which is what preserved reviewability.  A gate that can only
    block gets turned off.
    """
    decision = await judgment_gate(
        {OutboundDestination.PR_BODY: [FIXTURE_WORKSPACE]},
    ).gate(
        content=REGRESSION_PR_BODY,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert decision.verdict is GateVerdict.REDACTED
    assert FIXTURE_WORKSPACE not in decision.content
    for identifier in ("ISS-101", "ISS-102", "ISS-103"):
        assert identifier in decision.content
    assert decision.content.count("[REDACTED:org_private]") == 3


async def test_c2_the_deterministic_scanner_alone_calls_it_clean() -> None:
    """C-2: the shipped gate would have let the observed leak through."""
    decision = await deterministic_gate().gate(
        content=REGRESSION_PR_BODY,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert decision.verdict is GateVerdict.CLEAN
    assert decision.content == REGRESSION_PR_BODY


# ---------------------------------------------------------------------------
# C-3 — the false-positive floor
# ---------------------------------------------------------------------------

MUST_PASS: list[tuple[str, str, OutboundDestination]] = [
    (
        "ordinary technical pull-request body",
        "Moves `max_verdict` out of `adapters/pattern_outbound_gate.py` into "
        "`types/domain/gating.py` and points the two call sites at it. "
        "Behaviour is unchanged; the goldens are byte-identical.",
        OutboundDestination.PR_BODY,
    ),
    (
        "commit message naming a public dependency and issue number",
        "fix(deps): pin pydantic-settings to the release that restores "
        "extra=forbid on nested models (#4411)",
        OutboundDestination.COMMIT_MESSAGE,
    ),
    (
        "criteria artifact whose leaves are identifiers and enum members",
        '{"criteria": ["AC-1", "AC-2"], "verdict": "passed", "sha": "9f2c1ab"}',
        OutboundDestination.ARTIFACT_CRITERIA_JSON,
    ),
    (
        "prose naming the private-surface categories without an instance",
        "The audit judges customer identities, member handles and internal "
        "hostnames, and names none of them here.",
        OutboundDestination.PR_COMMENT,
    ),
]


@pytest.mark.parametrize(
    ("name", "payload", "destination"),
    MUST_PASS,
    ids=[case[0] for case in MUST_PASS],
)
async def test_c3_an_ordinary_payload_passes_byte_identical(
    name: str,
    payload: str,
    destination: OutboundDestination,
) -> None:
    """C-3: CLEAN, and the content is the input unchanged."""
    decision = await judgment_gate({}).gate(
        content=payload,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=destination,
    )
    assert decision.verdict is GateVerdict.CLEAN, name
    assert decision.content == payload
    assert decision.categories == ()


# ---------------------------------------------------------------------------
# C-4 — the discriminating case: one string, two destinations, two verdicts
# ---------------------------------------------------------------------------

TRACKER_LINK_PAYLOAD = (
    "Handing back to the board: see "
    "https://tracker.invalid/quarry-works/issue/ISS-104 for the decision."
)


async def test_c4_one_payload_two_destinations_two_verdicts() -> None:
    """C-4: byte-identical payload, same visibility, verdicts differ.

    A tracker link is unremarkable inside the coordination system and a
    disclosure on a public code-hosting surface.  **No pattern set can
    satisfy both rows**, because a pattern sees only the payload.  This is
    the case to run first whenever the corpus is doubted.
    """
    gate = judgment_gate({OutboundDestination.PR_BODY: [FIXTURE_WORKSPACE]})

    published = await gate.gate(
        content=TRACKER_LINK_PAYLOAD,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    on_the_tracker = await gate.gate(
        content=TRACKER_LINK_PAYLOAD,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.TRACKER_COMMENT,
    )

    assert published.verdict is GateVerdict.REDACTED
    assert FIXTURE_WORKSPACE not in published.content
    assert "ISS-104" in published.content

    assert on_the_tracker.verdict is GateVerdict.CLEAN
    assert on_the_tracker.content == TRACKER_LINK_PAYLOAD


async def test_c4_a_span_less_finding_blocks_rather_than_redacting() -> None:
    """The implication with nothing to excise: BLOCKED is the resolution."""

    class ImplicationScanner(RecordedAuditScanner):
        async def scan(
            self,
            *,
            content: str,
            destination: OutboundDestination,
        ) -> ScanResult:
            self.calls.append(content)
            return ScanResult(
                hits=(
                    ScanHit(
                        category=RedactionCategory.ORG_PRIVATE,
                        rationale="the passage as a whole implies an unreleased "
                        "capability; no substring carries it",
                    ),
                ),
            )

    config = AppConfig()
    gate = PatternOutboundContentGate(
        scanners=[ImplicationScanner({})],
        verdicts=config.deny_pattern_verdicts,
    )
    decision = await gate.gate(
        content="Once the next layer lands the whole thing runs itself.",
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert decision.verdict is GateVerdict.BLOCKED
    assert decision.content == ""
    assert decision.hits[0].rationale is not None
