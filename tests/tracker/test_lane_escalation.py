"""A later stage can fail only after the escalation is already durable."""

import json

import pytest
from pydantic import ValidationError

from kodezart.core.errors import TrackerUnavailableError
from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.services.lane_escalation import LaneEscalationWriter
from kodezart.types.domain.gating import (
    ContentClass,
    GateDecision,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
)
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.run_state import LaneEscalation
from tests.fakes import (
    FakeLinearMcpServer,
    FakeMcpIssue,
    FakeTrackerPort,
    PassThroughGate,
)
from tests.services.test_tracker_lifecycle import BlockingGate
from tests.tracker.test_linear_mcp_tracker import tracker_over

ISSUE = "work/42"
LABELS = {"decision": "Owner decision needed", "criterion": "Acceptance condition"}
OPERATION = OperationConfig(
    operation_name="fixture",
    workspace="fixture",
    issue_labels=LABELS,
    marker_prefixes={"escalation": "open-question"},
)


def question(**overrides):
    return LaneEscalation(
        **{
            "issue_id": ISSUE,
            "escalation_key": "criterion:alpha",
            "raised_by": "organize",
            "question": "Which declared owner decides this behavior?",
            "interim_reading": "Hold the affected change while other work continues.",
            "interim_basis": "The scope contains two contradictory declarations.",
            "raised_at_sha": "a" * 40,
            **overrides,
        }
    )


@pytest.fixture(params=["fake", "linear"])
async def port(request):
    server = FakeLinearMcpServer(
        issues=[FakeMcpIssue(id=ISSUE, labels=["unrelated", "queue:approved"])],
    )
    linear = tracker_over(server, issue_labels=LABELS)
    if request.param == "linear":
        return linear, lambda: tuple(server.calls), server
    fake = FakeTrackerPort(issues=[await linear.read_issue(issue_key=ISSUE)])
    return (
        fake,
        lambda: (tuple(fake.comment_writes), tuple(fake.classification_writes)),
        None,
    )


async def test_raise_is_durable_before_later_report_failure_and_replay_is_noop(port):
    tracker, writes, server = port
    gate = PassThroughGate()
    writer = LaneEscalationWriter(tracker=tracker, gate=gate, operation=OPERATION)
    before = await tracker.read_issue(issue_key=ISSUE)

    async def node_then_report():
        await writer.raise_escalation(
            lane_key="lane:1", escalation=question(), visibility=RepoVisibility.PUBLIC
        )
        assert "decision" in (await tracker.read_issue(issue_key=ISSUE)).issue_labels
        assert len(await tracker.list_comments(issue_key=ISSUE)) == 1
        raise RuntimeError("later stage report failed")

    with pytest.raises(RuntimeError, match="later stage"):
        await node_then_report()
    stored = (await tracker.list_comments(issue_key=ISSUE))[0]
    assert stored.body.splitlines()[0] == "[open-question:lane%3A1:criterion%3Aalpha]"
    assert json.loads(stored.body.split("\n", 1)[1]) == question().model_dump(
        by_alias=True
    )
    current = await tracker.read_issue(issue_key=ISSUE)
    assert current.state_name == before.state_name
    assert current.queue_states == before.queue_states
    assert current.body == before.body
    if server:
        assert server.issues[ISSUE].labels == [
            "unrelated",
            "queue:approved",
            LABELS["decision"],
        ]
        mutation_count = len(server.tool_calls("save_issue")) + len(
            server.tool_calls("save_comment")
        )
    else:
        mutation_count = writes()
    replay = await writer.raise_escalation(
        lane_key="lane:1", escalation=question(), visibility=RepoVisibility.PUBLIC
    )
    assert replay == stored
    assert await tracker.read_issue(issue_key=ISSUE) == current
    if server:
        assert (
            len(server.tool_calls("save_issue"))
            + len(server.tool_calls("save_comment"))
            == mutation_count
        )
    else:
        assert writes() == mutation_count
    assert gate.content_classes == [ContentClass.AUTHORED, ContentClass.AUTHORED]
    assert gate.destinations == [OutboundDestination.TRACKER_COMMENT] * 2
    assert gate.calls[0][0] == stored.body


async def test_failed_decision_write_propagates_and_retry_completes_same_comment():
    server = FakeLinearMcpServer(issues=[FakeMcpIssue(id=ISSUE)])
    tracker = tracker_over(server, issue_labels=LABELS)
    writer = LaneEscalationWriter(
        tracker=tracker, gate=PassThroughGate(), operation=OPERATION
    )
    server._tool_errors["save_issue"] = "temporarily refused"
    with pytest.raises(TrackerUnavailableError):
        await writer.raise_escalation(
            lane_key="lane", escalation=question(), visibility=RepoVisibility.PUBLIC
        )
    assert len(server.comments) == 1
    original = server.comments[0].id
    del server._tool_errors["save_issue"]
    result = await writer.raise_escalation(
        lane_key="lane", escalation=question(), visibility=RepoVisibility.PUBLIC
    )
    assert result.comment_key == original
    assert len(server.comments) == 1
    assert LABELS["decision"] in server.issues[ISSUE].labels


async def test_unrelated_label_added_between_read_and_write_survives():
    class ConcurrentLabelServer(FakeLinearMcpServer):
        def _tool_save_issue(self, arguments):
            self.issues[ISSUE].labels.append("concurrently-added")
            return super()._tool_save_issue(arguments)

    server = ConcurrentLabelServer(issues=[FakeMcpIssue(id=ISSUE, labels=["existing"])])
    tracker = tracker_over(server, issue_labels=LABELS)
    await tracker.set_issue_classification(issue_key=ISSUE, classification="decision")
    assert server.issues[ISSUE].labels == [
        "existing",
        "concurrently-added",
        LABELS["decision"],
    ]
    assert server.tool_calls("save_issue") == [
        {"id": ISSUE, "addLabels": [LABELS["decision"]]}
    ]


@pytest.mark.parametrize("missing", ["issue_labels", "marker_prefixes"])
async def test_missing_configuration_refuses_before_any_write(port, missing):
    tracker, _, _ = port
    config = OPERATION.model_copy(update={missing: {}})
    writer = LaneEscalationWriter(
        tracker=tracker, gate=PassThroughGate(), operation=config
    )
    with pytest.raises(OperationMemberAbsentError):
        await writer.raise_escalation(
            lane_key="lane", escalation=question(), visibility=RepoVisibility.PUBLIC
        )
    assert await tracker.list_comments(issue_key=ISSUE) == ()
    assert "decision" not in (await tracker.read_issue(issue_key=ISSUE)).issue_labels


async def test_gate_refusal_precedes_comment_and_classification(port):
    tracker, _, _ = port
    writer = LaneEscalationWriter(
        tracker=tracker, gate=BlockingGate(), operation=OPERATION
    )
    with pytest.raises(OutboundContentBlockedError):
        await writer.raise_escalation(
            lane_key="lane", escalation=question(), visibility=RepoVisibility.PUBLIC
        )
    assert await tracker.list_comments(issue_key=ISSUE) == ()
    assert "decision" not in (await tracker.read_issue(issue_key=ISSUE)).issue_labels


@pytest.mark.parametrize(
    "changes",
    [
        {"interim_reading": " "},
        {"interim_reading": ""},
        {"raised_at_sha": ""},
        {"unexpected": True},
    ],
)
def test_incomplete_escalation_is_not_a_value(changes):
    with pytest.raises(ValidationError):
        question(**changes)


def test_escalation_is_frozen_and_every_ruled_field_is_required():
    value = question()
    with pytest.raises(ValidationError):
        value.question = "different"
    for name in LaneEscalation.model_fields:
        fields = value.model_dump()
        del fields[name]
        with pytest.raises(ValidationError):
            LaneEscalation(**fields)


async def test_gate_cannot_rewrite_occurrence_identity(port):
    class RewritingGate:
        async def gate(self, **kwargs):
            _, body = kwargs["content"].split("\n", 1)
            return GateDecision(verdict=GateVerdict.CLEAN, content="[changed]\n" + body)

    tracker, _, _ = port
    writer = LaneEscalationWriter(
        tracker=tracker, gate=RewritingGate(), operation=OPERATION
    )
    with pytest.raises(OutboundContentBlockedError, match="identity"):
        await writer.raise_escalation(
            lane_key="lane", escalation=question(), visibility=RepoVisibility.PUBLIC
        )
    assert await tracker.list_comments(issue_key=ISSUE) == ()
    assert "decision" not in (await tracker.read_issue(issue_key=ISSUE)).issue_labels


def test_escalation_preserves_the_supplied_commit_identity():
    assert (
        question(raised_at_sha="opaque-sha256-or-other-ref").raised_at_sha
        == "opaque-sha256-or-other-ref"
    )


@pytest.mark.parametrize(
    "field", ["raisedAtSha", "issueId", "raisedBy", "escalationKey"]
)
async def test_gate_cannot_redact_event_identity_or_remove_required_fields(port, field):
    class FieldGate:
        async def gate(self, **kwargs):
            marker, body = kwargs["content"].split("\n", 1)
            data = json.loads(body)
            data[field] = "redacted"
            return GateDecision(
                verdict=GateVerdict.REDACTED, content=marker + "\n" + json.dumps(data)
            )

    tracker, _, _ = port
    writer = LaneEscalationWriter(
        tracker=tracker, gate=FieldGate(), operation=OPERATION
    )
    with pytest.raises(OutboundContentBlockedError, match="provenance"):
        await writer.raise_escalation(
            lane_key="lane", escalation=question(), visibility=RepoVisibility.PUBLIC
        )
    assert await tracker.list_comments(issue_key=ISSUE) == ()
    assert "decision" not in (await tracker.read_issue(issue_key=ISSUE)).issue_labels


async def test_gate_field_name_redaction_is_refused_before_write(port):
    class FieldNameGate:
        async def gate(self, **kwargs):
            return GateDecision(
                verdict=GateVerdict.REDACTED,
                content=kwargs["content"].replace("raisedAtSha", "redacted"),
            )

    tracker, _, _ = port
    writer = LaneEscalationWriter(
        tracker=tracker, gate=FieldNameGate(), operation=OPERATION
    )
    with pytest.raises(OutboundContentBlockedError, match="required escalation fields"):
        await writer.raise_escalation(
            lane_key="lane", escalation=question(), visibility=RepoVisibility.PUBLIC
        )
    assert await tracker.list_comments(issue_key=ISSUE) == ()
    assert "decision" not in (await tracker.read_issue(issue_key=ISSUE)).issue_labels


async def test_valid_question_redaction_is_persisted_as_gated(port):
    class ProseGate:
        async def gate(self, **kwargs):
            return GateDecision(
                verdict=GateVerdict.REDACTED,
                content=kwargs["content"].replace(
                    "Which declared owner", "Which [redacted]"
                ),
            )

    tracker, _, _ = port
    writer = LaneEscalationWriter(
        tracker=tracker, gate=ProseGate(), operation=OPERATION
    )
    result = await writer.raise_escalation(
        lane_key="lane", escalation=question(), visibility=RepoVisibility.PUBLIC
    )
    parsed = LaneEscalation.model_validate_json(result.body.split("\n", 1)[1])
    assert parsed.question == "Which [redacted] decides this behavior?"
    assert "decision" in (await tracker.read_issue(issue_key=ISSUE)).issue_labels
