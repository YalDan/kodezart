"""What the entry does with an organize report, over the real entry.

The composed cases substitute their own entry to pin the engine's reaction
to a halt; these drive ``ScopeEntry`` itself, so the reaction they pin is
the entry's own.
"""

import pytest

from kodezart.domain.errors import OrganizeHaltError, ScopeRunLiveError
from kodezart.services.scope_entry import ScopeEntry
from kodezart.types.domain.job import JobRecord, JobState
from kodezart.types.domain.operation import RepoEntry
from kodezart.types.domain.organize import MandateKind
from kodezart.types.domain.organize_owner import (
    OrganizeReport,
    StageHaltCause,
    StageHaltReport,
)
from tests.fakes import FIXTURE_EPOCH, FakeJobQueue
from tests.integration.test_scope_runtime import ORIGIN, SCOPE, board

REPOSITORY = RepoEntry(url=ORIGIN, trunk="trunk")

HALTED = OrganizeReport(
    halt=StageHaltReport.model_validate(
        {
            "cause": "stage_incomplete",
            "phase": "criteria",
            "unlabelledIssueIds": ["B"],
        }
    )
)


class StubOrganizer:
    """Answers one report and records that it was asked."""

    def __init__(self, report):
        self.report = report
        self.runs = []

    async def run(self, *, scope, repository, job_id):
        self.runs.append((scope, repository, job_id))
        return self.report


def entry(organizer):
    return ScopeEntry(
        approvals=board(lanes=("A",)),
        stages_for=lambda _url: organizer,
        registry=FakeJobQueue(),
    )


async def admit(organizer):
    await entry(organizer).admit(scope=SCOPE, repository=REPOSITORY, job_id="entry-job")


async def test_a_halted_report_refuses_the_run_and_carries_the_halt():
    """A halt is the stages' answer, so the run does not begin on it.

    Returning here would start a walk over members the stages said are not
    ready, with nothing carrying why.
    """
    organizer = StubOrganizer(HALTED)

    with pytest.raises(OrganizeHaltError) as caught:
        await admit(organizer)

    assert caught.value.report.halt.cause is StageHaltCause.STAGE_INCOMPLETE
    assert caught.value.report.halt.unlabelled_issue_ids == ("B",)
    assert len(organizer.runs) == 1


async def test_a_completed_report_admits_the_run():
    organizer = StubOrganizer(
        OrganizeReport(completed_phases=(MandateKind.TICKET, MandateKind.CRITERIA))
    )

    await admit(organizer)

    assert len(organizer.runs) == 1


async def test_an_operation_with_no_table_admits_an_approved_scope_unorganized():
    """No table means no stage to run, and no organizer to ask."""
    asked = []

    def stages_for(url):
        asked.append(url)
        return None

    await ScopeEntry(
        approvals=board(lanes=("A",)),
        stages_for=stages_for,
        registry=FakeJobQueue(),
    ).admit(scope=SCOPE, repository=REPOSITORY, job_id="entry-job")

    assert asked == [ORIGIN]


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
        self._board = board(lanes=("A",))
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


def refusing_stages(url: str):
    raise AssertionError(f"the entry asked for the stages of {url}")


def entry_over(*records, stages_for=refusing_stages):
    """One entry whose registry holds *records*, in the order given."""
    board_double = CountingBoard()
    registry = FakeJobQueue()
    for held in records:
        registry.records[held.job_id] = held
    return (
        ScopeEntry(approvals=board_double, stages_for=stages_for, registry=registry),
        board_double,
    )


async def test_a_scope_another_job_is_walking_is_refused_before_any_read():
    """The refusal costs the board nothing at all, not even the approval read.

    A scope is a queue item: two walks of one scope contend over every lane of
    it, so the one that yields should spend no request finding out.
    """
    unit, board_double = entry_over(live_record("earlier-job"))

    with pytest.raises(ScopeRunLiveError) as caught:
        await unit.admit(scope=SCOPE, repository=REPOSITORY, job_id="later-job")

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

    first, board_double = entry_over(mine, theirs, stages_for=lambda _url: None)
    await first.admit(scope=SCOPE, repository=REPOSITORY, job_id="mine")
    assert board_double.calls["read_scope_labels"] == 1

    second, _ = entry_over(theirs, mine)
    with pytest.raises(ScopeRunLiveError) as caught:
        await second.admit(scope=SCOPE, repository=REPOSITORY, job_id="mine")
    assert caught.value.job_id == "theirs"
    assert caught.value.lane == "tracker"


async def test_a_run_the_registry_does_not_hold_yields_to_any_live_one():
    """A caller that drove the engine directly cannot claim to be the earlier."""
    unit, _ = entry_over(live_record("queued-run"))

    with pytest.raises(ScopeRunLiveError, match="queued-run"):
        await unit.admit(scope=SCOPE, repository=REPOSITORY, job_id="direct-caller")


async def test_a_terminal_job_over_the_scope_does_not_refuse():
    """A run that ended holds up nothing, so the next one reaches the board."""
    unit, board_double = entry_over(
        live_record("ended", state=JobState.TERMINAL),
        stages_for=lambda _url: None,
    )

    await unit.admit(scope=SCOPE, repository=REPOSITORY, job_id="next-job")

    assert board_double.calls["read_scope_labels"] == 1
