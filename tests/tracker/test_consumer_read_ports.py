"""Small read-only substitutions reach the actual native-backed consumers."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from kodezart.core.errors import TrackerUnavailableError
from kodezart.core.protocols import (
    TrackerCommentReader,
    TrackerContextReader,
    TrackerCriteriaReader,
    TrackerPort,
)
from kodezart.domain.errors import (
    AssetFetchError,
    CriterionResolutionError,
    EscalationReadError,
    LaneRecordReadError,
    RulingRecordReadError,
)
from kodezart.services.criterion_sources import resolve_criterion
from kodezart.services.escalation_records import EscalationRecordReader
from kodezart.services.fire_context import FireContextAssembler
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.types.domain.gating import RepoVisibility, WriterShape
from kodezart.types.domain.tracker import TrackerAsset, TrackerComment, TrackerIssue
from tests.fakes import FakeMcpAsset, FakeMcpIssue, PassThroughGate
from tests.tracker import test_escalation_record_collector as escalation
from tests.tracker import test_lane_records as lane
from tests.tracker import test_ruling_records as ruling
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    DOCUMENT_CONTENT,
    DOCUMENT_KEY,
    DOCUMENT_TITLE,
    fixture_server,
)

CRITERION = "native/condition"


@pytest.fixture
def server():
    server = fixture_server()
    server.issues[CRITERION] = FakeMcpIssue(
        id=CRITERION,
        parent_id=APPROVED_ISSUE,
        labels=["acceptance-condition"],
        description="**Check:** Exact source.\n\n**Evidence:** unchanged bytes λ\n",
    )
    server.issues[APPROVED_ISSUE].documents = [
        FakeMcpAsset(id=DOCUMENT_KEY, title=DOCUMENT_TITLE, url="native-document")
    ]
    return server


@dataclass
class CommentsOnly:
    source: TrackerCommentReader

    async def list_comments(self, *, issue_key: str) -> Sequence[TrackerComment]:
        return await self.source.list_comments(issue_key=issue_key)


@dataclass
class CriteriaOnly:
    source: TrackerCriteriaReader

    async def read_criteria(self, *, issue_key: str) -> Sequence[TrackerIssue]:
        return await self.source.read_criteria(issue_key=issue_key)


@dataclass
class DocumentsOnly:
    source: TrackerContextReader

    async def list_issue_assets(self, *, issue_key: str) -> Sequence[TrackerAsset]:
        return await self.source.list_issue_assets(issue_key=issue_key)

    async def read_document(self, *, document_key: str) -> str:
        return await self.source.read_document(document_key=document_key)


async def test_all_record_readers_work_with_only_complete_comment_reads(
    tracker: TrackerPort, tracker_writes
):
    stored_lane = await lane.seed(tracker)
    stored_escalation = await escalation.seed_escalation(tracker)
    stored_ruling, original_ruling = await ruling.seed(tracker)
    before = tracker_writes()
    comments = CommentsOnly(tracker)
    lane_comment, lane_record = await LaneRecordReader(
        tracker=comments, operation=lane.OPERATION
    ).read(**lane.ADDRESS, record_ref=stored_lane.comment_key)
    question_comment, question = await EscalationRecordReader(
        tracker=comments, operation=escalation.OPERATION
    ).read(**escalation.ADDRESS, record_ref=stored_escalation.comment_key)
    rulings = await RulingRecordReader(
        tracker=comments, operation=ruling.OPERATION
    ).read_all(**ruling.ADDRESS)
    assert lane_comment == stored_lane and lane_record.lane_key == lane.LANE
    assert question_comment == stored_escalation and question == escalation.escalation()
    assert rulings == ((stored_ruling, original_ruling),)
    assert tracker_writes() == before


@pytest.mark.parametrize("cancelled", [False, True])
async def test_each_record_reader_preserves_failure_or_cancellation(
    tracker, monkeypatch, cancelled
):
    failure = (
        asyncio.CancelledError() if cancelled else TrackerUnavailableError("unreadable")
    )

    async def fail(*, issue_key):
        raise failure

    monkeypatch.setattr(tracker, "list_comments", fail)
    comments = CommentsOnly(tracker)
    calls = (
        (
            LaneRecordReader(tracker=comments, operation=lane.OPERATION).read,
            lane.ADDRESS,
            LaneRecordReadError,
        ),
        (
            EscalationRecordReader(
                tracker=comments, operation=escalation.OPERATION
            ).read,
            escalation.ADDRESS,
            EscalationReadError,
        ),
        (
            RulingRecordReader(tracker=comments, operation=ruling.OPERATION).read_all,
            ruling.ADDRESS,
            RulingRecordReadError,
        ),
    )
    for read, address, error in calls:
        with pytest.raises(asyncio.CancelledError if cancelled else error) as caught:
            await read(**address)
        assert (caught.value if cancelled else caught.value.__cause__) is failure


async def test_criterion_only_reader_observes_native_edits_and_preserves_source(
    tracker: TrackerPort, tracker_writes
):
    criteria = CriteriaOnly(tracker)
    first = await resolve_criterion(
        tracker=criteria, issue_key=APPROVED_ISSUE, criterion_key=CRITERION
    )
    assert first.body.endswith("unchanged bytes λ\n")
    await tracker.update_issue(issue_key=CRITERION, body="**Check:** Changed source.\n")
    before = tracker_writes()
    second = await resolve_criterion(
        tracker=criteria, issue_key=APPROVED_ISSUE, criterion_key=CRITERION
    )
    assert second.body == "**Check:** Changed source.\n" and first != second
    assert tracker_writes() == before


@pytest.mark.parametrize("kind", ["empty", "duplicate", "unreadable", "cancelled"])
async def test_criterion_only_reader_does_not_turn_refusal_into_absence(
    tracker, monkeypatch, kind
):
    original = await tracker.read_criteria(issue_key=APPROVED_ISSUE)
    failure = (
        asyncio.CancelledError() if kind == "cancelled" else RuntimeError("failed read")
    )

    async def read(*, issue_key):
        if kind in {"cancelled", "unreadable"}:
            raise failure
        return () if kind == "empty" else (*original, *original)

    monkeypatch.setattr(tracker, "read_criteria", read)
    with pytest.raises(
        asyncio.CancelledError if kind == "cancelled" else CriterionResolutionError
    ) as caught:
        await resolve_criterion(
            tracker=CriteriaOnly(tracker),
            issue_key=APPROVED_ISSUE,
            criterion_key=CRITERION,
        )
    if kind == "cancelled":
        assert caught.value is failure
    else:
        assert caught.value.issue_key == APPROVED_ISSUE
        assert caught.value.criterion_key == CRITERION
        assert caught.value.__cause__ is (failure if kind == "unreadable" else None)


def assembler(
    documents: TrackerContextReader, gate: PassThroughGate
) -> FireContextAssembler:
    return FireContextAssembler(
        tracker=documents,
        gate=gate,
        max_count=3,
        max_bytes=4096,
        fetch_timeout_seconds=0.1,
    )


async def test_documents_only_reader_fetches_and_gates_native_full_content(
    tracker: TrackerPort, tracker_writes
):
    gate = PassThroughGate()
    before = tracker_writes()
    context = await assembler(DocumentsOnly(tracker), gate).assemble(
        issue_key=APPROVED_ISSUE, body="the retained ticket"
    )
    assert context.body == "the retained ticket"
    assert [(asset.asset_key, asset.content) for asset in context.assets] == [
        (DOCUMENT_KEY, DOCUMENT_CONTENT)
    ]
    assert gate.calls == [(DOCUMENT_CONTENT, RepoVisibility.PUBLIC, WriterShape.PROSE)]
    assert tracker_writes() == before


@pytest.mark.parametrize("cancelled", [False, True])
async def test_documents_only_reader_preserves_fetch_failure_and_cancellation(
    tracker, monkeypatch, cancelled
):
    failure = asyncio.CancelledError() if cancelled else RuntimeError("failed document")

    async def fail(*, document_key):
        raise failure

    monkeypatch.setattr(tracker, "read_document", fail)
    gate = PassThroughGate()
    with pytest.raises(
        asyncio.CancelledError if cancelled else AssetFetchError
    ) as caught:
        await assembler(DocumentsOnly(tracker), gate).assemble(
            issue_key=APPROVED_ISSUE, body="retained"
        )
    assert (caught.value if cancelled else caught.value.__cause__) is failure
    assert gate.calls == []


def test_real_consumer_construction_typechecks_without_unrelated_operations():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            str(root / "tests/tracker/read_port_contract.py"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
