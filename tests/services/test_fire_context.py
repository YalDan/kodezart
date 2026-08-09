"""Assets a fire's ticket references reach the session, or the fire fails loudly.

Everything here runs over the in-process fake tracker serving fixture
attachments and documents.  No live workspace, no live remote, no vendor
call.

The sanitization case references KOD-47's gate rather than re-implementing
any part of it: tracker-sourced content is private input, and what makes
that safe is that the gate — unchanged, constructed from shipped
configuration — is what stands between it and a repository.
"""

import asyncio

import pytest

from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.core.config import AppConfig
from kodezart.domain.errors import AssetFetchError
from kodezart.services.fire_context import FireContextAssembler
from kodezart.types.domain.gating import (
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.tracker import TrackerAsset
from tests.fakes import FakeTrackerPort, make_tracker_issue

ISSUE = "K-1"
BODY = "the ticket body"

SPEC_KEY = "asset-spec"
SPEC_TITLE = "spec.md"
SPEC_CONTENT = "the specification the fire needs to build"

NOTES_KEY = "asset-notes"
NOTES_TITLE = "notes.md"
NOTES_CONTENT = "supporting notes"

DEFAULTS = AppConfig()


def asset(key: str, title: str) -> TrackerAsset:
    return TrackerAsset(
        asset_key=key,
        title=title,
        url=f"https://tracker.invalid/{key}",
    )


def tracker_serving(documents: dict[str, str]) -> FakeTrackerPort:
    return FakeTrackerPort(
        issues=[make_tracker_issue(ISSUE, body=BODY)],
        assets={
            ISSUE: [asset(key, f"{key}.md") for key in documents],
        },
        documents=documents,
    )


def assembler(
    tracker: FakeTrackerPort,
    *,
    max_count: int = DEFAULTS.tracker_asset_max_count,
    max_bytes: int = DEFAULTS.tracker_asset_max_bytes,
    fetch_timeout_seconds: float = DEFAULTS.tracker_asset_fetch_timeout_seconds,
) -> FireContextAssembler:
    return FireContextAssembler(
        tracker=tracker,
        max_count=max_count,
        max_bytes=max_bytes,
        fetch_timeout_seconds=fetch_timeout_seconds,
    )


# ---------------------------------------------------------------------------
# The asset reaches the session
# ---------------------------------------------------------------------------


async def test_a_referenced_document_lands_in_the_fire_context() -> None:
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue(ISSUE, body=BODY)],
        assets={ISSUE: [asset(SPEC_KEY, SPEC_TITLE)]},
        documents={SPEC_KEY: SPEC_CONTENT},
    )
    context = await assembler(tracker).assemble(issue_key=ISSUE, body=BODY)
    assert [(a.asset_key, a.title, a.content) for a in context.assets] == [
        (SPEC_KEY, SPEC_TITLE, SPEC_CONTENT),
    ]


async def test_the_session_can_read_each_fetched_asset() -> None:
    """The rendered context reproduces the content the session must open."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue(ISSUE, body=BODY)],
        assets={ISSUE: [asset(SPEC_KEY, SPEC_TITLE), asset(NOTES_KEY, NOTES_TITLE)]},
        documents={SPEC_KEY: SPEC_CONTENT, NOTES_KEY: NOTES_CONTENT},
    )
    rendered = (await assembler(tracker).assemble(issue_key=ISSUE, body=BODY)).render()
    assert BODY in rendered
    for key, title, content in (
        (SPEC_KEY, SPEC_TITLE, SPEC_CONTENT),
        (NOTES_KEY, NOTES_TITLE, NOTES_CONTENT),
    ):
        assert f"- {key}: {title}" in rendered
        assert content in rendered


async def test_a_ticket_with_no_assets_says_so_rather_than_saying_nothing() -> None:
    """ "No assets" and "assets not fetched" must not look alike."""
    tracker = FakeTrackerPort(issues=[make_tracker_issue(ISSUE, body=BODY)])
    rendered = (await assembler(tracker).assemble(issue_key=ISSUE, body=BODY)).render()
    assert "references no assets" in rendered


# ---------------------------------------------------------------------------
# Fail loud
# ---------------------------------------------------------------------------


async def test_an_unfetchable_required_asset_raises_and_is_never_skipped() -> None:
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue(ISSUE, body=BODY)],
        assets={ISSUE: [asset(SPEC_KEY, SPEC_TITLE)]},
        documents={},
    )
    with pytest.raises(AssetFetchError) as excinfo:
        await assembler(tracker).assemble(issue_key=ISSUE, body=BODY)
    assert excinfo.value.reason == "unreadable"
    assert excinfo.value.asset_key == SPEC_KEY
    assert excinfo.value.issue_key == ISSUE


async def test_one_unfetchable_asset_fails_the_whole_context() -> None:
    """No partial context: a fire built on half its inputs is worse than none."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue(ISSUE, body=BODY)],
        assets={ISSUE: [asset(SPEC_KEY, SPEC_TITLE), asset(NOTES_KEY, NOTES_TITLE)]},
        documents={SPEC_KEY: SPEC_CONTENT},
    )
    with pytest.raises(AssetFetchError):
        await assembler(tracker).assemble(issue_key=ISSUE, body=BODY)


async def test_a_fetch_that_outlives_the_timeout_raises_the_typed_error() -> None:
    class SlowTracker(FakeTrackerPort):
        async def read_document(self, *, document_key: str) -> str:
            await asyncio.sleep(1.0)
            return SPEC_CONTENT

    tracker = SlowTracker(
        issues=[make_tracker_issue(ISSUE, body=BODY)],
        assets={ISSUE: [asset(SPEC_KEY, SPEC_TITLE)]},
    )
    with pytest.raises(AssetFetchError) as excinfo:
        await assembler(tracker, fetch_timeout_seconds=0.01).assemble(
            issue_key=ISSUE,
            body=BODY,
        )
    assert excinfo.value.reason == "timeout"


# ---------------------------------------------------------------------------
# Bounds, read from AppConfig
# ---------------------------------------------------------------------------


async def test_the_three_bounds_are_kodezart_prefixed_config_fields() -> None:
    """No magic number: each bound is a named, constrained field."""
    for name in (
        "tracker_asset_max_count",
        "tracker_asset_max_bytes",
        "tracker_asset_fetch_timeout_seconds",
    ):
        assert name in AppConfig.model_fields


async def test_more_assets_than_the_configured_maximum_raises() -> None:
    tracker = tracker_serving({"a": "1", "b": "2", "c": "3"})
    with pytest.raises(AssetFetchError) as excinfo:
        await assembler(tracker, max_count=2).assemble(issue_key=ISSUE, body=BODY)
    assert excinfo.value.reason == "too_many"


async def test_exactly_the_configured_maximum_is_admitted() -> None:
    """The bound is inclusive; the paired positive says which side."""
    tracker = tracker_serving({"a": "1", "b": "2"})
    context = await assembler(tracker, max_count=2).assemble(
        issue_key=ISSUE,
        body=BODY,
    )
    assert len(context.assets) == 2


async def test_an_asset_over_the_size_bound_raises_rather_than_truncating() -> None:
    tracker = tracker_serving({"a": "x" * 100})
    with pytest.raises(AssetFetchError) as excinfo:
        await assembler(tracker, max_bytes=50).assemble(issue_key=ISSUE, body=BODY)
    assert excinfo.value.reason == "too_large"
    assert excinfo.value.asset_key == "a"


async def test_the_size_bound_is_measured_in_bytes_not_characters() -> None:
    """A multi-byte character costs what it costs on the wire."""
    tracker = tracker_serving({"a": "é" * 30})
    with pytest.raises(AssetFetchError):
        await assembler(tracker, max_bytes=59).assemble(issue_key=ISSUE, body=BODY)
    context = await assembler(tracker, max_bytes=60).assemble(
        issue_key=ISSUE,
        body=BODY,
    )
    assert context.assets[0].size_bytes() == 60


# ---------------------------------------------------------------------------
# Sanitization — the gate KOD-47 owns, referenced and not duplicated
# ---------------------------------------------------------------------------


PRIVATE_FIXTURE_CONTENT = (
    "deploy with https://x-access-token:ghp_" + "a" * 36 + "@example.invalid/o/r.git"
)


def shipped_gate() -> PatternOutboundContentGate:
    """The gate as a deployment gets it: shipped patterns, shipped verdicts."""
    config = AppConfig()
    return PatternOutboundContentGate(
        scanners=[RegexContentScanner(patterns=config.deny_patterns)],
        verdicts=config.deny_pattern_verdicts,
    )


async def test_private_fixture_content_toward_a_repository_path_is_blocked() -> None:
    """Tracker-sourced content is private input; the gate is what makes it safe."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue(ISSUE, body=BODY)],
        assets={ISSUE: [asset(SPEC_KEY, SPEC_TITLE)]},
        documents={SPEC_KEY: PRIVATE_FIXTURE_CONTENT},
    )
    context = await assembler(tracker).assemble(issue_key=ISSUE, body=BODY)
    decision = await shipped_gate().gate(
        content=context.assets[0].content,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert decision.verdict is GateVerdict.BLOCKED


async def test_the_rendered_context_is_gated_as_a_whole_not_per_asset() -> None:
    """Embedding an asset in the fire's text does not launder it."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue(ISSUE, body=BODY)],
        assets={ISSUE: [asset(SPEC_KEY, SPEC_TITLE)]},
        documents={SPEC_KEY: PRIVATE_FIXTURE_CONTENT},
    )
    rendered = (await assembler(tracker).assemble(issue_key=ISSUE, body=BODY)).render()
    decision = await shipped_gate().gate(
        content=rendered,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
    )
    assert decision.verdict is GateVerdict.BLOCKED
