"""Issue-wide ruling reads discover native lane markers without guessing lanes."""

from datetime import timedelta

import pytest

from kodezart.domain.errors import RulingRecordReadError
from kodezart.domain.rulings import render_ruling
from kodezart.types.domain.agent import Ruling
from tests.domain.test_rulings import PREFIXES, ruling_data
from tests.tracker.conftest import APPROVED_ISSUE, linear_over_fake_mcp
from tests.tracker.test_comment_pages import CommentPageServer, comment
from tests.tracker.test_ruling_records import reader


async def seed_lane(tracker, lane, *, question="Which interpretation applies?"):
    value = Ruling.model_validate(
        ruling_data(issue_ref=APPROVED_ISSUE, question=question)
    )
    body = render_ruling(ruling=value, lane_key=lane, marker_prefixes=PREFIXES)
    stored = await tracker.post_comment(issue_key=APPROVED_ISSUE, body=body)
    return stored, value


async def test_issue_wide_read_retains_actual_different_lane_occurrences(
    tracker,
    tracker_writes,
):
    first = await seed_lane(tracker, "old:lane/café")
    second = await seed_lane(tracker, "current%lane", question="Another actual choice?")
    before = tracker_writes()
    assert await reader(tracker).read_issue(issue_key=APPROVED_ISSUE) == (first, second)
    assert await reader(tracker).read_all(
        issue_key=APPROVED_ISSUE, lane_key="current%lane"
    ) == (second,)
    assert tracker_writes() == before


async def test_same_question_under_two_lanes_is_ambiguous_not_two_rulings(tracker):
    await seed_lane(tracker, "first")
    await seed_lane(tracker, "second")
    with pytest.raises(RulingRecordReadError, match="share an identity") as caught:
        await reader(tracker).read_issue(issue_key=APPROVED_ISSUE)
    assert caught.value.lane_key is None


@pytest.mark.parametrize(
    "marker",
    [
        "[fixture-pinned:]",
        "[fixture-pinned:lane]",
        "[fixture-pinned:%ZZ:key]",
        "[fixture-pinned:lane%2fone:key]",
    ],
)
async def test_malformed_owned_lane_component_is_not_silent_absence(tracker, marker):
    await tracker.post_comment(issue_key=APPROVED_ISSUE, body=marker + "\nnot a ruling")
    with pytest.raises(RulingRecordReadError) as caught:
        await reader(tracker).read_issue(issue_key=APPROVED_ISSUE)
    assert caught.value.lane_key is None


async def test_ordinary_comment_and_other_namespace_remain_unselected(tracker):
    await tracker.post_comment(
        issue_key=APPROVED_ISSUE,
        body="ordinary source\n[fixture-pinned:lane:key]",
    )
    await tracker.post_comment(
        issue_key=APPROVED_ISSUE, body="[another-prefix:lane:key]\nother record"
    )
    assert await reader(tracker).read_issue(issue_key=APPROVED_ISSUE) == ()


async def test_native_pages_retain_historical_lane_and_current_native_provenance():
    first = Ruling.model_validate(ruling_data(issue_ref=APPROVED_ISSUE))
    second = Ruling.model_validate(
        ruling_data(issue_ref=APPROVED_ISSUE, question="A later choice?")
    )
    bodies = [
        render_ruling(ruling=value, lane_key=lane, marker_prefixes=PREFIXES)
        for value, lane in ((first, "historical:lane/café"), (second, "current%lane"))
    ]
    previous = comment("old-native-record", bodies[0])
    current = comment("new-native-record", bodies[1])
    current.created_at = previous.created_at + timedelta(seconds=1)
    server = CommentPageServer(
        {
            None: {
                "comments": [previous.wire()],
                "hasNextPage": True,
                "cursor": "next",
            },
            "next": {
                "comments": [current.wire()],
                "hasNextPage": False,
            },
        }
    )
    rows = await reader(linear_over_fake_mcp(server)).read_issue(
        issue_key=APPROVED_ISSUE
    )
    assert tuple(value for _, value in rows) == (first, second)
    assert tuple(row.comment_key for row, _ in rows) == (
        "old-native-record",
        "new-native-record",
    )
    assert tuple(row.body for row, _ in rows) == tuple(bodies)


@pytest.mark.parametrize("defect", ["reply", "noncanonical-lane", "foreign-owner"])
async def test_full_valid_payload_cannot_override_native_provenance(defect):
    value = Ruling.model_validate(
        ruling_data(
            issue_ref="another-issue" if defect == "foreign-owner" else APPROVED_ISSUE
        )
    )
    body = render_ruling(ruling=value, lane_key="lane/actual", marker_prefixes=PREFIXES)
    if defect == "noncanonical-lane":
        body = body.replace("lane%2Factual", "lane%2factual", 1)
    row = comment("actual-native-record", body)
    if defect == "reply":
        row.parent_id = "actual-native-parent-comment"
    server = CommentPageServer({None: {"comments": [row.wire()], "hasNextPage": False}})
    with pytest.raises(RulingRecordReadError):
        await reader(linear_over_fake_mcp(server)).read_issue(issue_key=APPROVED_ISSUE)
