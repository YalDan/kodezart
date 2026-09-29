"""The scoped arm's refusals, over the real entry."""

from typing import NoReturn

import pytest

from kodezart.domain.errors import ScopeRunLiveError
from kodezart.services.scope_entry import ScopeEntry
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.job import JobRecord, JobState
from kodezart.types.domain.operation import OperationMemberAbsentError, ScopeLabel
from kodezart.types.domain.scope import ScopeContainer, ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode
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


class Preflight:
    """The scope-plan read assertion, refusing for one unmapped label or none.

    Counts its calls, so a case states that the assertion was made rather
    than inferring it from the absence of a refusal.
    """

    def __init__(self, *, unmapped: str | None = None) -> None:
        self._unmapped = unmapped
        self.calls = 0

    def require_scope_plan_reads(self) -> None:
        self.calls += 1
        if self._unmapped is not None:
            raise OperationMemberAbsentError(
                missing=f"issue_labels[{self._unmapped!r}]",
                stops="scope plan barriers cannot be read",
            )

    def require_issue_classification_reads(
        self, *, additional_keys: frozenset[str] = frozenset()
    ) -> None:
        raise AssertionError("the entry asked for the issue classification reads")


def entry_over(*records, preflight=None, arm_for=unreached_arm):
    """One entry whose registry holds *records*, in the order given."""
    board_double = CountingBoard()
    registry = FakeJobQueue()
    for held in records:
        registry.records[held.job_id] = held
    return (
        ScopeEntry(
            approvals=board_double,
            registry=registry,
            preflight=preflight if preflight is not None else Preflight(),
            arm_for=arm_for,
        ),
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


# ---------------------------------------------------------------------------
# An operation that maps no criterion or no decision label is refused after
# the approval read and before any session: the groom and prep prompts name
# both labels.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("unmapped", ["criterion", "decision"])
async def test_an_operation_missing_a_scope_plan_label_is_refused_before_delivery(
    unmapped: str,
) -> None:
    """The refusal names the label, spends one approval read, reaches no arm."""
    arms_asked: list[str | None] = []

    def recording_arm(url: str | None) -> NoReturn:
        arms_asked.append(url)
        raise AssertionError("the entry reached for a delivery arm")

    preflight = Preflight(unmapped=unmapped)
    unit, board_double = entry_over(preflight=preflight, arm_for=recording_arm)

    run = unit.run(
        prompt="run the scope",
        repo_path=None,
        repo_url="https://example.invalid/repo",
        base_spec=trunk_base("unused-request-default"),
        scope=SCOPE,
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=[],
        cache_key="scoped-job",
    )
    with pytest.raises(OperationMemberAbsentError) as caught:
        await anext(aiter(run))

    assert caught.value.missing == f"issue_labels['{unmapped}']"
    assert preflight.calls == 1
    # Exactly the reads one admitted run's approval question spends, and
    # nothing after them.
    admitted, admitted_board = entry_over()
    await admitted.admit(scope=SCOPE, job_id="scoped-job")
    assert admitted_board.calls["read_scope_labels"] == 1
    assert board_double.calls == admitted_board.calls
    assert arms_asked == []
