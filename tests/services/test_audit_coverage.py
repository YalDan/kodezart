"""Coverage survives quiet ticks, restarts, tied stamps and interrupted visits."""

import ast
import asyncio
import inspect
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig
from kodezart.services import audit_coverage
from kodezart.services.audit_coverage import AuditCoverage
from kodezart.types.domain.audit import AuditCandidate
from kodezart.types.domain.scope import ScopeKind, ScopeRef

NOW = datetime(2026, 9, 8, tzinfo=UTC)
SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="project")
CONFIG = AppConfig(
    audit_sweep_interval_seconds=60, audit_full_sweep_interval_seconds=120
)


def row(key, seconds=0):
    return AuditCandidate(
        issue_key=key, state_changed_at=NOW + timedelta(seconds=seconds)
    )


async def visit(candidate):
    pass


async def cover(service, rows, seconds=0, scope=SCOPE, visitor=visit):
    return await service.cover(
        scope=scope,
        candidates=rows,
        observed_at=NOW + timedelta(seconds=seconds),
        visit=visitor,
    )


async def test_first_delta_and_periodic_full_cover_exact_records():
    service = AuditCoverage(config=CONFIG)
    first = await cover(service, [row("z"), row("a")])
    assert first.full and [r.issue_key for r in first.covered] == ["a", "z"]
    quiet = await cover(service, [row("z"), row("a")], 60)
    assert not quiet.full and quiet.covered == ()
    changed = await cover(service, [row("z", 60), row("a")], 60)
    assert not changed.full and changed.covered == (row("z", 60),)
    full = await cover(service, [row("z", 60), row("a")], 120)
    assert full.full and full.covered == (row("a"), row("z", 60))


async def test_new_record_with_tied_or_older_stamp_is_not_lost():
    service = AuditCoverage(config=CONFIG)
    await cover(service, [row("z")])
    result = await cover(service, [row("z"), row("a"), row("old", -30)], 60)
    assert result.covered == (row("old", -30), row("a"))


async def test_restart_and_scopes_cannot_inherit_coverage():
    service = AuditCoverage(config=CONFIG)
    await cover(service, [row("x")])
    other = ScopeRef(kind=ScopeKind.ISSUE, key="project")
    assert (await cover(service, [row("x")], 60, scope=other)).full
    assert (await cover(AuditCoverage(config=CONFIG), [row("x")], 60)).full


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_partial_visit_does_not_advance_any_mark(failure):
    service = AuditCoverage(config=CONFIG)
    seen = []

    async def fail(candidate):
        seen.append(candidate.issue_key)
        if candidate.issue_key == "b":
            raise failure()

    with pytest.raises(failure):
        await cover(service, [row("a"), row("b")], visitor=fail)
    assert seen == ["a", "b"]
    result = await cover(service, [row("a"), row("b")], 60)
    assert result.full and result.covered == (row("a"), row("b"))


async def test_failed_periodic_full_remains_due():
    service = AuditCoverage(config=CONFIG)
    await cover(service, [row("a")])

    async def fail(candidate):
        raise RuntimeError("read failed")

    with pytest.raises(RuntimeError):
        await cover(service, [row("a")], 120, visitor=fail)
    assert (await cover(service, [row("a")], 121)).full


async def test_same_scope_reentry_refuses_but_other_scope_can_run():
    service = AuditCoverage(config=CONFIG)

    async def reenter(candidate):
        with pytest.raises(ValueError, match="active"):
            await cover(service, [row("a")])
        other = ScopeRef(kind=ScopeKind.PROJECT, key="other")
        assert (await cover(service, [row("a")], scope=other)).full

    await cover(service, [row("a")], visitor=reenter)
    assert not (await cover(service, [row("a")], 60)).full


async def test_snapshot_and_returned_records_are_immutable():
    service = AuditCoverage(config=CONFIG)
    rows = [row("a"), row("b")]

    async def change_input(candidate):
        rows.clear()

    result = await cover(service, rows, visitor=change_input)
    assert result.covered == (row("a"), row("b"))
    with pytest.raises(ValidationError):
        result.covered[0].issue_key = "changed"


async def test_no_records_does_not_advance_a_full_mark():
    service = AuditCoverage(config=CONFIG)
    assert (await cover(service, [])).covered == ()
    result = await cover(service, [row("a")], 60)
    assert result.full


async def test_invalid_input_refuses_before_visiting_and_does_not_poison_cache():
    service = AuditCoverage(config=CONFIG)
    with pytest.raises(ValueError, match="duplicate"):
        await cover(service, [row("a"), row("a")])
    with pytest.raises(ValueError, match="follows"):
        await cover(service, [row("a", 60)])
    with pytest.raises(ValueError, match="timezone"):
        await service.cover(
            scope=SCOPE,
            candidates=[],
            observed_at=NOW.replace(tzinfo=None),
            visit=visit,
        )
    assert (await cover(service, [row("a")])).full
    with pytest.raises(ValueError, match="backwards"):
        await cover(service, [], -1)


def test_coverage_adds_no_clock_or_numeric_policy():
    tree = ast.parse(inspect.getsource(audit_coverage))
    assert not [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float)
    ]
    assert not [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in {"sleep", "now", "time", "monotonic"}
    ]
