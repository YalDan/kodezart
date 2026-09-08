"""Observer-recorded landing is one field on the existing work-ref carrier."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from kodezart.domain.errors import DuplicateWorkRefError
from kodezart.types.domain.branch import WorkRef, WorkRefLanding, WorkRefRole
from tests.tracker.conftest import APPROVED_ISSUE, FIXTURE_NOW


def work_ref(*, landing=WorkRefLanding.UNKNOWN, sha="0" * 40):
    return WorkRef(
        issue_id=APPROVED_ISSUE,
        role=WorkRefRole.DELIVERABLE,
        branch="feature/recorded-input",
        pushed_head_sha=sha,
        landing=landing,
        recorded_at=FIXTURE_NOW,
    )


def test_landing_is_exactly_one_three_state_field_on_the_existing_carrier():
    assert set(WorkRef.model_fields) == {
        "issue_id",
        "role",
        "branch",
        "pushed_head_sha",
        "landing",
        "recorded_at",
    }
    assert WorkRef.model_fields["landing"].annotation is WorkRefLanding
    assert [(item.name, item.value) for item in WorkRefLanding] == [
        ("LANDED", "landed"),
        ("NOT_LANDED", "not_landed"),
        ("UNKNOWN", "unknown"),
    ]
    original = work_ref().model_dump()
    del original["landing"]
    assert WorkRef.model_validate(original).landing is WorkRefLanding.UNKNOWN


@pytest.mark.parametrize("value", [True, False, None, "", "merged", "not landed"])
def test_invalid_or_boolean_landing_refuses(value):
    with pytest.raises(ValidationError):
        work_ref(landing=value)


@pytest.mark.parametrize("landing", list(WorkRefLanding))
@pytest.mark.parametrize("sha", [None, "unknown", "0000000"])
async def test_landing_round_trips_without_changing_existing_fields(
    tracker, tracker_writes, landing, sha
):
    offered = work_ref(landing=landing, sha=sha)
    await tracker.record_work_ref(ref=offered)
    writes = tracker_writes()
    await tracker.record_work_ref(
        ref=offered.model_copy(update={"recorded_at": FIXTURE_NOW + timedelta(days=1)})
    )
    (read,) = await tracker.work_refs(issue_key=APPROVED_ISSUE)
    assert read.identity() == offered.identity()
    assert read.landing is landing
    assert read.pushed_head_sha == sha
    assert tracker_writes() == writes


async def test_recording_a_different_landing_is_not_an_idempotent_noop(tracker):
    await tracker.record_work_ref(ref=work_ref())
    with pytest.raises(DuplicateWorkRefError):
        await tracker.record_work_ref(ref=work_ref(landing=WorkRefLanding.LANDED))
    (retained,) = await tracker.work_refs(issue_key=APPROVED_ISSUE)
    assert retained.landing is WorkRefLanding.UNKNOWN


@pytest.mark.parametrize("landing", list(WorkRefLanding))
def test_landing_is_frozen(landing):
    value = work_ref(landing=landing)
    with pytest.raises(ValidationError):
        value.landing = WorkRefLanding.LANDED
