"""The existing writer is the source; malformed addresses never supply facts."""

import ast
import inspect
from unittest.mock import AsyncMock

import pytest

from kodezart.domain.errors import EscalationReadError
from kodezart.services import escalation_records, escalation_signals
from kodezart.services.escalation_records import EscalationRecordReader
from kodezart.services.lane_escalation import LaneEscalationWriter
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import OperationMemberAbsentError
from tests.fakes import PassThroughGate
from tests.tracker.test_escalation_record_collector import (
    ADDRESS,
    MARKER,
    OPERATION,
    escalation,
    seed_escalation,
)
from tests.tracker.test_lane_escalation import ISSUE, question
from tests.tracker.test_lane_escalation import OPERATION as WRITER_OPERATION
from tests.tracker.test_lane_escalation import port as port


async def test_actual_gated_writer_round_trips_through_cold_record_reader(port):
    tracker, writes, server = port
    gate = PassThroughGate()
    original = question()
    stored = await LaneEscalationWriter(
        tracker=tracker, gate=gate, operation=WRITER_OPERATION
    ).raise_escalation(
        lane_key="lane:1", escalation=original, visibility=RepoVisibility.PUBLIC
    )
    before = writes()
    comment, record = await EscalationRecordReader(
        tracker=tracker, operation=WRITER_OPERATION
    ).read(
        issue_key=ISSUE,
        lane_key="lane:1",
        escalation_key=original.escalation_key,
        record_ref=stored.comment_key,
    )
    assert record == original
    assert comment == stored
    assert len(gate.content_classes) == 1
    # Native reads appear in the native call log; only mutation entries matter.
    if server is None:
        assert writes() == before
    else:
        assert [call for call in writes() if call[0].startswith("save_")] == [
            call for call in before if call[0].startswith("save_")
        ]


@pytest.mark.parametrize("change", ["other-owner", "reply"])
async def test_comment_ownership_and_top_level_are_required(
    tracker, monkeypatch, change
):
    comment = await seed_escalation(tracker)
    fields = (
        {"issue_key": "another-owner"}
        if change == "other-owner"
        else {"reply_to": "other-comment"}
    )
    monkeypatch.setattr(
        tracker,
        "list_comments",
        AsyncMock(return_value=(comment.model_copy(update=fields),)),
    )
    with pytest.raises(EscalationReadError):
        await EscalationRecordReader(tracker=tracker, operation=OPERATION).read(
            **ADDRESS
        )


@pytest.mark.parametrize(
    "change", ["later-line", "suffix", "different-prefix", "different-lane"]
)
async def test_neighbouring_or_quoted_marker_is_not_the_occurrence(tracker, change):
    marker = MARKER
    if change == "later-line":
        marker = "Quoted example\n" + marker
    elif change == "suffix":
        marker += " old occurrence"
    elif change == "different-prefix":
        marker = marker.replace("fixture-escalation", "escalation")
    else:
        marker = marker.replace("lane%3Aalpha", "another-lane")
    await tracker.post_comment(
        issue_key=ADDRESS["issue_key"],
        body=marker + "\n" + escalation().model_dump_json(by_alias=True),
    )
    with pytest.raises(EscalationReadError, match="no comment"):
        await EscalationRecordReader(tracker=tracker, operation=OPERATION).read(
            **ADDRESS
        )


@pytest.mark.parametrize(
    "field", ["issue_key", "lane_key", "escalation_key", "record_ref"]
)
async def test_empty_addresses_refuse_before_any_read(tracker, monkeypatch, field):
    read = AsyncMock(side_effect=AssertionError("invalid address reached tracker"))
    monkeypatch.setattr(tracker, "list_comments", read)
    with pytest.raises(EscalationReadError):
        await EscalationRecordReader(tracker=tracker, operation=OPERATION).read(
            **{**ADDRESS, field: ""}
        )
    read.assert_not_awaited()


async def test_missing_prefix_refuses_before_any_read(tracker, monkeypatch):
    read = AsyncMock(side_effect=AssertionError("missing prefix reached tracker"))
    monkeypatch.setattr(tracker, "list_comments", read)
    with pytest.raises(OperationMemberAbsentError):
        await EscalationRecordReader(
            tracker=tracker,
            operation=OPERATION.model_copy(update={"marker_prefixes": {}}),
        ).read(**ADDRESS)
    read.assert_not_awaited()


def test_actual_collector_declares_only_tracker_reads_and_pure_observation_calls():
    allowed = {
        "self._tracker.list_comments",
        "refusal",
        "compose_comment_marker",
        "comment_under_marker",
        "EscalationReadError",
        "any",
        "dict",
        "isinstance",
        "json.loads",
        "LaneEscalation.model_fields.values",
        "LaneEscalation.model_validate_json",
        "set",
        "ValueError",
        "comment.body.partition",
        "EscalationRecordReader",
        "LaneRecordReader",
        "escalation_reader.read",
        "lane_reader.read",
        "tuple",
        "len",
        "RunShapeReadError",
        "observe_escalation_ageing",
        "AlarmReading",
        "escalation_comment.body.partition",
        "json.dumps",
    }
    for module in (escalation_records, escalation_signals):
        tree = ast.parse(inspect.getsource(module))
        calls = {
            ast.unparse(node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        assert calls <= allowed, calls - allowed
        imports = {
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        assert not any(
            name and name.startswith("kodezart.adapters") for name in imports
        )
