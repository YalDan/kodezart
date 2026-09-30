"""One returned watch value carries the evidence across task boundaries."""

import asyncio

import httpx
import pytest
from pydantic import TypeAdapter, ValidationError

from kodezart.core.protocols import CIMonitor
from kodezart.services.check_classification import classify_red_checks
from kodezart.types.domain.check_observation import (
    AbsentChecks,
    CIWatchResult,
    IncompleteChecks,
    ObservedChecks,
)
from tests.adapters.test_ci_watch_evidence import BRANCH, REPO, SHA, check
from tests.adapters.test_github_api import _make_client
from tests.fakes import FakeCIMonitor


async def test_completed_watch_returns_one_portable_observation():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "check_runs": [check(), check(identity=2, name="lint", passed=True)],
            },
        )

    client = _make_client(handler)
    try:
        observed = await asyncio.create_task(
            client.wait_for_checks(repo_url=REPO, ref=BRANCH)
        )
        assert observed.commit_sha == SHA
        assert observed.checks_passed is False
        assert observed.check_names == {"unit", "lint"}
        assert observed.failed_check_names == {"unit"}
        assert observed.summary == "CI failed: unit"
        assert len(requests) == 1
    finally:
        await client.close()


@pytest.mark.parametrize("passed", [True, False])
async def test_same_sha_classification_refuses_a_changed_rerun_identity(passed):
    from kodezart.domain.errors import CheckObservationError

    initial = ObservedChecks(
        commit_sha=SHA,
        checks_passed=False,
        check_names=frozenset({"unit"}),
        failed_check_names=frozenset({"unit"}),
        summary="",
    )
    monitor = FakeCIMonitor(passed=passed, observed_sha_by_ref={SHA: "b" * 40})
    with pytest.raises(CheckObservationError, match="changed the immutable commit"):
        await classify_red_checks(
            ci=monitor, repo_url=REPO, repository=None, initial=initial, max_attempts=1
        )
    assert monitor.rerun_calls == [(REPO, SHA)]


@pytest.mark.parametrize(
    "changes",
    [
        {"checks_passed": True},
        {"failed_check_names": frozenset()},
        {"failed_check_names": frozenset({"outside"})},
        {"check_names": frozenset()},
        {"check_names": frozenset({"unit", " "})},
    ],
)
def test_completed_observation_refuses_incoherent_evidence(changes):
    values = {
        "commit_sha": SHA,
        "checks_passed": False,
        "check_names": frozenset({"unit"}),
        "failed_check_names": frozenset({"unit"}),
        "summary": "",
        **changes,
    }
    with pytest.raises(ValidationError):
        ObservedChecks.model_validate(values)


@pytest.mark.parametrize(
    "observation",
    [
        ObservedChecks(
            commit_sha=SHA,
            checks_passed=True,
            check_names=frozenset({"unit"}),
            failed_check_names=frozenset(),
            summary="",
        ),
        AbsentChecks(summary="No run appeared"),
        IncompleteChecks(
            commit_shas=frozenset({SHA}),
            check_names=frozenset({"unit"}),
            failed_check_names=frozenset(),
            observed_count=1,
            expected_count=2,
            summary="The roster is still incomplete",
        ),
    ],
)
def test_watch_variants_round_trip_without_task_or_vendor_state(observation):
    schema = TypeAdapter(CIWatchResult)
    assert schema.validate_json(schema.dump_json(observation)) == observation


def test_monitor_returns_evidence_without_a_second_reader():
    methods = {
        name
        for name, value in vars(CIMonitor).items()
        if not name.startswith("_") and callable(value)
    }
    assert methods == {"wait_for_checks", "rerun_checks", "checks_declared"}
