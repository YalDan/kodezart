"""P1 full-address persistence over both the shipped port and its consumer double."""

import asyncio

import pytest

from kodezart.core.errors import TrackerProtocolError
from kodezart.core.owned_tasks import settle
from kodezart.domain.errors import (
    DuplicateCommentMarkerError,
    SurfaceLeaseError,
    SurfaceLeaseLostError,
)
from kodezart.domain.run_alarm_record import render_run_alarm, run_alarm_marker
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.operation import OperationMemberAbsentError
from kodezart.types.domain.run_alarm import (
    AlarmBound,
    AlarmReading,
    AlarmSignal,
    CountEvidence,
    RunAlarm,
    ScopeSubject,
    SurfaceEvidence,
    SurfaceSubject,
    TextEvidence,
)
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.fakes import FakeTrackerPort
from tests.tracker.conftest import APPROVED_ISSUE, FixtureClock, fixture_server
from tests.tracker.marker_config import MARKER_PREFIXES
from tests.tracker.test_linear_mcp_tracker import tracker_over

PREFIXES = {**MARKER_PREFIXES, "run_alarm": "fixture-alarm"}
JOB = "actual-job-77"
DURATION = 37.25


def alarm(*, marker="record:a", scope=False, signal=AlarmSignal.SURFACE_CONTENDED):
    surface = WritableSurface(
        kind=SurfaceKind.MARKER_COMMENT,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="observed-native-issue"),
        marker=marker,
    )
    subject = (
        ScopeSubject(scope_key="scope/opaque")
        if scope
        else SurfaceSubject(
            scope_key="scope/opaque", lane_key="lane/opaque", surface=surface
        )
    )
    return RunAlarm(
        subject=subject,
        signal=signal,
        readings=(
            AlarmReading(
                source_ref="native/first",
                value=TextEvidence(value=" x\n"),
                at_sha="actual-head",
            ),
            AlarmReading(
                source_ref="native/surface", value=SurfaceEvidence(value=surface)
            ),
            AlarmReading(source_ref="native/count", value=CountEvidence(value=2)),
        ),
        bound=AlarmBound(
            config_field="run_alarm_max_surface_holders",
            configured_value=1,
            observed_value=2,
        ),
        raised_at_sha="actual-head",
        raised_by=JOB,
    )


def address(value, *, prefixes=PREFIXES):
    return WritableSurface(
        kind=SurfaceKind.MARKER_COMMENT,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=APPROVED_ISSUE),
        marker=run_alarm_marker(
            subject=value.subject, signal=value.signal, marker_prefixes=prefixes
        ),
    )


class Boundary:
    def __init__(self):
        self.clock = FixtureClock()
        self.server = fixture_server(clock=self.clock)
        self.pause = None
        self.reached = asyncio.Event()
        self.resume = asyncio.Event()

    async def call_tool(self, *, name, arguments):
        if self.pause and self.pause(name, arguments):
            self.reached.set()
            await self.resume.wait()
        return await self.server.call_tool(name=name, arguments=arguments)

    def adapter(self, prefixes=PREFIXES):
        return tracker_over(
            self.server, caller=self, marker_prefixes=prefixes, clock=self.clock
        )


@pytest.fixture(params=["linear-mcp", "fake-port"])
async def ports(request):
    boundary = Boundary()
    port = boundary.adapter()
    if request.param == "fake-port":
        port = FakeTrackerPort(
            issues=[await port.read_issue(issue_key=APPROVED_ISSUE)],
            marker_prefixes=PREFIXES,
            clock=boundary.clock,
        )
    return port, boundary


def writes(port, boundary):
    if isinstance(port, FakeTrackerPort):
        return tuple(port.comment_writes)
    return tuple(
        arguments for name, arguments in boundary.server.calls if name == "save_comment"
    )


async def store(port, *values, holder=JOB):
    async with RunSurfaceLease(
        tracker=port,
        job_id=holder,
        surfaces=frozenset(address(v) for v in values),
        lease_seconds=DURATION,
    ) as lease:
        await lease.renew()
        for value in values:
            await settle(
                port.record_run_alarm(
                    issue_key=APPROVED_ISSUE, alarm=value, holder=holder
                )
            )


async def read(port, value):
    return await port.read_run_alarm(
        issue_key=APPROVED_ISSUE, subject=value.subject, signal=value.signal
    )


async def test_real_adapter_implements_alarm_native_roundtrip_port(ports):
    port, boundary = ports
    values = (
        alarm(),
        alarm(marker="record:b"),
        alarm(scope=True),
        alarm(signal=AlarmSignal.WRITE_BACK_MISSING),
    )
    assert len({address(v) for v in values}) == len(values)
    assert await read(port, values[0]) is None
    await store(port, *values)
    assert [await read(port, value) for value in values] == list(values)
    assert not hasattr(values[2].subject, "lane_key")
    assert set(RunAlarm.model_fields) == {
        "subject",
        "signal",
        "readings",
        "bound",
        "raised_at_sha",
        "raised_by",
    }
    # A new adapter/double has no local alarm cache; only persisted comments survive.
    cold = boundary.adapter()
    if isinstance(port, FakeTrackerPort):
        cold = FakeTrackerPort(marker_prefixes=PREFIXES, clock=boundary.clock)
        cold.comments = list(port.comments)
    assert [await read(cold, value) for value in values] == list(values)


async def test_identical_repeat_has_zero_native_mutations_and_update_keeps_address(
    ports,
):
    port, boundary = ports
    value = alarm()
    async with RunSurfaceLease(
        tracker=port,
        job_id=JOB,
        surfaces=frozenset({address(value)}),
        lease_seconds=DURATION,
    ):
        await port.record_run_alarm(issue_key=APPROVED_ISSUE, alarm=value, holder=JOB)
        before = writes(port, boundary)
        await port.record_run_alarm(issue_key=APPROVED_ISSUE, alarm=value, holder=JOB)
        assert writes(port, boundary) == before
        changed = value.model_copy(update={"raised_at_sha": "next-observed-head"})
        await port.record_run_alarm(issue_key=APPROVED_ISSUE, alarm=changed, holder=JOB)
        assert await read(port, value) == changed
        assert len(await port.list_comments(issue_key=APPROVED_ISSUE)) >= 1
        records = [
            c
            for c in await port.list_comments(issue_key=APPROVED_ISSUE)
            if c.body.startswith(address(value).marker)
        ]
        assert len(records) == 1


@pytest.mark.parametrize(
    "mode", ["missing", "foreign", "expired", "identical-without-lease"]
)
async def test_actual_holder_is_required_even_for_identical_payload(ports, mode):
    port, boundary = ports
    value = alarm()
    if mode == "identical-without-lease":
        await store(port, value)
    if mode in {"foreign", "expired"}:
        await port.acquire_surfaces(
            surfaces=frozenset({address(value)}),
            holder="foreign-job" if mode == "foreign" else JOB,
            lease_seconds=DURATION,
        )
    if mode == "expired":
        boundary.clock.advance(seconds=DURATION + 1)
    before = writes(port, boundary)
    with pytest.raises(SurfaceLeaseError):
        await port.record_run_alarm(issue_key=APPROVED_ISSUE, alarm=value, holder=JOB)
    assert writes(port, boundary) == before


@pytest.mark.parametrize(
    "damage", ["framing", "subject", "signal", "duplicate-key", "unknown-field"]
)
async def test_damaged_native_record_is_neither_absence_nor_overwritten(ports, damage):
    port, boundary = ports
    value = alarm()
    body = f"{address(value).marker}\n{render_run_alarm(alarm=value)}"
    if damage == "framing":
        body += "\nunrecorded prose"
    elif damage == "subject":
        body = body.replace('"scope/opaque"', '"another-scope"')
    elif damage == "signal":
        body = body.replace(
            '"signal": "surface_contended"', '"signal": "write_back_missing"'
        )
    elif damage == "duplicate-key":
        body = body.replace('"raisedBy":', '"raisedBy": "impostor",\n  "raisedBy":')
    else:
        body = body.replace('"raisedBy":', '"active": true,\n  "raisedBy":')
    await port.post_comment(issue_key=APPROVED_ISSUE, body=body)
    with pytest.raises(TrackerProtocolError):
        await read(port, value)
    async with RunSurfaceLease(
        tracker=port,
        job_id=JOB,
        surfaces=frozenset({address(value)}),
        lease_seconds=DURATION,
    ):
        before = writes(port, boundary)
        with pytest.raises(TrackerProtocolError):
            await port.record_run_alarm(
                issue_key=APPROVED_ISSUE, alarm=value, holder=JOB
            )
        assert writes(port, boundary) == before


async def test_duplicate_native_address_is_a_typed_refusal(ports):
    port, boundary = ports
    value = alarm()
    await store(port, value)
    body = f"{address(value).marker}\n{render_run_alarm(alarm=value)}"
    await port.post_comment(issue_key=APPROVED_ISSUE, body=body)
    before = writes(port, boundary)
    with pytest.raises(DuplicateCommentMarkerError):
        await read(port, value)
    with pytest.raises(DuplicateCommentMarkerError):
        await port.record_run_alarm(issue_key=APPROVED_ISSUE, alarm=value, holder=JOB)
    assert writes(port, boundary) == before


async def test_disjoint_alarm_addresses_can_be_written_by_concurrent_jobs(ports):
    port, _ = ports
    first, second = alarm(marker="one"), alarm(marker="two")
    await asyncio.gather(
        store(port, first, holder="job-one"), store(port, second, holder="job-two")
    )
    assert await read(port, first) == first
    assert await read(port, second) == second


async def test_explicit_renewal_extends_duration_and_loss_never_reacquires(ports):
    port, boundary = ports
    value = alarm()
    async with RunSurfaceLease(
        tracker=port,
        job_id=JOB,
        surfaces=frozenset({address(value)}),
        lease_seconds=DURATION,
    ) as lease:
        boundary.clock.advance(seconds=DURATION - 1)
        await lease.renew()
        boundary.clock.advance(seconds=2)
        await port.record_run_alarm(issue_key=APPROVED_ISSUE, alarm=value, holder=JOB)
        boundary.clock.advance(seconds=DURATION + 1)
        before = writes(port, boundary)
        with pytest.raises(SurfaceLeaseLostError) as error:
            await lease.renew()
        assert error.value.job_id == JOB
        assert error.value.surfaces == frozenset({address(value)})
        # A refused native renewal can update/retract its existing grant.
        # It must create no new grant or alarm, and cannot retry after loss.
        if not isinstance(port, FakeTrackerPort):
            assert all("id" in call for call in writes(port, boundary)[len(before) :])
        after_loss = writes(port, boundary)
        with pytest.raises(SurfaceLeaseLostError):
            await lease.renew()
        assert writes(port, boundary) == after_loss
    assert await read(port, value) == value


async def test_required_prefix_has_no_fallback_or_mutation():
    boundary = Boundary()
    port = boundary.adapter(prefixes={})
    with pytest.raises(OperationMemberAbsentError):
        await port.record_run_alarm(issue_key=APPROVED_ISSUE, alarm=alarm(), holder=JOB)
    assert not boundary.server.tool_calls("save_comment")


async def test_cancelled_owned_write_settles_then_releases_before_other_job():
    boundary = Boundary()
    port = boundary.adapter()
    value = alarm()
    boundary.pause = lambda name, args: (
        name == "save_comment"
        and str(args.get("body", "")).startswith("[fixture-alarm:")
    )
    task = asyncio.create_task(store(port, value))
    await asyncio.wait_for(boundary.reached.wait(), timeout=2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    with pytest.raises(SurfaceLeaseError):
        await port.acquire_surfaces(
            surfaces=frozenset({address(value)}), holder="other", lease_seconds=DURATION
        )
    boundary.resume.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await read(port, value) == value
    await port.acquire_surfaces(
        surfaces=frozenset({address(value)}), holder="other", lease_seconds=DURATION
    )


@pytest.mark.parametrize("holder", [None, "", "   "])
async def test_no_implicit_holder_can_bypass_port_admission(ports, holder):
    port, boundary = ports
    value = alarm()
    before = writes(port, boundary)
    with pytest.raises(SurfaceLeaseError):
        await port.record_run_alarm(
            issue_key=APPROVED_ISSUE, alarm=value, holder=holder
        )
    assert writes(port, boundary) == before


async def test_in_flight_backend_write_is_explicitly_not_fenced_by_lease_expiry():
    boundary = Boundary()
    port = boundary.adapter()
    value = alarm()
    surface_set = frozenset({address(value)})
    boundary.pause = lambda name, args: (
        name == "save_comment"
        and str(args.get("body", "")).startswith("[fixture-alarm:")
    )
    task = asyncio.create_task(store(port, value))
    await asyncio.wait_for(boundary.reached.wait(), timeout=2)
    boundary.clock.advance(seconds=DURATION * 2 + 1)
    await port.acquire_surfaces(
        surfaces=surface_set, holder="next-real-job", lease_seconds=DURATION
    )
    boundary.resume.set()
    await task
    # The backend accepted a request admitted before expiry. This is a measured
    # limitation, not a conditional-commit/fencing guarantee from the owner.
    assert await read(port, value) == value
    with pytest.raises(SurfaceLeaseError) as error:
        await port.record_run_alarm(issue_key=APPROVED_ISSUE, alarm=value, holder=JOB)
    assert error.value.current_holder == "next-real-job"


async def test_failure_exits_release_the_declared_surface_set(ports):
    port, _ = ports
    value = alarm()
    surface_set = frozenset({address(value)})
    with pytest.raises(RuntimeError, match="caller interrupted before publication"):
        async with RunSurfaceLease(
            tracker=port, job_id=JOB, surfaces=surface_set, lease_seconds=DURATION
        ):
            raise RuntimeError("caller interrupted before publication")
    await port.acquire_surfaces(
        surfaces=surface_set, holder="next-real-job", lease_seconds=DURATION
    )
    assert await read(port, value) is None
