"""Actual Linear wire and page failures cannot produce an unanswered state."""

import pytest

from kodezart.core.errors import (
    McpCredentialRefusedError,
    TrackerAccessDeniedError,
    TrackerProtocolError,
)
from kodezart.domain.errors import EscalationReadError
from kodezart.types.domain.escalation import EscalationResolutionState
from kodezart.types.domain.operation import OperationMemberAbsentError
from tests.fakes import FakeLinearMcpServer
from tests.tracker.conftest import APPROVED_ISSUE, linear_over_fake_mcp
from tests.tracker.test_comment_pages import CommentPageServer, comment
from tests.tracker.test_escalation_resolution import ADDRESS, DECISION, ESCALATION
from tests.tracker.test_linear_mcp_tracker import tracker_over


def records():
    escalation = comment("question", ESCALATION + "\nHistorical prose and SHA 0000000.")
    decision = comment("answer", DECISION + "\nThe decision's prose is not parsed.")
    decision.parent_id = escalation.id
    return escalation.wire(), decision.wire()


async def test_full_pagination_finds_the_decision_and_preserves_its_native_link():
    escalation, decision = records()
    server = CommentPageServer(
        {
            None: {"comments": [escalation], "hasNextPage": True, "cursor": "later"},
            "later": {"comments": [decision], "hasNextPage": False},
        }
    )
    tracker = linear_over_fake_mcp(server)
    resolution = await tracker.read_escalation_resolution(**ADDRESS)
    assert resolution.state is EscalationResolutionState.RESOLVED
    assert resolution.decision_ref == "answer"
    assert server.tool_calls("list_comments") == [
        {"issueId": APPROVED_ISSUE},
        {"issueId": APPROVED_ISSUE, "cursor": "later"},
    ]
    comments = await tracker.list_comments(issue_key=APPROVED_ISSUE)
    assert next(c for c in comments if c.comment_key == "answer").reply_to == "question"
    assert server.tool_calls("save_comment") == []


@pytest.mark.parametrize(
    "malformation",
    [
        "parent-absent",
        "parent-wrong-type",
        "parent-empty",
        "body-absent",
        "id-empty",
        "envelope-incomplete",
    ],
)
async def test_unreadable_record_refuses_instead_of_defaulting(malformation):
    escalation, decision = records()
    page = {"comments": [escalation, decision], "hasNextPage": False}
    if malformation == "parent-absent":
        decision.pop("parentId")
    elif malformation == "parent-wrong-type":
        decision["parentId"] = 42
    elif malformation == "parent-empty":
        decision["parentId"] = ""
    elif malformation == "body-absent":
        escalation.pop("body")
    elif malformation == "id-empty":
        escalation["id"] = ""
    else:
        page.pop("hasNextPage")
    server = CommentPageServer({None: page})
    with pytest.raises(EscalationReadError) as raised:
        await linear_over_fake_mcp(server).read_escalation_resolution(**ADDRESS)
    assert raised.value.__cause__ is not None


async def test_legacy_generic_read_does_not_require_unreported_linkage():
    escalation, _ = records()
    escalation.pop("parentId")
    server = CommentPageServer({None: {"comments": [escalation], "hasNextPage": False}})
    tracker = linear_over_fake_mcp(server)
    assert len(await tracker.list_comments(issue_key=APPROVED_ISSUE)) == 1
    with pytest.raises(EscalationReadError):
        await tracker.read_escalation_resolution(**ADDRESS)


@pytest.mark.parametrize("cursor", [None, "later"])
async def test_unfinished_or_stuck_pages_refuse_even_after_a_decision_is_seen(cursor):
    escalation, decision = records()
    server = CommentPageServer(
        {
            None: {
                "comments": [escalation, decision],
                "hasNextPage": True,
                "cursor": "later",
            },
            "later": {"comments": [], "hasNextPage": True, "cursor": cursor},
        }
    )
    with pytest.raises(EscalationReadError) as raised:
        await linear_over_fake_mcp(server).read_escalation_resolution(**ADDRESS)
    assert isinstance(raised.value.__cause__, TrackerProtocolError)


@pytest.mark.parametrize("changed", [False, True])
async def test_page_overlap_is_deduplicated_but_conflicting_record_versions_refuse(
    changed,
):
    escalation, decision = records()
    repeated = dict(decision)
    if changed:
        repeated["parentId"] = "another-question"
    server = CommentPageServer(
        {
            None: {
                "comments": [escalation, decision],
                "hasNextPage": True,
                "cursor": "later",
            },
            "later": {"comments": [repeated], "hasNextPage": False},
        }
    )
    tracker = linear_over_fake_mcp(server)
    if changed:
        with pytest.raises(EscalationReadError):
            await tracker.read_escalation_resolution(**ADDRESS)
    else:
        assert (
            await tracker.read_escalation_resolution(**ADDRESS)
        ).decision_ref == "answer"


async def test_duplicate_decision_on_later_page_cannot_hide_behind_first_match():
    escalation, decision = records()
    second = {**decision, "id": "another-answer"}
    server = CommentPageServer(
        {
            None: {
                "comments": [escalation, decision],
                "hasNextPage": True,
                "cursor": "later",
            },
            "later": {"comments": [second], "hasNextPage": False},
        }
    )
    with pytest.raises(EscalationReadError, match="found 2"):
        await linear_over_fake_mcp(server).read_escalation_resolution(**ADDRESS)


@pytest.mark.parametrize("failure", ["transport", "transient", "credential"])
async def test_failed_transport_remains_typed_with_its_original_cause(failure):
    options = {
        "transport": {"transport_failures": {"list_comments": 1}},
        "transient": {"transient_failures": {"list_comments": 1}},
        "credential": {"credential_refused_after": {"list_comments": 0}},
    }
    server = FakeLinearMcpServer(**options[failure])
    with pytest.raises(EscalationReadError) as raised:
        await linear_over_fake_mcp(server).read_escalation_resolution(**ADDRESS)
    assert raised.value.__cause__ is not None
    if failure == "credential":
        assert isinstance(raised.value.__cause__, TrackerAccessDeniedError)
        assert isinstance(raised.value.__cause__.__cause__, McpCredentialRefusedError)


async def test_missing_prefix_is_a_configuration_refusal():
    escalation, _ = records()
    server = CommentPageServer({None: {"comments": [escalation], "hasNextPage": False}})
    with pytest.raises(OperationMemberAbsentError, match="marker_prefixes"):
        await tracker_over(server, marker_prefixes={}).read_escalation_resolution(
            **ADDRESS
        )
