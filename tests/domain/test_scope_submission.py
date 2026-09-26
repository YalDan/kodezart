"""Which of two live runs over one scope goes first (KOD-880).

A plain function over records already read, so the order two walks are put
into is arithmetic rather than a judgement — and it is asked here directly,
because a walk-level assertion cannot tell "the later one yielded" from
"the earlier one happened to finish first".
"""

import pytest

from kodezart.domain.scope_submission import prior_live_job
from kodezart.types.domain.job import JobRecord, JobState
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.fakes import FIXTURE_EPOCH

SCOPE = ScopeRef(kind=ScopeKind.PROJECT, key="scoped-project")

MINE = "my-job"


def record(job_id: str, *, lane: str = "workflow") -> JobRecord:
    """One live record over the scope, on *lane*."""
    return JobRecord(
        job_id=job_id,
        lane=lane,
        state=JobState.RUNNING,
        submitted_at=FIXTURE_EPOCH,
        scope=SCOPE,
    )


OTHER = record("other-job", lane="tracker")
FIRST = record("first-job")
SECOND = record("second-job")
MY_RECORD = record(MINE)


@pytest.mark.parametrize(
    ("live", "expected"),
    [
        ((), None),
        ((MY_RECORD,), None),
        ((OTHER, MY_RECORD), OTHER),
        ((MY_RECORD, OTHER), None),
        ((OTHER,), OTHER),
        ((FIRST, SECOND, MY_RECORD), FIRST),
    ],
    ids=[
        "nothing live",
        "only mine",
        "one ahead of mine",
        "mine ahead of one",
        "mine not held",
        "two ahead of mine",
    ],
)
def test_prior_live_job_each_row(live, expected) -> None:
    """The oldest live record goes first, and it is the only one that does.

    A rule that refused on ANY other live record would refuse both of two
    walks that are RUNNING at once — the queue marks a record RUNNING before
    it calls the engine — and the scope would never run at all.
    """
    assert prior_live_job(live=live, job_id=MINE) is expected


def test_a_job_the_records_do_not_hold_yields_to_the_oldest_live_one() -> None:
    """A caller that drove the engine directly has no place in the order.

    It cannot claim to be the earlier of the two, because nothing recorded
    when it started, so it yields.
    """
    assert prior_live_job(live=(OTHER,), job_id="not-submitted") is OTHER
