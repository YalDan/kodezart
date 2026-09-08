"""Nondefault prefixes reach the real adapter through production composition."""

import pytest

from kodezart.composition.tracker import build_tracker
from kodezart.core.config import AppConfig
from kodezart.core.protocols import TrackerPort
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.domain.errors import UnsupportedClaimError
from kodezart.types.domain.branch import WorkRef, WorkRefRole, trunk_base
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from tests.fakes import FakeMcpComment
from tests.tracker.conftest import (
    APPROVED_ISSUE,
    CLAIMED_ISSUE,
    FIXTURE_NOW,
    FIXTURE_REPO_URL,
    fixture_server,
)


async def test_composed_prefixes_round_trip_through_every_adapter(tracker: TrackerPort):
    operation = OperationConfig(
        operation_name="fixture",
        workspace="fixture",
        marker_prefixes={"decision": "changed-prefix"},
    )
    marker = compose_comment_marker(
        prefixes=operation.marker_prefixes,
        purpose="decision",
        lane="one",
        occurrence_key="two",
    )
    written = await tracker.upsert_comment(
        target=APPROVED_ISSUE, marker=marker, body="decision"
    )
    assert written.body == "[changed-prefix:one:two]\ndecision"
    assert (await tracker.list_comments(issue_key=APPROVED_ISSUE)) == (written,)


async def test_all_existing_marker_carriers_use_the_injected_operation_mapping():
    operation = OperationConfig(
        operation_name="fixture",
        workspace="fixture",
        marker_prefixes={
            "claim": "different.claim",
            "work_ref": "different.ref",
            "base_spec": "different.base",
            "repository": "different.repository",
        },
    )
    server = fixture_server()
    tracker, _ = build_tracker(config=AppConfig(), operation=operation, caller=server)
    server.comments.append(
        FakeMcpComment(
            id="legacy-claim",
            issue_id=CLAIMED_ISSUE,
            author="fixture-service",
            body=(
                '<!-- different.claim holder="one-job" '
                'expires-at="2099-01-01T00:00:00+00:00" -->'
            ),
            created_at=FIXTURE_NOW,
        )
    )
    claim = await tracker.active_claim(issue_key=CLAIMED_ISSUE)
    assert claim is not None and claim.holder == "one-job"
    with pytest.raises(UnsupportedClaimError):
        await tracker.renew_claim(
            issue_key=CLAIMED_ISSUE, holder="one-job", lease_seconds=600
        )
    assert len(server.comments) == 1
    await tracker.release_claim(issue_key=CLAIMED_ISSUE, holder="one-job")
    assert server.comments == []

    ref = WorkRef(
        issue_id=CLAIMED_ISSUE,
        role=WorkRefRole.DELIVERABLE,
        branch="fixture-branch",
        recorded_at=FIXTURE_NOW,
    )
    await tracker.record_work_ref(ref=ref)
    assert server.comments[-1].body.startswith("<!-- different.ref ")
    assert (await tracker.work_refs(issue_key=CLAIMED_ISSUE))[
        0
    ].identity() == ref.identity()

    spec = trunk_base("main")
    await tracker.record_base_spec(issue_key=CLAIMED_ISSUE, spec=spec)
    assert server.comments[-1].body.startswith("<!-- different.base ")
    assert await tracker.read_base_spec(issue_key=CLAIMED_ISSUE) == spec

    await tracker.post_comment(
        issue_key=CLAIMED_ISSUE,
        body=f'<!-- different.repository url="{FIXTURE_REPO_URL}" -->',
    )
    assert (
        await tracker.recorded_repository(issue_key=CLAIMED_ISSUE) == FIXTURE_REPO_URL
    )
    server.comments.append(
        FakeMcpComment(
            id="wrong-prefix",
            issue_id=CLAIMED_ISSUE,
            author="fixture",
            body='<!-- differentXrepository url="https://wrong.invalid" -->',
            created_at=FIXTURE_NOW,
        )
    )
    assert (
        await tracker.recorded_repository(issue_key=CLAIMED_ISSUE) == FIXTURE_REPO_URL
    )


async def test_unconfigured_marker_write_fails_before_any_backend_mutation():
    operation = OperationConfig(operation_name="fixture", workspace="fixture")
    server = fixture_server()
    tracker, _ = build_tracker(config=AppConfig(), operation=operation, caller=server)
    with pytest.raises(UnsupportedClaimError):
        await tracker.claim_issue(
            issue_key=CLAIMED_ISSUE, holder="one-job", lease_seconds=600
        )
    assert server.tool_calls("save_comment") == []


async def test_empty_log_does_not_disguise_an_unconfigured_marker_reader():
    operation = OperationConfig(operation_name="fixture", workspace="fixture")
    server = fixture_server()
    tracker, _ = build_tracker(config=AppConfig(), operation=operation, caller=server)
    for read in (
        tracker.active_claim,
        tracker.work_refs,
        tracker.read_base_spec,
        tracker.recorded_repository,
    ):
        with pytest.raises(OperationMemberAbsentError, match="marker_prefixes"):
            await read(issue_key=CLAIMED_ISSUE)
    assert server.calls == []
