"""Fixed admission preserves real writer failures and caller cancellation."""

import asyncio

import pytest

from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.types.domain.gating import RepoVisibility, ScanFailureKind
from tests.adapters.test_judgment_scanner import audit_result
from tests.chains.test_outbound_gating import make_engine, run_engine
from tests.fakes import FakePRCreator, FakeVisibilityResolver
from tests.integration.test_authored_aggregate_admission import (
    COUNT_CLAIM,
    gate_with_judge,
)
from tests.integration.test_private_reference_writes import DescriptionExecutor


class FailingBodyJudge:
    def __init__(self, failure):
        self.failure = failure
        self.entered = asyncio.Event()
        self.closed = asyncio.Event()

    async def stream(self, **kwargs):
        if COUNT_CLAIM not in kwargs["prompt"]:
            yield audit_result([])
            return
        self.entered.set()
        try:
            if self.failure is None:
                await asyncio.Event().wait()
            else:
                raise self.failure
        finally:
            self.closed.set()


@pytest.mark.parametrize("visibility", [RepoVisibility.PUBLIC, RepoVisibility.UNKNOWN])
@pytest.mark.parametrize("privacy", [False, True])
@pytest.mark.parametrize(
    "error,kind",
    [
        (OSError("temporary"), ScanFailureKind.TRANSPORT_ERROR),
        (TimeoutError(), ScanFailureKind.TIMEOUT),
    ],
)
async def test_unreadable_authored_body_never_reaches_the_actual_pr_writer(
    tmp_path, visibility, privacy, error, kind
):
    judge = FailingBodyJudge(error)
    creator = FakePRCreator()
    engine = make_engine(
        pr_creator=creator,
        gate=await gate_with_judge(tmp_path, judge, privacy=privacy),
        visibility_resolver=FakeVisibilityResolver(visibility),
        executor=DescriptionExecutor(COUNT_CLAIM),
    )
    with pytest.raises(OutboundContentBlockedError) as caught:
        await run_engine(engine)
    assert caught.value.failure is kind
    assert creator.calls == []
    assert judge.closed.is_set()


@pytest.mark.parametrize("privacy", [False, True])
async def test_cancelled_body_judgment_stays_cancelled_without_a_publication(
    tmp_path, privacy
):
    judge = FailingBodyJudge(None)
    creator = FakePRCreator()
    engine = make_engine(
        pr_creator=creator,
        gate=await gate_with_judge(tmp_path, judge, privacy=privacy),
        visibility_resolver=FakeVisibilityResolver(RepoVisibility.UNKNOWN),
        executor=DescriptionExecutor(COUNT_CLAIM),
    )
    task = asyncio.create_task(run_engine(engine))
    try:
        await asyncio.wait_for(judge.entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert creator.calls == []
    assert judge.closed.is_set()
