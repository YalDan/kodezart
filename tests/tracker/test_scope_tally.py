"""Actual scope membership and configured phase labels feed the shared signal."""

import asyncio
import json
from dataclasses import dataclass

import pytest

from kodezart.composition.tracker import build_tracker
from kodezart.core.backoff import RetryPolicy
from kodezart.core.config import AppConfig
from kodezart.core.errors import TrackerProtocolError
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import RunShapeReadError
from kodezart.domain.run_shape import tally_unmoved
from kodezart.services.scope_tally import observe_scope_tally
from kodezart.types.domain.operation import OperationConfig, OperationMemberAbsentError
from kodezart.types.domain.organize import MandateKind
from tests.domain.test_organize import mandate_operation_fields
from tests.fakes import FakeTrackerPort
from tests.tracker.test_scope_reads import (
    INITIATIVE,
    MILESTONE,
    PROJECT,
    ScopeMcpIssue,
    ScopeMcpServer,
    _container,
)


@dataclass
class Fixture:
    tracker: TrackerPort
    native: TrackerPort
    fake: FakeTrackerPort
    server: ScopeMcpServer
    operation: OperationConfig

    def labels(self, key, *labels):
        self.server.issues[key].labels = [
            self.operation.issue_labels[label] for label in labels
        ]
        self.fake.issues[key] = self.fake.issues[key].model_copy(
            update={"issue_labels": frozenset(labels)}
        )

    def read_only(self):
        assert not self.fake.issue_writes
        assert not self.fake.queue_writes
        assert not self.fake.comment_writes
        assert not self.fake.claim_writes
        assert {tool for tool, _ in self.server.calls} <= {
            "get_issue",
            "list_issues",
            "get_project",
            "get_initiative",
            "get_milestone",
            "list_projects",
            "list_milestones",
        }

    async def observe(self, *, phase=MandateKind.TICKET, scope=PROJECT):
        return await observe_scope_tally(
            tracker=self.tracker,
            operation=self.operation,
            scope=scope,
            phase=phase,
            raised_at_sha="supervisor-tick",
            raised_by="run/holder",
        )


@pytest.fixture(params=["native", "fake"])
async def tally(request):
    fields = mandate_operation_fields()
    fields["issue_labels"].update(
        {"tracker": "recorded-history", "decision": "open-question"}
    )
    # Neither native label spelling nor declaration order defines the phase sequence.
    fields["organize_mandates"].reverse()
    operation = OperationConfig.model_validate(fields)
    server = ScopeMcpServer()
    server.issues = {
        "one": ScopeMcpIssue(id="one"),
        "two": ScopeMcpIssue(id="two"),
        "check": ScopeMcpIssue(id="check", parent_id="one", labels=["check"]),
        "record": ScopeMcpIssue(id="record", labels=["recorded-history"]),
        "decision": ScopeMcpIssue(id="decision", labels=["open-question"]),
    }
    config = AppConfig(_env_file=None)
    native, _ = build_tracker(
        backend=config.tracker.backend,
        retry=RetryPolicy(
            attempts=config.tracker.max_retries + 1,
            initial_delay=config.tracker.retry_backoff_factor,
        ),
        operation=operation,
        caller=server,
    )
    fake = FakeTrackerPort(
        issues=[await native.read_issue(issue_key=key) for key in server.issues],
        scope_containers=[
            _container(PROJECT, INITIATIVE),
            _container(INITIATIVE),
            _container(MILESTONE, PROJECT),
        ],
        scope_memberships={
            PROJECT: tuple(server.issues),
            MILESTONE: tuple(server.issues),
            INITIATIVE: tuple(server.issues),
        },
    )
    return Fixture(
        native if request.param == "native" else fake, native, fake, server, operation
    )


@pytest.mark.parametrize(
    "phase,current,next_key",
    [
        (MandateKind.GROOM, "groomed", "body"),
        (MandateKind.TICKET, "body", "criteria"),
    ],
)
async def test_actual_marker_barrier_zero_partial_and_complete_rosters(
    tally, phase, current, next_key
):
    tally.labels("one", next_key)
    alarm = await tally.observe(phase=phase)
    assert alarm is not None
    assert alarm.subject.scope_key == PROJECT.key
    assert json.loads(alarm.readings[3].value) == ["one", "two"]
    assert alarm.raised_at_sha == "supervisor-tick"
    assert alarm.raised_by == "run/holder"
    assert (
        tally_unmoved(
            subject=alarm.subject,
            readings=alarm.readings,
            raised_at_sha=alarm.raised_at_sha,
            raised_by=alarm.raised_by,
        )
        == alarm
    )
    tally.labels("one", current, next_key)
    assert await tally.observe(phase=phase) is not None
    tally.labels("two", current)
    assert await tally.observe(phase=phase) is None
    tally.labels("two")
    assert await tally.observe(phase=phase) is not None
    tally.read_only()


async def test_no_next_marker_is_quiet_even_when_every_current_marker_is_missing(tally):
    assert await tally.observe() is None
    tally.read_only()


async def test_excluded_record_or_criterion_next_marker_cannot_invent_entry(tally):
    for key, classification in [
        ("check", "criterion"),
        ("record", "tracker"),
        ("decision", "decision"),
    ]:
        tally.labels(key, classification, "criteria")
    assert await tally.observe() is None
    tally.read_only()


async def test_record_classification_does_not_prune_deliverable_descendants(tally):
    tally.server.issues["one"].parent_id = "record"
    tally.fake.issues["one"] = tally.fake.issues["one"].model_copy(
        update={"parent_key": "record"}
    )
    tally.labels("one", "criteria")
    alarm = await tally.observe()
    assert alarm is not None
    assert json.loads(alarm.readings[3].value) == ["one", "two"]
    tally.read_only()


@pytest.mark.parametrize("scope", [PROJECT, MILESTONE, INITIATIVE])
async def test_native_scope_addresses_and_milestone_query_are_preserved(tally, scope):
    tally.labels("one", "criteria")
    alarm = await tally.observe(scope=scope)
    assert alarm is not None
    assert alarm.subject.scope_key == scope.key
    assert json.loads(alarm.readings[2].value)["kind"] == scope.kind.value
    tally.read_only()


async def test_execution_rung_refuses_before_any_query(tally):
    tally.server.calls.clear()
    tally.fake.issue_reads.clear()
    with pytest.raises(RunShapeReadError, match="lane-dispatched event reader"):
        await tally.observe(phase=MandateKind.CRITERIA)
    assert tally.server.calls == []
    assert tally.fake.issue_reads == []
    tally.read_only()


async def test_empty_scope_has_no_entry(tally):
    tally.server.issues.clear()
    tally.fake.issues.clear()
    tally.fake.scope_memberships[PROJECT] = ()
    assert await tally.observe() is None
    tally.read_only()


@pytest.mark.parametrize(
    "damage",
    ["classification", "marker", "new-member", "duplicate", "foreign-identity"],
)
async def test_changing_or_incomplete_roster_refuses(tally, monkeypatch, damage):
    tally.labels("one", "criteria")
    original = tally.tracker.read_planning_issue
    changed = False

    async def read(*, issue_key):
        nonlocal changed
        value = await original(issue_key=issue_key)
        if not changed:
            changed = True
            if damage == "classification":
                tally.labels("two", "tracker")
            elif damage == "marker":
                tally.labels("one", "body", "criteria")
            elif damage == "new-member":
                tally.server.issues["added"] = ScopeMcpIssue(id="added")
                tally.fake.issues["added"] = tally.fake.issues["two"].model_copy(
                    update={"issue_key": "added"}
                )
                tally.fake.scope_memberships[PROJECT] += ("added",)
            elif damage == "foreign-identity":
                return value.model_copy(update={"issue_key": "foreign"})
            elif damage == "duplicate":
                pass
        return value

    monkeypatch.setattr(tally.tracker, "read_planning_issue", read)
    if damage == "duplicate":
        original_scope = tally.tracker.scope_issues

        async def scope_issues(*, ref):
            rows = list(await original_scope(ref=ref))
            return [*rows, rows[0]]

        monkeypatch.setattr(tally.tracker, "scope_issues", scope_issues)
    with pytest.raises(RunShapeReadError):
        await tally.observe()
    tally.read_only()


async def test_cancellation_is_propagated_without_an_alarm(tally, monkeypatch):
    async def canceled(*, issue_key):
        raise asyncio.CancelledError

    monkeypatch.setattr(tally.tracker, "read_planning_issue", canceled)
    with pytest.raises(asyncio.CancelledError):
        await tally.observe()
    tally.read_only()


@pytest.mark.parametrize(
    "missing", ["criterion", "tracker", "decision", "body", "criteria"]
)
async def test_unmapped_native_classification_or_phase_refuses_before_read(
    tally, missing
):
    operation = tally.operation.model_copy(
        update={
            "issue_labels": {
                key: value
                for key, value in tally.operation.issue_labels.items()
                if key != missing
            },
            "organize_mandates": (),
        }
    )
    config = AppConfig(_env_file=None)
    native, _ = build_tracker(
        backend=config.tracker.backend,
        retry=RetryPolicy(
            attempts=config.tracker.max_retries + 1,
            initial_delay=config.tracker.retry_backoff_factor,
        ),
        operation=operation,
        caller=tally.server,
    )
    tally.tracker = native
    tally.server.calls.clear()
    with pytest.raises(OperationMemberAbsentError):
        await tally.observe()
    assert tally.server.calls == []


async def test_native_omitted_classification_is_not_a_missing_phase_marker(
    tally, monkeypatch
):
    original = tally.server._tool_get_issue

    def omitted(arguments):
        result = dict(original(arguments))
        result.pop("labels", None)
        return result

    monkeypatch.setattr(tally.server, "_tool_get_issue", omitted)
    tally.tracker = tally.native
    with pytest.raises(TrackerProtocolError):
        await tally.observe()
    tally.read_only()


async def test_unreadable_roster_member_cannot_become_a_missing_marker(
    tally, monkeypatch
):
    from kodezart.core.errors import McpTransportError

    original = tally.tracker.scope_issues

    async def disappearing(*, ref):
        rows = await original(ref=ref)
        tally.server.issues.pop("one", None)
        tally.fake.issues.pop("one", None)
        return rows

    monkeypatch.setattr(tally.tracker, "scope_issues", disappearing)
    with pytest.raises((RunShapeReadError, McpTransportError)):
        await tally.observe()
    tally.read_only()


async def test_issue_subtree_roster_retains_its_native_address(tally):
    from kodezart.types.domain.scope import ScopeKind, ScopeRef

    scope = ScopeRef(kind=ScopeKind.ISSUE, key="one")
    tally.labels("one", "criteria")
    alarm = await tally.observe(scope=scope)
    assert alarm is not None
    assert json.loads(alarm.readings[3].value) == ["one"]
    assert alarm.subject.scope_key == "one"
    tally.labels("one", "body", "criteria")
    assert await tally.observe(scope=scope) is None
    tally.read_only()


@pytest.mark.parametrize("alias", ["criteria", "criterion", "tracker", "decision"])
async def test_marker_aliases_cannot_hide_a_phase_or_change_roster(tally, alias):
    fields = tally.operation.model_dump()
    for mandate in fields["organize_mandates"]:
        if mandate["kind"] is MandateKind.TICKET:
            mandate["terminal_marker_key"] = f"issue_labels.{alias}"
    tally.operation = OperationConfig.model_validate(fields)
    tally.server.calls.clear()
    with pytest.raises(RunShapeReadError, match=r"phase markers|aliases"):
        await tally.observe()
    assert tally.server.calls == []


async def test_missing_mandate_table_refuses_before_membership_read(tally):
    tally.operation = tally.operation.model_copy(update={"organize_mandates": ()})
    tally.server.calls.clear()
    with pytest.raises(OperationMemberAbsentError, match="organize_mandates"):
        await tally.observe()
    assert tally.server.calls == []
