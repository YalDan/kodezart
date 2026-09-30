"""Independent native-boundary probes for the issue-wide ruling reader."""

import asyncio

import pytest

from kodezart.core.errors import TrackerProtocolError, TrackerUnavailableError
from kodezart.domain.errors import RulingRecordReadError
from kodezart.domain.rulings import render_ruling
from kodezart.types.domain.agent import Ruling
from tests.domain.test_rulings import PREFIXES, ruling_data
from tests.tracker.conftest import APPROVED_ISSUE, linear_over_fake_mcp
from tests.tracker.test_comment_pages import CommentPageServer, comment
from tests.tracker.test_ruling_records import reader


def native_row(key="native-one", lane="historical:lane/é", **changes):
    value = Ruling.model_validate(ruling_data(issue_ref=APPROVED_ISSUE, **changes))
    body = render_ruling(ruling=value, lane_key=lane, marker_prefixes=PREFIXES)
    return comment(key, body).wire(), value


def two_pages(first, second, **last):
    return CommentPageServer(
        {
            None: {"comments": first, "hasNextPage": True, "cursor": "next"},
            "next": {"comments": second, "hasNextPage": False, **last},
        }
    )


@pytest.mark.parametrize("lane", ["historical:lane/é", "literal%2F", "[]\n:?#"])
async def test_actual_encoded_lane_and_native_author_survive_cold_read(lane):
    row, value = native_row(lane=lane)
    row["author"] = None
    server = two_pages([], [row])
    observed = await reader(linear_over_fake_mcp(server)).read_issue(
        issue_key=APPROVED_ISSUE
    )
    assert len(observed) == 1
    native, actual = observed[0]
    assert actual == value
    assert native.body == row["body"]
    assert native.comment_key == row["id"]
    assert native.author_key is None
    assert native.issue_key == APPROVED_ISSUE
    assert server.tool_calls("list_comments") == [
        {"issueId": APPROVED_ISSUE},
        {"issueId": APPROVED_ISSUE, "cursor": "next"},
    ]
    assert not server.tool_calls("save_comment")
    assert not server.tool_calls("save_issue")


async def test_identical_page_overlap_is_one_native_record():
    row, value = native_row()
    server = two_pages([row], [row])
    observed = await reader(linear_over_fake_mcp(server)).read_issue(
        issue_key=APPROVED_ISSUE
    )
    assert tuple(ruling for _, ruling in observed) == (value,)


@pytest.mark.parametrize("same_question", [True, False])
async def test_conflicting_native_key_across_pages_is_refused(same_question):
    first, _ = native_row()
    second, _ = native_row(
        resolution="A conflicting answer.",
        **({} if same_question else {"question": "A different exact question?"}),
    )
    server = two_pages([first], [second])
    with pytest.raises(RulingRecordReadError) as caught:
        await reader(linear_over_fake_mcp(server)).read_issue(issue_key=APPROVED_ISSUE)
    assert caught.value.lane_key is None
    assert not server.tool_calls("save_comment")


async def test_missing_native_reply_metadata_cannot_assert_top_level_record():
    row, _ = native_row()
    del row["parentId"]
    server = two_pages([], [row])
    with pytest.raises(RulingRecordReadError) as caught:
        await reader(linear_over_fake_mcp(server)).read_issue(issue_key=APPROVED_ISSUE)
    assert caught.value.lane_key is None


@pytest.mark.parametrize("cursor", [None, "next"])
async def test_later_page_cannot_advance_refuses_with_unknown_lane(cursor):
    row, _ = native_row()
    server = two_pages([row], [], hasNextPage=True, cursor=cursor)
    with pytest.raises(RulingRecordReadError) as caught:
        await reader(linear_over_fake_mcp(server)).read_issue(issue_key=APPROVED_ISSUE)
    assert caught.value.issue_key == APPROVED_ISSUE
    assert caught.value.lane_key is None
    assert isinstance(caught.value.__cause__, TrackerProtocolError)


async def test_later_page_damage_refuses_without_partial_result():
    first, _ = native_row()
    second, _ = native_row("native-two", question="A second question?")
    second["body"] = second["body"].split("\n", 1)[0] + "\nmalformed"
    server = two_pages([first], [second])
    with pytest.raises(RulingRecordReadError):
        await reader(linear_over_fake_mcp(server)).read_issue(issue_key=APPROVED_ISSUE)
    assert len(server.tool_calls("list_comments")) == 2


@pytest.mark.parametrize("same_lane", [True, False])
async def test_duplicate_question_later_page_is_not_reconciled_by_answer(same_lane):
    first, _ = native_row(lane="lane-one")
    second, _ = native_row(
        "native-two",
        lane="lane-one" if same_lane else "lane-two",
        resolution="A conflicting answer.",
    )
    server = two_pages([first], [second])
    with pytest.raises(RulingRecordReadError, match="share an identity"):
        await reader(linear_over_fake_mcp(server)).read_issue(issue_key=APPROVED_ISSUE)


class CancelledPageServer(CommentPageServer):
    def _tool_list_comments(self, arguments):
        if arguments.get("cursor") == "next":
            raise asyncio.CancelledError()
        return super()._tool_list_comments(arguments)


async def test_cancellation_during_second_native_page_propagates():
    row, _ = native_row()
    server = CancelledPageServer(
        {None: {"comments": [row], "hasNextPage": True, "cursor": "next"}}
    )
    with pytest.raises(asyncio.CancelledError):
        await reader(linear_over_fake_mcp(server)).read_issue(issue_key=APPROVED_ISSUE)
    assert not server.tool_calls("save_comment")


async def test_backend_outage_retains_typed_cause_and_unknown_lane():
    server = CommentPageServer({None: {"comments": [], "hasNextPage": False}})
    server._tool_errors["list_comments"] = "unreachable"
    with pytest.raises(RulingRecordReadError) as caught:
        await reader(linear_over_fake_mcp(server)).read_issue(issue_key=APPROVED_ISSUE)
    assert caught.value.lane_key is None
    assert caught.value.issue_key == APPROVED_ISSUE
    assert isinstance(caught.value.__cause__, TrackerUnavailableError)


async def test_read_all_keeps_explicit_lane_boundary_with_other_lane_damage():
    row, value = native_row(lane="actual-lane")
    damage = comment("native-two", "[fixture-pinned:other-lane:bad]\nmalformed")
    server = two_pages([row], [damage.wire()])
    ruling_reader = reader(linear_over_fake_mcp(server))
    actual = await ruling_reader.read_all(
        issue_key=APPROVED_ISSUE, lane_key="actual-lane"
    )
    assert tuple(ruling for _, ruling in actual) == (value,)
    with pytest.raises(RulingRecordReadError):
        await ruling_reader.read_issue(issue_key=APPROVED_ISSUE)


@pytest.mark.parametrize(
    "defect", ["conflicting-answer", "conflicting-question", "missing-reply"]
)
async def test_inherited_explicit_lane_boundary_refuses_ambiguous_native_record(defect):
    first, _ = native_row()
    if defect == "missing-reply":
        del first["parentId"]
        server = two_pages([], [first])
    else:
        second, _ = native_row(
            resolution="A conflicting answer.",
            **(
                {"question": "Another question?"}
                if defect == "conflicting-question"
                else {}
            ),
        )
        server = two_pages([first], [second])
    with pytest.raises(RulingRecordReadError):
        await reader(linear_over_fake_mcp(server)).read_all(
            issue_key=APPROVED_ISSUE, lane_key="historical:lane/é"
        )
