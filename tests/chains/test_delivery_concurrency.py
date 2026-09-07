"""One configured semaphore bounds watches across all lanes on a coordinator."""

import asyncio

import pytest

from kodezart.core.config import AppConfig
from kodezart.domain.errors import ForgeAPIError
from tests.chains.test_delivery_runtime import BASE, SHA, context, dispatch, setup
from tests.fakes import FakeCIMonitor, FakeGitService


class HeldMonitor(FakeCIMonitor):
    def __init__(self, bound):
        super().__init__()
        self.bound = bound
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.maximum = 0
        self.refs = []

    async def wait_for_checks(self, *, repo_url, ref):
        self.refs.append(ref)
        self.active += 1
        self.maximum = max(self.active, self.maximum)
        if self.active == self.bound:
            self.entered.set()
        try:
            await self.release.wait()
            return True, "Observed green."
        finally:
            self.active -= 1


@pytest.mark.parametrize("bound", [1, 2, 4])
async def test_n_plus_one_lanes_share_the_configured_watch_bound(bound):
    monitor = HeldMonitor(bound)
    branches = [f"lane/{index}" for index in range(bound + 1)]
    fixture = setup(
        monitor=monitor,
        git=FakeGitService(
            remote_branch_shas={BASE: "b" * 40, **dict.fromkeys(branches, SHA)}
        ),
        config=AppConfig(delivery_max_concurrent_watches=bound),
    )
    tasks = [
        asyncio.create_task(
            fixture.coordinator.deliver(
                dispatch(lane_key=branch, head_branch=branch),
                feature_branch=branch,
                final_commit_sha=SHA,
                context=context(),
            )
        )
        for branch in branches
    ]
    try:
        await asyncio.wait_for(monitor.entered.wait(), timeout=2)
        await asyncio.sleep(0)
        assert monitor.maximum == bound
        assert len(monitor.refs) == bound
    finally:
        monitor.release.set()
        results = await asyncio.gather(*tasks)
    assert len(results) == bound + 1
    assert {result.head_branch for result in results} == set(branches)
    assert monitor.maximum == bound
    assert monitor.active == 0
    assert sorted(monitor.refs) == sorted(branches)


@pytest.mark.parametrize("cancel", [False, True])
async def test_watch_failure_or_cancellation_releases_its_slot(cancel):
    entered = asyncio.Event()
    hold = asyncio.Event()
    failure = ForgeAPIError("watch failed", status_code=None, detail="test")

    class FailingFirst(FakeCIMonitor):
        async def wait_for_checks(self, *, repo_url, ref):
            if ref == "lane/first":
                entered.set()
                await hold.wait()
                raise failure
            return True, "Second lane completes."

    fixture = setup(
        monitor=FailingFirst(),
        git=FakeGitService(
            remote_branch_shas={BASE: "b" * 40, "lane/first": SHA, "lane/second": SHA}
        ),
        config=AppConfig(delivery_max_concurrent_watches=1),
    )

    async def run(branch):
        return await fixture.coordinator.deliver(
            dispatch(lane_key=branch, head_branch=branch),
            feature_branch=branch,
            final_commit_sha=SHA,
            context=context(),
        )

    first = asyncio.create_task(run("lane/first"))
    await asyncio.wait_for(entered.wait(), timeout=2)
    second = asyncio.create_task(run("lane/second"))
    if cancel:
        first.cancel()
    else:
        hold.set()
    with pytest.raises(asyncio.CancelledError if cancel else ForgeAPIError):
        await first
    result = await asyncio.wait_for(second, timeout=2)
    assert result.head_branch == "lane/second"
