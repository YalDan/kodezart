"""Legacy and observer-edited native records preserve explicit landing facts."""

import pytest

from kodezart.core.errors import TrackerProtocolError
from kodezart.types.domain.branch import WorkRefLanding
from tests.fakes import FakeMcpComment
from tests.tracker.conftest import APPROVED_ISSUE, FIXTURE_NOW, fixture_server
from tests.tracker.marker_config import MARKER_PREFIXES
from tests.tracker.test_linear_mcp_tracker import linear_over_fake_mcp


def body(attributes=""):
    return (
        f'<!-- {MARKER_PREFIXES["work_ref"]} role="deliverable" '
        f'branch="feature/input" pushed-head-sha="0000000"{attributes} -->'
    )


def server_with_record(text):
    server = fixture_server()
    server.comments.append(
        FakeMcpComment(
            id="observer-record",
            issue_id=APPROVED_ISSUE,
            author="human-observer",
            body=text,
            created_at=FIXTURE_NOW,
        )
    )
    return server


async def test_legacy_absence_is_unknown_and_current_human_edit_is_read_cold():
    server = server_with_record(body())
    (legacy,) = await linear_over_fake_mcp(server).work_refs(issue_key=APPROVED_ISSUE)
    assert legacy.landing is WorkRefLanding.UNKNOWN
    server.comments[0].body = body(' landing="landed"')
    (amended,) = await linear_over_fake_mcp(server).work_refs(issue_key=APPROVED_ISSUE)
    assert amended.landing is WorkRefLanding.LANDED
    assert amended.branch == legacy.branch
    assert amended.pushed_head_sha == legacy.pushed_head_sha == "0000000"
    assert amended.recorded_at == legacy.recorded_at
    assert not [call for call in server.calls if call[0] == "save_comment"]


@pytest.mark.parametrize(
    "attributes",
    [
        ' landing="true"',
        ' landing=""',
        ' landing="unknown" landing="landed"',
        " landing=landed",
        ' landing="not landed"',
    ],
)
async def test_unreadable_landing_never_disappears_into_ref_absence(attributes):
    server = server_with_record(body(attributes))
    with pytest.raises(TrackerProtocolError):
        await linear_over_fake_mcp(server).work_refs(issue_key=APPROVED_ISSUE)


async def test_repeated_native_work_ref_marker_refuses():
    server = server_with_record(body() + "\n" + body(' landing="landed"'))
    with pytest.raises(TrackerProtocolError):
        await linear_over_fake_mcp(server).work_refs(issue_key=APPROVED_ISSUE)
