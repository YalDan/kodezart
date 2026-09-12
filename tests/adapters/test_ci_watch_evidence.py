"""Completed watch evidence keeps its original commit across moving branches."""

import asyncio

import httpx
import pytest

from kodezart.domain.errors import CheckObservationError, ForgeAPIError
from kodezart.types.domain.check_observation import (
    AbsentChecks,
    IncompleteChecks,
    ObservedChecks,
)
from tests.adapters.test_github_api import _make_client
from tests.fakes import FakeCIMonitor

REPO = "https://github.com/example/project"
BRANCH = "feature/one"
SHA = "a" * 40


def check(*, identity=1, sha=SHA, passed=False, **changes):
    return {
        "id": identity,
        "name": "unit",
        "head_sha": sha,
        "status": "completed",
        "conclusion": "success" if passed else "failure",
        **changes,
    }


@pytest.fixture(params=["fake", "github"])
async def boundary(request):
    requests = []
    rows = [check()]
    if request.param == "fake":
        monitor = FakeCIMonitor(
            passed=False,
            failed_names=frozenset({"unit"}),
            observed_sha_by_ref={BRANCH: SHA},
        )
        yield monitor, requests
        return

    def handler(req):
        requests.append(req)
        assert req.url.path.endswith(f"/commits/{BRANCH}/check-runs")
        return httpx.Response(200, json={"total_count": len(rows), "check_runs": rows})

    monitor = _make_client(handler)
    try:
        yield monitor, requests
    finally:
        await monitor.close()


async def test_no_task_local_evidence_reader_is_exposed(boundary):
    monitor, requests = boundary
    assert not hasattr(monitor, "observed_checks")
    assert not hasattr(monitor, "failed_check_names")
    assert requests == []


async def test_original_watch_returns_its_complete_evidence_without_another_query(
    boundary,
):
    monitor, requests = boundary
    observed = await monitor.wait_for_checks(repo_url=REPO, ref=BRANCH)
    assert isinstance(observed, ObservedChecks)
    assert observed.commit_sha == SHA
    assert observed.checks_passed is False
    assert observed.failed_check_names == {"unit"}
    assert "unit" in observed.check_names
    count = len(requests)
    assert ObservedChecks.model_validate_json(observed.model_dump_json()) == observed
    assert len(requests) == count


async def test_fake_original_failing_set_survives_changed_unwatched_state():
    monitor = FakeCIMonitor(passed=False, failed_names=frozenset({"original"}))
    original = await monitor.wait_for_checks(repo_url=REPO, ref=BRANCH)
    monitor._failed_names = frozenset({"later"})
    later = await monitor.wait_for_checks(repo_url=REPO, ref=BRANCH)
    assert original.failed_check_names == {"original"}
    assert later.failed_check_names == {"later"}


async def test_watch_return_is_portable_after_the_observing_task_ends(boundary):
    monitor, _ = boundary
    observed = await asyncio.create_task(
        monitor.wait_for_checks(repo_url=REPO, ref=BRANCH)
    )
    assert observed.commit_sha == SHA
    assert observed.failed_check_names == {"unit"}


@pytest.mark.parametrize(
    "problem",
    [
        "missing-sha",
        "null-sha",
        "blank-sha",
        "mixed-sha",
        "duplicate",
        "pending",
        "unknown",
    ],
)
async def test_incomplete_native_identity_or_terminal_set_is_unreadable(problem):
    rows = [check()]
    if problem == "missing-sha":
        del rows[0]["head_sha"]
    elif problem == "null-sha":
        rows[0]["head_sha"] = None
    elif problem == "blank-sha":
        rows[0]["head_sha"] = " "
    elif problem == "mixed-sha":
        rows.append(check(identity=2, sha="b" * 40))
    elif problem == "duplicate":
        rows.append(check())
    elif problem == "pending":
        rows[0]["status"] = "in_progress"
    else:
        rows.append(check(identity=2, conclusion="unrecognized"))
    client = _make_client(
        lambda _: httpx.Response(
            200, json={"total_count": len(rows), "check_runs": rows}
        ),
        ci_poll_max_attempts=1,
    )
    try:
        if problem == "pending":
            result = await client.wait_for_checks(repo_url=REPO, ref=BRANCH)
            assert isinstance(result, IncompleteChecks)
            assert result.commit_shas == {SHA}
            assert result.check_names == {"unit"}
        else:
            with pytest.raises(CheckObservationError):
                await client.wait_for_checks(repo_url=REPO, ref=BRANCH)
    finally:
        await client.close()


async def test_requested_sha_must_match_the_native_check_identity():
    client = _make_client(
        lambda _: httpx.Response(
            200, json={"total_count": 1, "check_runs": [check(sha="b" * 40)]}
        )
    )
    try:
        with pytest.raises(CheckObservationError, match="requested SHA"):
            await client.wait_for_checks(repo_url=REPO, ref=SHA)
    finally:
        await client.close()


@pytest.mark.parametrize("later", ["failure", "cancel", "empty", "pending"])
async def test_later_unsuccessful_watch_cannot_reuse_older_native_evidence(later):
    first = True

    async def handler(request):
        if first:
            return httpx.Response(200, json={"total_count": 1, "check_runs": [check()]})
        if request.url.path.endswith("/workflows"):
            return httpx.Response(200, json={"total_count": 0, "workflows": []})
        if later == "failure":
            return httpx.Response(403)
        if later == "cancel":
            await asyncio.Event().wait()
        rows = [] if later == "empty" else [check(status="in_progress")]
        return httpx.Response(200, json={"total_count": len(rows), "check_runs": rows})

    client = _make_client(
        handler, ci_poll_max_attempts=1, ci_no_workflows_grace_polls=1
    )
    try:
        original = await client.wait_for_checks(repo_url=REPO, ref=BRANCH)
        assert original.commit_sha == SHA
        first = False
        if later == "failure":
            with pytest.raises(ForgeAPIError):
                await client.wait_for_checks(repo_url=REPO, ref=BRANCH)
        elif later == "cancel":
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.01):
                    await client.wait_for_checks(repo_url=REPO, ref=BRANCH)
        else:
            later_watch = await client.wait_for_checks(repo_url=REPO, ref=BRANCH)
            expected = AbsentChecks if later == "empty" else IncompleteChecks
            assert isinstance(later_watch, expected)
        assert original.commit_sha == SHA
        assert original.failed_check_names == {"unit"}
        assert not hasattr(client, "observed_checks")
    finally:
        await client.close()
