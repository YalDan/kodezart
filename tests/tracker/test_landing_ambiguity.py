"""Conflicting recorded input identities cannot select an optimistic landing."""

import pytest

from kodezart.domain.errors import BaseResolutionError
from tests.fakes import FakeGitService, FakeMcpComment
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    ASSET_ISSUE,
    CLAIMED_ISSUE,
    FIXTURE_NOW,
    fixture_server,
)
from tests.tracker.marker_config import MARKER_PREFIXES
from tests.tracker.test_landing_base_resolution import resolve


@pytest.fixture
def server(owner, order):
    value = fixture_server()
    value.issues[CLAIMED_ISSUE].parent_id = (
        ASSET_ISSUE if owner == ASSET_ISSUE else None
    )
    for index, state in enumerate(order):
        value.comments.append(
            FakeMcpComment(
                id=f"record-{index}",
                issue_id=owner,
                author="observer",
                body=(
                    f'<!-- {MARKER_PREFIXES["work_ref"]} role="deliverable" '
                    f'branch="recorded-{index}" pushed-head-sha="0000000" '
                    f'landing="{state}" -->'
                ),
                created_at=FIXTURE_NOW,
            )
        )
    return value


@pytest.mark.parametrize("owner", [CLAIMED_ISSUE, ASSET_ISSUE])
@pytest.mark.parametrize("order", [("landed", "unknown"), ("unknown", "landed")])
async def test_ambiguous_own_or_inherited_records_refuse_before_git(
    tracker, tracker_writes, owner
):
    before = tracker_writes()
    git = FakeGitService()
    with pytest.raises(BaseResolutionError) as caught:
        await resolve(tracker, git)
    assert caught.value.issue_id == APPROVED_ISSUE
    assert caught.value.blocker_issue_ids == (CLAIMED_ISSUE,)
    assert caught.value.branches == ("recorded-0", "recorded-1")
    assert owner in str(caught.value)
    assert git.calls == []
    assert tracker_writes() == before
