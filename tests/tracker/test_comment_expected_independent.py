"""Independent native expected-comment counterexamples; production adapter intact."""

import pytest

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.core.backoff import RetryPolicy
from kodezart.core.errors import McpTransportError
from kodezart.domain.errors import StaleCommentWriteError, SurfaceLeaseError
from kodezart.types.domain.dispatch import SelfWriteLedger
from kodezart.types.domain.tracker import TrackerComment
from tests.tracker import conftest as fixture
from tests.tracker.conftest import APPROVED_ISSUE, fixture_server, linear_over_fake_mcp
from tests.tracker.lease_fixtures import lease_for_comment, leased_comment

MARKER = "[independent:expected:record]"


@pytest.mark.parametrize(
    "field,value",
    [
        ("issue_key", "foreign-issue"),
        ("reply_to", "parent"),
        ("body", "[another:marker]\noriginal"),
    ],
)
async def test_wrong_expected_address_never_writes(field, value):
    server = fixture_server()
    tracker = linear_over_fake_mcp(server)
    original = await leased_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER, body="original"
    )
    expected = TrackerComment.model_validate({**original.model_dump(), field: value})
    async with lease_for_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER
    ) as holder:
        writes = len(server.tool_calls("save_comment"))
        with pytest.raises(StaleCommentWriteError):
            await tracker.upsert_comment(
                target=APPROVED_ISSUE,
                marker=MARKER,
                body="replacement",
                holder=holder,
                expected=expected,
            )
        assert len(server.tool_calls("save_comment")) == writes


@pytest.mark.parametrize("change", ["body", "lease", "unchanged"])
async def test_known_unissued_retry_revalidates_changed_expected_comment(
    monkeypatch, change
):
    server = fixture_server()
    tracker = LinearMcpTracker(
        marker_prefixes=fixture.MARKER_PREFIXES,
        issue_labels={
            "criterion": "acceptance-condition",
            fixture.FIRE_STAGE_KEY: fixture.FIRE_STAGE_LABEL,
        },
        criteria_stage_label_key=fixture.FIRE_STAGE_KEY,
        scope_labels={"approved": fixture.FIRE_SCOPE_LABEL},
        caller=server,
        queue_state_labels=fixture.QUEUE_STATE_LABELS,
        workflow_state_names=fixture.WORKFLOW_STATE_NAMES,
        team_identifiers=fixture.TEAM_IDENTIFIERS,
        retry=RetryPolicy(attempts=2, initial_delay=0),
        clock=fixture.FixtureClock(),
        ledger=SelfWriteLedger(),
    )
    original = await leased_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER, body="original"
    )
    async with lease_for_comment(
        tracker, target=APPROVED_ISSUE, marker=MARKER
    ) as holder:
        call = server.call_tool
        attempted = False

        async def boundary(*, name, arguments):
            nonlocal attempted
            if (
                name == "save_comment"
                and arguments.get("id") == original.comment_key
                and not attempted
            ):
                attempted = True
                row = next(c for c in server.comments if c.id == original.comment_key)
                if change == "body":
                    row.body = f"{MARKER}\nconcurrent answer"
                elif change == "lease":
                    server.comments[:] = [row]
                raise McpTransportError(
                    "session absent before sending", server_name="fixture"
                )
            return await call(name=name, arguments=arguments)

        monkeypatch.setattr(server, "call_tool", boundary)
        writes = len(server.tool_calls("save_comment"))

        async def amend():
            return await tracker.upsert_comment(
                target=APPROVED_ISSUE,
                marker=MARKER,
                body="replacement",
                holder=holder,
                expected=original,
            )

        if change == "unchanged":
            updated = await amend()
            assert updated.comment_key == original.comment_key
            assert updated.body == f"{MARKER}\nreplacement"
            assert len(server.tool_calls("save_comment")) == writes + 1
        else:
            error = StaleCommentWriteError if change == "body" else SurfaceLeaseError
            with pytest.raises(error):
                await amend()
            assert len(server.tool_calls("save_comment")) == writes
        assert attempted
