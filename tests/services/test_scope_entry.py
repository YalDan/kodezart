"""The scoped arm's refusals, over the real entry."""

import pytest

from kodezart.domain.errors import ScopeRunLiveError
from kodezart.services.scope_entry import ScopeEntry
from kodezart.types.domain.job import JobRecord, JobState
from kodezart.types.domain.operation import ScopeLabel
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from tests.fakes import FIXTURE_EPOCH, FakeJobQueue, FakeTrackerPort

SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="scoped-project")


def board():
    """The addressed project, carrying the approval label."""
    return FakeTrackerPort(
        scope_containers=[
            ScopeContainer(
                ref=SCOPE,
                name="scoped project",
                description="",
                url="https://tracker.invalid/project/scoped-project",
            )
        ],
        scope_label_members={SCOPE: frozenset({ScopeLabel.APPROVED})},
    )


def unreached_arm(_url):
    raise AssertionError("the entry reached for a delivery arm")


# ---------------------------------------------------------------------------
# KOD-880 — a scope another job in this process is already walking is refused
# at the entry, before any member is read.
# ---------------------------------------------------------------------------

#: The lane the HTTP routes submit onto, which is not the dispatch lane.
LANE = "workflow"


def live_record(job_id: str, *, lane: str = LANE, state=JobState.RUNNING):
    return JobRecord(
        job_id=job_id,
        lane=lane,
        state=state,
        submitted_at=FIXTURE_EPOCH,
        scope=SCOPE,
    )


class CountingBoard:
    """The approval reads, each counted, over the board every other case uses.

    Counted rather than forbidden, so the claim "nothing about the scope was
    read" is a number read off the double and not the absence of a crash.
    """

    def __init__(self):
        self._board = board()
        self.calls: dict[str, int] = dict.fromkeys(
            (
                "read_scope_labels",
                "execution_approved",
                "container_metadata",
                "scope_issues",
            ),
            0,
        )

    def __getattr__(self, name):
        attribute = getattr(self._board, name)
        if name not in self.calls:
            return attribute

        async def counted(**kwargs):
            self.calls[name] += 1
            return await attribute(**kwargs)

        return counted


def entry_over(*records):
    """One entry whose registry holds *records*, in the order given."""
    board_double = CountingBoard()
    registry = FakeJobQueue()
    for held in records:
        registry.records[held.job_id] = held
    return (
        ScopeEntry(approvals=board_double, registry=registry, arm_for=unreached_arm),
        board_double,
    )


async def test_a_scope_another_job_is_walking_is_refused_before_any_read():
    """The refusal costs the board nothing at all, not even the approval read.

    A scope is a queue item: two walks of one scope contend over every lane of
    it, so the one that yields should spend no request finding out.
    """
    unit, board_double = entry_over(live_record("earlier-job"))

    with pytest.raises(ScopeRunLiveError) as caught:
        await unit.admit(scope=SCOPE, job_id="later-job")

    assert caught.value.job_id == "earlier-job"
    assert caught.value.lane == LANE
    assert caught.value.ref == SCOPE
    assert "earlier-job" in str(caught.value)
    assert set(board_double.calls.values()) == {0}


async def test_the_earlier_of_two_live_jobs_proceeds():
    """One of two concurrent walks goes on, and it is the earlier one.

    Both are RUNNING at once — the queue marks a record RUNNING before it
    calls the engine — so a rule refusing on any other live job would refuse
    both and the scope would never run.
    """
    mine = live_record("mine")
    theirs = live_record("theirs", lane="tracker")

    first, board_double = entry_over(mine, theirs)
    await first.admit(scope=SCOPE, job_id="mine")
    assert board_double.calls["read_scope_labels"] == 1

    second, _ = entry_over(theirs, mine)
    with pytest.raises(ScopeRunLiveError) as caught:
        await second.admit(scope=SCOPE, job_id="mine")
    assert caught.value.job_id == "theirs"
    assert caught.value.lane == "tracker"


async def test_a_run_the_registry_does_not_hold_yields_to_any_live_one():
    """A caller that drove the engine directly cannot claim to be the earlier."""
    unit, _ = entry_over(live_record("queued-run"))

    with pytest.raises(ScopeRunLiveError, match="queued-run"):
        await unit.admit(scope=SCOPE, job_id="direct-caller")


async def test_a_terminal_job_over_the_scope_does_not_refuse():
    """A run that ended holds up nothing, so the next one reaches the board."""
    unit, board_double = entry_over(live_record("ended", state=JobState.TERMINAL))

    await unit.admit(scope=SCOPE, job_id="next-job")

    assert board_double.calls["read_scope_labels"] == 1
