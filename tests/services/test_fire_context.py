"""Assets a fire's ticket references reach the session, or the fire fails loudly.

Everything here runs over the in-process fake tracker serving fixture
attachments and documents.  No live workspace, no live remote, no vendor
call.

The sanitization cases reference KOD-47's gate rather than re-implementing
any part of it: tracker-sourced content is untrusted input, and KOD-107 R1
rules that the gate — unchanged, constructed from shipped configuration —
is what stands between a tracker document and a fire context.  Every
assembler built here holds the shipped gate, so the admission rule is
exercised by every case in the module and not only by the ones about it.
"""

import ast
import asyncio
from pathlib import Path

import pytest

from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.regex_content_scanner import RegexContentScanner
from kodezart.core.config import AppConfig
from kodezart.core.protocols import OutboundContentGate
from kodezart.domain.errors import AssetFetchError
from kodezart.services.fire_context import FireContextAssembler
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    ScanFailureKind,
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


def shipped_gate() -> PatternOutboundContentGate:
    """The gate as a deployment gets it: shipped patterns, shipped verdicts."""
    config = AppConfig()
    return PatternOutboundContentGate(
        scanners=[RegexContentScanner(patterns=config.deny_patterns)],
        verdicts=config.deny_pattern_verdicts,
    )


def assembler(
    tracker: FakeTrackerPort,
    *,
    gate: OutboundContentGate | None = None,
    max_count: int = DEFAULTS.tracker_asset_max_count,
    max_bytes: int = DEFAULTS.tracker_asset_max_bytes,
    fetch_timeout_seconds: float = DEFAULTS.tracker_asset_fetch_timeout_seconds,
) -> FireContextAssembler:
    return FireContextAssembler(
        tracker=tracker,
        gate=shipped_gate() if gate is None else gate,
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
# Admission — KOD-107 R1: tracker documents are untrusted on the way IN
# ---------------------------------------------------------------------------


PRIVATE_FIXTURE_CONTENT = (
    "deploy with https://x-access-token:ghp_" + "a" * 36 + "@example.invalid/o/r.git"
)

#: The claim the two documents KOD-107 reports actually carry.  It buys
#: nothing here, and the test below is what makes that checkable.
SANITIZED_CLAIM_TITLE = "routine prompt (sanitized, updated 2026-07-24)"


type GatePosture = tuple[
    RepoVisibility,
    WriterShape,
    OutboundDestination,
    ContentClass,
]


class ScriptedGate:
    """A gate returning one scripted decision, recording what it was asked."""

    def __init__(self, decision: GateDecision) -> None:
        self.decision: GateDecision = decision
        self.seen: list[str] = []
        self.postures: list[GatePosture] = []

    async def gate(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        shape: WriterShape,
        destination: OutboundDestination,
        content_class: ContentClass,
    ) -> GateDecision:
        self.seen.append(content)
        self.postures.append((visibility, shape, destination, content_class))
        return self.decision.model_copy(update={"content": content})


def tracker_with(content: str, *, title: str = SPEC_TITLE) -> FakeTrackerPort:
    return FakeTrackerPort(
        issues=[make_tracker_issue(ISSUE, body=BODY)],
        assets={ISSUE: [asset(SPEC_KEY, title)]},
        documents={SPEC_KEY: content},
    )


async def test_the_shipped_gate_is_what_refuses_the_private_fixture() -> None:
    """The refusals below are the shipped gate's verdict, not a local rule."""
    decision = await shipped_gate().gate(
        content=PRIVATE_FIXTURE_CONTENT,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )
    assert decision.verdict is GateVerdict.BLOCKED


async def test_a_document_carrying_a_private_identifier_never_enters_the_context() -> (
    None
):
    """AC-66: the document is refused on the way in, naming it.

    Supersedes the weaker pair this module used to carry, which assembled
    the context first and then showed that writing it out would be blocked.
    That demonstrated the outbound gate, which held at trunk already; what
    the exposure path needed is that the content does not get in.
    """
    with pytest.raises(AssetFetchError) as excinfo:
        await assembler(tracker_with(PRIVATE_FIXTURE_CONTENT)).assemble(
            issue_key=ISSUE,
            body=BODY,
        )

    assert excinfo.value.reason == "private_content"
    assert excinfo.value.asset_key == SPEC_KEY
    assert excinfo.value.issue_key == ISSUE


async def test_a_document_claiming_to_be_sanitized_is_refused_on_its_body() -> None:
    """AC-67: the safety claim is not evidence — it is the reported defect.

    Both documents KOD-107 filed on are titled *sanitized* and are not.  A
    machine-readable marker would be the same claim in stricter syntax, so
    nothing here reads a title, a label or a marker.
    """
    with pytest.raises(AssetFetchError) as excinfo:
        await assembler(
            tracker_with(PRIVATE_FIXTURE_CONTENT, title=SANITIZED_CLAIM_TITLE),
        ).assemble(issue_key=ISSUE, body=BODY)

    assert excinfo.value.reason == "private_content"
    assert excinfo.value.asset_key == SPEC_KEY


async def test_a_gate_that_cannot_answer_refuses_rather_than_admitting() -> None:
    """AC-67, fail-closed: "no answer" is not "clean", asserted on its own."""
    scripted = ScriptedGate(
        GateDecision(
            verdict=GateVerdict.BLOCKED,
            content="",
            failure=ScanFailureKind.TIMEOUT,
        ),
    )

    with pytest.raises(AssetFetchError) as excinfo:
        await assembler(tracker_with(SPEC_CONTENT), gate=scripted).assemble(
            issue_key=ISSUE,
            body=BODY,
        )

    assert excinfo.value.reason == "private_content"
    assert excinfo.value.asset_key == SPEC_KEY


async def test_a_redacted_verdict_refuses_rather_than_admitting_the_redaction() -> None:
    """Only CLEAN enters: a doctored input is worse than a fire that did not start."""
    scripted = ScriptedGate(
        GateDecision(verdict=GateVerdict.REDACTED, content="[redacted]"),
    )

    with pytest.raises(AssetFetchError) as excinfo:
        await assembler(tracker_with(PRIVATE_FIXTURE_CONTENT), gate=scripted).assemble(
            issue_key=ISSUE,
            body=BODY,
        )

    assert excinfo.value.reason == "private_content"


async def test_every_fetched_document_is_gated_at_the_ruled_posture() -> None:
    """No document reaches a context ungated, and the posture is the ruled one."""
    scripted = ScriptedGate(GateDecision(verdict=GateVerdict.CLEAN, content=""))
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue(ISSUE, body=BODY)],
        assets={ISSUE: [asset(SPEC_KEY, SPEC_TITLE), asset(NOTES_KEY, NOTES_TITLE)]},
        documents={SPEC_KEY: SPEC_CONTENT, NOTES_KEY: NOTES_CONTENT},
    )

    context = await assembler(tracker, gate=scripted).assemble(
        issue_key=ISSUE,
        body=BODY,
    )

    assert scripted.seen == [SPEC_CONTENT, NOTES_CONTENT]
    assert set(scripted.postures) == {
        (
            RepoVisibility.PUBLIC,
            WriterShape.PROSE,
            OutboundDestination.PR_BODY,
            ContentClass.AUTHORED,
        ),
    }
    assert len(context.assets) == 2


async def test_one_refused_document_fails_the_whole_context() -> None:
    """Embedding an asset in the fire's text cannot launder it: nothing renders."""
    tracker = FakeTrackerPort(
        issues=[make_tracker_issue(ISSUE, body=BODY)],
        assets={ISSUE: [asset(SPEC_KEY, SPEC_TITLE), asset(NOTES_KEY, NOTES_TITLE)]},
        documents={SPEC_KEY: SPEC_CONTENT, NOTES_KEY: PRIVATE_FIXTURE_CONTENT},
    )

    with pytest.raises(AssetFetchError) as excinfo:
        await assembler(tracker).assemble(issue_key=ISSUE, body=BODY)

    assert excinfo.value.asset_key == NOTES_KEY


# ---------------------------------------------------------------------------
# No asset reaches a file — KOD-59 R2 as amended on the issue
# ---------------------------------------------------------------------------


ASSEMBLER_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "kodezart"
    / "services"
    / "fire_context.py"
)

#: Every module name that could put bytes on disk.  ``pathlib`` and ``os``
#: are the obvious two; ``tempfile`` and ``shutil`` are the two a writer
#: reaches for when the obvious two look too blunt.
_FILESYSTEM_MODULES: tuple[str, ...] = (
    "os",
    "pathlib",
    "shutil",
    "tempfile",
    "aiofiles",
)

#: Names that WRITE, as opposed to merely locating.  ``open`` is a builtin,
#: so an import check alone would miss it.
_WRITE_CALLS: tuple[str, ...] = ("open", "write_text", "write_bytes", "mkdir")


def _module_imports(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".")[0])
    return imported


def _called_names(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_the_write_probe_recognises_a_module_that_does_write() -> None:
    """Guards the two assertions below: a probe that never fires proves nothing."""
    probe = Path(__file__)

    assert _module_imports(probe) & set(_FILESYSTEM_MODULES)
    assert _called_names(ASSEMBLER_SOURCE)


def test_an_asset_cannot_be_committed_because_it_is_never_written() -> None:
    """KOD-59 R2's test requirement, under the layout amended on that issue.

    R2 rules an on-disk layout plus a git-ignore, and asks for a test that
    an asset cannot be committed by accident.  The layout is not shipped —
    the amendment on KOD-59 records why: there is no workspace at dispatch
    time, because the fire is enqueued and not run.  The obligation is
    discharged more strongly instead: an asset cannot be committed because
    no code path writes one, asserted over the module rather than over a
    `.gitignore` line that a later writer could satisfy while still
    writing files somewhere else.
    """
    assert _module_imports(ASSEMBLER_SOURCE) & set(_FILESYSTEM_MODULES) == set()
    assert _called_names(ASSEMBLER_SOURCE) & set(_WRITE_CALLS) == set()


def test_the_assembler_holds_no_collaborator_that_could_write_a_file() -> None:
    """The import check is about the module; this one is about the object."""
    subject = assembler(FakeTrackerPort())

    assert [
        value
        for value in vars(subject).values()
        if any(hasattr(value, name) for name in ("persist", "acquire", "write"))
    ] == []
