"""The actual specification reader requires current machine and human facts."""

import asyncio

import pytest

from kodezart.composition.tracker import build_tracker
from kodezart.core.backoff import RetryPolicy
from kodezart.core.config import AppConfig
from kodezart.domain.errors import FireSpecEntryError
from kodezart.types.domain.operation import (
    OperationConfig,
    OperationMemberAbsentError,
    ScopeLabel,
)
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.domain.test_organize import mandate_operation_fields
from tests.fakes import FakeMcpIssue, FakeTrackerPort
from tests.tracker.conftest import (
    FIRE_ENTRY_LABELS,
    FIRE_SCOPE_LABEL,
    FIRE_STAGE_KEY,
    FIRE_STAGE_LABEL,
)
from tests.tracker.test_fire_spec_reader import CRITERION, SUBJECT
from tests.tracker.test_fire_spec_reader import server as server
from tests.tracker.test_linear_mcp_tracker import tracker_over

PARENT = "approval/parent"


def approval(tracker, server, key, present):
    if isinstance(tracker, FakeTrackerPort):
        tracker.scope_label_members[ScopeRef(kind=ScopeKind.ISSUE, key=key)] = (
            frozenset({ScopeLabel.APPROVED}) if present else frozenset()
        )
    else:
        row = server.issues[key]
        row.labels = [label for label in row.labels if label != FIRE_SCOPE_LABEL]
        if present:
            row.labels.append(FIRE_SCOPE_LABEL)


def marker(tracker, server, key, present):
    if isinstance(tracker, FakeTrackerPort):
        row = tracker.issues[key]
        labels = row.issue_labels - {FIRE_STAGE_KEY}
        tracker.issues[key] = row.model_copy(
            update={"issue_labels": labels | {FIRE_STAGE_KEY} if present else labels}
        )
    else:
        row = server.issues[key]
        row.labels = [label for label in row.labels if label != FIRE_STAGE_LABEL]
        if present:
            row.labels.append(FIRE_STAGE_LABEL)


@pytest.mark.parametrize("missing", ["approval", "completion", "both"])
async def test_either_missing_fact_refuses_actual_spec_read(
    tracker, server, tracker_writes, missing
):
    before = tracker_writes()
    if missing in {"approval", "both"}:
        approval(tracker, server, SUBJECT, False)
    if missing in {"completion", "both"}:
        marker(tracker, server, SUBJECT, False)
    with pytest.raises(FireSpecEntryError) as caught:
        await tracker.read_fire_spec(issue_key=SUBJECT)
    assert caught.value.issue_key == SUBJECT
    assert tuple(
        row.issue_key for row in await tracker.read_criteria(issue_key=SUBJECT)
    ) == (CRITERION,)
    assert tracker_writes() == before


@pytest.mark.parametrize("revoked", ["approval", "completion"])
async def test_same_reader_observes_revocation_and_restoration(
    tracker, server, tracker_writes, revoked
):
    before = tracker_writes()
    initial = await tracker.read_fire_spec(issue_key=SUBJECT)
    change = approval if revoked == "approval" else marker
    change(tracker, server, SUBJECT, False)
    with pytest.raises(FireSpecEntryError):
        await tracker.read_fire_spec(issue_key=SUBJECT)
    change(tracker, server, SUBJECT, True)
    assert await tracker.read_fire_spec(issue_key=SUBJECT) == initial
    assert tracker_writes() == before


async def test_only_approval_inherits_from_parent(tracker, server, tracker_writes):
    before = tracker_writes()
    if isinstance(tracker, FakeTrackerPort):
        tracker.issues[PARENT] = tracker.issues[SUBJECT].model_copy(
            update={"issue_key": PARENT}
        )
        tracker.issues[SUBJECT] = tracker.issues[SUBJECT].model_copy(
            update={"parent_key": PARENT}
        )
    else:
        server.issues[PARENT] = FakeMcpIssue(id=PARENT, labels=list(FIRE_ENTRY_LABELS))
        server.issues[SUBJECT].parent_id = PARENT
    approval(tracker, server, SUBJECT, False)
    approval(tracker, server, PARENT, True)
    assert (await tracker.read_fire_spec(issue_key=SUBJECT)).subject == SUBJECT
    approval(tracker, server, PARENT, False)
    with pytest.raises(FireSpecEntryError, match="approval"):
        await tracker.read_fire_spec(issue_key=SUBJECT)
    approval(tracker, server, PARENT, True)
    marker(tracker, server, SUBJECT, False)
    with pytest.raises(FireSpecEntryError, match="completion"):
        await tracker.read_fire_spec(issue_key=SUBJECT)
    assert tracker_writes() == before


async def test_parent_text_and_queue_label_do_not_supply_either_fact(
    tracker, server, tracker_writes
):
    await tracker.update_issue(
        issue_key=SUBJECT, body="scope:approved queue:approved:criteria"
    )
    approval(tracker, server, SUBJECT, False)
    marker(tracker, server, SUBJECT, False)
    if not isinstance(tracker, FakeTrackerPort):
        server.issues[SUBJECT].labels.extend(
            ["queue:approved", "scope:approved", "queue:approved:criteria"]
        )
    before = tracker_writes()
    with pytest.raises(FireSpecEntryError):
        await tracker.read_fire_spec(issue_key=SUBJECT)
    assert tracker_writes() == before


async def test_missing_phase_configuration_is_explicit_at_point_of_need(
    tracker, server
):
    if isinstance(tracker, FakeTrackerPort):
        tracker.criteria_stage_label_key = None
    else:
        tracker = tracker_over(server, criteria_stage_label_key=None)
    assert await tracker.read_criteria(issue_key=SUBJECT)
    with pytest.raises(OperationMemberAbsentError, match="terminal_marker_key"):
        await tracker.read_fire_spec(issue_key=SUBJECT)


async def test_actual_composition_uses_remapped_criteria_row(server):
    fields = mandate_operation_fields()
    fields["issue_labels"]["criteria"] = "ready under this operation"
    fields["scope_labels"]["approved"] = "authorized under this operation"
    server.issues[SUBJECT].labels = [
        "ready under this operation",
        "authorized under this operation",
    ]
    server.issues[CRITERION].labels = ["check"]
    config = AppConfig(_env_file=None)
    port, _ = build_tracker(
        backend=config.tracker.backend,
        retry=RetryPolicy(
            attempts=config.tracker.max_retries + 1,
            initial_delay=config.tracker.retry_backoff_factor,
        ),
        operation=OperationConfig.model_validate(fields),
        caller=server,
    )
    assert (await port.read_fire_spec(issue_key=SUBJECT)).criteria == (CRITERION,)
    # A different completed phase cannot stand in for the selected table row.
    server.issues[SUBJECT].labels = ["body complete", "authorized under this operation"]
    with pytest.raises(FireSpecEntryError, match="completion"):
        await port.read_fire_spec(issue_key=SUBJECT)
    assert not server.tool_calls("save_issue")


async def test_native_missing_marker_mapping_refuses_without_tool_calls(server):
    server.calls.clear()
    port = tracker_over(server, criteria_stage_label_key="unmapped")
    with pytest.raises(OperationMemberAbsentError, match=r"issue_labels\.unmapped"):
        await port.read_fire_spec(issue_key=SUBJECT)
    assert server.calls == []


async def test_cancellation_during_approval_propagates_without_spec_or_write(server):
    class CanceledServer(type(server)):
        async def call_tool(self, **kwargs):
            raise asyncio.CancelledError

    port = tracker_over(CanceledServer())
    with pytest.raises(asyncio.CancelledError):
        await port.read_fire_spec(issue_key=SUBJECT)


@pytest.mark.parametrize("omitted", ["labels", "parentId"])
async def test_native_incomplete_subject_facts_refuse_at_actual_spec_read(
    server, omitted
):
    class IncompleteServer(type(server)):
        def _tool_get_issue(self, arguments):
            value = dict(super()._tool_get_issue(arguments))
            if arguments["id"] == SUBJECT:
                value.pop(omitted)
            return value

    from kodezart.domain.errors import CriterionReadError

    native = IncompleteServer(issues=list(server.issues.values()))
    with pytest.raises(CriterionReadError) as caught:
        await tracker_over(native).read_fire_spec(issue_key=SUBJECT)
    assert caught.value.__cause__ is not None
    assert not native.tool_calls("save_issue")


async def test_absent_configured_mandates_refuse_only_at_fire_read(server):
    fields = mandate_operation_fields()
    fields["organize_mandates"] = []
    server.issues[SUBJECT].labels = ["approved scope"]
    server.issues[CRITERION].labels = ["check"]
    config = AppConfig(_env_file=None)
    port, _ = build_tracker(
        backend=config.tracker.backend,
        retry=RetryPolicy(
            attempts=config.tracker.max_retries + 1,
            initial_delay=config.tracker.retry_backoff_factor,
        ),
        operation=OperationConfig.model_validate(fields),
        caller=server,
    )
    assert await port.read_criteria(issue_key=SUBJECT)
    with pytest.raises(OperationMemberAbsentError, match="terminal_marker_key"):
        await port.read_fire_spec(issue_key=SUBJECT)
    assert not server.tool_calls("save_issue")
