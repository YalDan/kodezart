"""The record store the queue writes and a scope run's entry reads (KOD-880).

Driven directly, because the two reads on the port are about records in states
the queue reaches at different moments, and a fixture driving the queue could
only observe one of them at a time.
"""

import pytest

from kodezart.adapters.job_registry import InMemoryJobRegistry
from kodezart.types.domain.job import JobRecord, JobState
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.fakes import FIXTURE_EPOCH

SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="scoped-project")
OTHER_SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="another-project")

#: The two lanes a deployment actually submits onto: the HTTP routes' default,
#: and the dispatch lane the scheduled pass uses.
ROUTE_LANE = "workflow"
PASS_LANE = "tracker"


def record(
    job_id: str,
    *,
    lane: str = ROUTE_LANE,
    state: JobState = JobState.RUNNING,
    scope: ScopeRef | None = SCOPE,
) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        lane=lane,
        state=state,
        submitted_at=FIXTURE_EPOCH,
        scope=scope,
    )


def holding(*records: JobRecord) -> InMemoryJobRegistry:
    registry = InMemoryJobRegistry()
    for held in records:
        registry.add(held)
    return registry


async def test_live_for_scope_finds_jobs_on_every_lane_oldest_first() -> None:
    """Whichever lane each was submitted on, in submission order.

    The two submitters a deployment has do not share a lane, so a read
    narrowed to one of them would see neither walk from the other — which is
    the state this store exists to end.
    """
    registry = holding(
        record("posted", lane=ROUTE_LANE),
        record("submitted", lane=PASS_LANE),
    )

    live = await registry.live_for_scope(scope=SCOPE)

    assert [held.job_id for held in live] == ["posted", "submitted"]
    assert [held.lane for held in live] == [ROUTE_LANE, PASS_LANE]


async def test_a_terminal_job_is_not_live() -> None:
    """A run that ended contends over nothing, so it holds up nothing."""
    registry = holding(record("ended", state=JobState.TERMINAL))

    assert await registry.live_for_scope(scope=SCOPE) == ()


async def test_a_queued_job_is_live() -> None:
    """A job waiting for a worker is a walk of this scope that is about to run."""
    registry = holding(record("waiting", state=JobState.QUEUED))

    assert [held.job_id for held in await registry.live_for_scope(scope=SCOPE)] == [
        "waiting"
    ]


@pytest.mark.parametrize(
    "scope",
    [OTHER_SCOPE, None],
    ids=["another scope", "a per-issue fire"],
)
async def test_another_scopes_job_is_not_this_scopes(scope: ScopeRef | None) -> None:
    """Addressed, so a job over another scope and a fire hold up neither."""
    registry = holding(record("elsewhere", scope=scope))

    assert await registry.live_for_scope(scope=SCOPE) == ()


async def test_a_forgotten_job_is_gone_from_every_read() -> None:
    """Eviction is one act: the record answers neither read afterwards."""
    registry = holding(record("evicted"))

    registry.forget("evicted")

    assert await registry.get(job_id="evicted") is None
    assert await registry.live_for_scope(scope=SCOPE) == ()
    assert registry.open() == ()


async def test_open_lists_every_record_that_is_not_terminal() -> None:
    """The shutdown sweep's own roster, whatever each record is addressed at."""
    registry = holding(
        record("running"),
        record("ended", state=JobState.TERMINAL),
        record("fire", scope=None, state=JobState.QUEUED),
    )

    assert [held.job_id for held in registry.open()] == ["running", "fire"]


async def test_an_amendment_replaces_the_record_and_answers_with_it() -> None:
    """One store, so a moved record is moved for every reader of it."""
    registry = holding(record("moving"))

    amended = registry.amend("moving", state=JobState.TERMINAL, queue_position=None)

    assert amended.state is JobState.TERMINAL
    assert await registry.get(job_id="moving") == amended
    assert await registry.live_for_scope(scope=SCOPE) == ()
