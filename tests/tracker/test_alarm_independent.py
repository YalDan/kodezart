"""Independent publication-time controls at the real adapter's MCP boundary."""

import pytest

from kodezart.core.errors import TrackerProtocolError
from kodezart.services.run_surface_lease import RunSurfaceLease
from tests.tracker.conftest import APPROVED_ISSUE
from tests.tracker.test_run_alarm_records import (
    DURATION,
    JOB,
    Boundary,
    address,
    alarm,
    read,
    store,
)


class ChangingRecordBoundary(Boundary):
    def __init__(self):
        super().__init__()
        self.arm = False
        self.comment_reads = 0
        self.comment_id = None
        self.damaged_body = None
        self.after_external_write = None

    async def call_tool(self, *, name, arguments):
        if self.arm and name == "list_comments":
            self.comment_reads += 1
            # The first list is record_run_alarm's successful parse. The next
            # is the actual universal writer's fresh addressed read.
            if self.comment_reads == 2:
                self.arm = False
                await self.server.call_tool(
                    name="save_comment",
                    arguments={"id": self.comment_id, "body": self.damaged_body},
                )
                self.after_external_write = tuple(self.server.calls)
        return await super().call_tool(name=name, arguments=arguments)


@pytest.mark.parametrize("damage_after_initial_read", [False, True])
async def test_alarm_damage_seen_by_universal_writer_is_not_overwritten(
    damage_after_initial_read,
):
    boundary = ChangingRecordBoundary()
    port = boundary.adapter()
    value = alarm()
    await store(port, value)
    existing = next(
        comment
        for comment in await port.list_comments(issue_key=APPROVED_ISSUE)
        if comment.body.startswith(address(value).marker)
    )
    async with RunSurfaceLease(
        tracker=port,
        job_id=JOB,
        surfaces=frozenset({address(value)}),
        lease_seconds=DURATION,
    ):
        boundary.comment_id = existing.comment_key
        boundary.damaged_body = existing.body + "\nexternal damage"
        boundary.arm = damage_after_initial_read
        if damage_after_initial_read:
            with pytest.raises(TrackerProtocolError):
                await port.record_run_alarm(
                    issue_key=APPROVED_ISSUE, alarm=value, holder=JOB
                )
            assert boundary.after_external_write is not None
            assert not any(
                name == "save_comment"
                for name, _ in boundary.server.calls[
                    len(boundary.after_external_write) :
                ]
            )
        else:
            before = tuple(boundary.server.calls)
            await port.record_run_alarm(
                issue_key=APPROVED_ISSUE, alarm=value, holder=JOB
            )
            assert not any(
                name == "save_comment"
                for name, _ in boundary.server.calls[len(before) :]
            )
            assert await read(port, value) == value
