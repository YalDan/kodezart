"""Completed watch evidence keeps its original commit across moving branches."""

import asyncio

import httpx
import pytest

from kodezart.core.protocols import CIObservationReader
from kodezart.domain.errors import CheckObservationError, ForgeAPIError
from kodezart.types.domain.check_observation import ObservedChecks
from tests.adapters.test_github_api import _make_client
from tests.fakes import FakeCIMonitor, FakeCIObservationReader

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
        reader = FakeCIObservationReader()
        monitor = FakeCIMonitor(
            passed=False,
            failed_names=frozenset({"unit"}),
            observation_reader=reader,
            observed_sha_by_ref={BRANCH: SHA},
        )
        yield monitor, reader, requests
        return

    def handler(req):
        requests.append(req)
        assert req.url.path.endswith(f"/commits/{BRANCH}/check-runs")
        return httpx.Response(200, json={"total_count": len(rows), "check_runs": rows})

    monitor = _make_client(handler)
    try:
        yield monitor, monitor, requests
    finally:
        await monitor.close()


async def test_no_watch_is_not_a_readable_observation(boundary):
    _, reader, requests = boundary
    assert isinstance(reader, CIObservationReader)
    with pytest.raises(CheckObservationError):
        await reader.observed_checks(repo_url=REPO, ref=BRANCH)
    assert requests == []


async def test_original_watch_is_read_without_another_forge_request(boundary):
    monitor, reader, requests = boundary
    passed, _ = await monitor.wait_for_checks(repo_url=REPO, ref=BRANCH)
    assert passed is False
    count = len(requests)
    expected = ObservedChecks(commit_sha=SHA, checks_passed=False)
    assert await reader.observed_checks(repo_url=REPO, ref=BRANCH) == expected
    assert await reader.observed_checks(repo_url=REPO, ref=BRANCH) == expected
    assert await monitor.failed_check_names(repo_url=REPO, ref=BRANCH) == {"unit"}
    assert len(requests) == count


async def test_fake_original_failing_set_survives_changed_unwatched_state():
    monitor = FakeCIMonitor(passed=False, failed_names=frozenset({"original"}))
    await monitor.wait_for_checks(repo_url=REPO, ref=BRANCH)
    monitor._failed_names = frozenset({"later"})
    assert await monitor.failed_check_names(repo_url=REPO, ref=BRANCH) == {"original"}
    await monitor.wait_for_checks(repo_url=REPO, ref=BRANCH)
    assert await monitor.failed_check_names(repo_url=REPO, ref=BRANCH) == {"later"}


@pytest.mark.parametrize("repo,ref", [(REPO + "-other", BRANCH), (REPO, "other")])
async def test_watch_identity_is_repository_and_ref_specific(boundary, repo, ref):
    monitor, reader, _ = boundary
    await monitor.wait_for_checks(repo_url=REPO, ref=BRANCH)
    with pytest.raises(CheckObservationError):
        await reader.observed_checks(repo_url=repo, ref=ref)


async def test_child_task_cannot_inherit_a_parent_watch(boundary):
    monitor, reader, _ = boundary
    await monitor.wait_for_checks(repo_url=REPO, ref=BRANCH)
    with pytest.raises(CheckObservationError):
        await asyncio.create_task(reader.observed_checks(repo_url=REPO, ref=BRANCH))
    assert (await reader.observed_checks(repo_url=REPO, ref=BRANCH)).commit_sha == SHA


@pytest.mark.parametrize(
    "problem",
    ["missing-sha", "null-sha", "mixed-sha", "duplicate", "pending", "unknown"],
)
async def test_incomplete_native_identity_or_terminal_set_is_unreadable(problem):
    rows = [check()]
    if problem == "missing-sha":
        del rows[0]["head_sha"]
    elif problem == "null-sha":
        rows[0]["head_sha"] = None
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
        await client.wait_for_checks(repo_url=REPO, ref=BRANCH)
        with pytest.raises(CheckObservationError):
            await client.observed_checks(repo_url=REPO, ref=BRANCH)
    finally:
        await client.close()


async def test_requested_sha_must_match_the_native_check_identity():
    client = _make_client(
        lambda _: httpx.Response(
            200, json={"total_count": 1, "check_runs": [check(sha="b" * 40)]}
        )
    )
    try:
        await client.wait_for_checks(repo_url=REPO, ref=SHA)
        with pytest.raises(CheckObservationError, match="requested SHA"):
            await client.observed_checks(repo_url=REPO, ref=SHA)
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
        await client.wait_for_checks(repo_url=REPO, ref=BRANCH)
        assert (
            await client.observed_checks(repo_url=REPO, ref=BRANCH)
        ).commit_sha == SHA
        first = False
        if later == "failure":
            with pytest.raises(ForgeAPIError):
                await client.wait_for_checks(repo_url=REPO, ref=BRANCH)
        elif later == "cancel":
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.01):
                    await client.wait_for_checks(repo_url=REPO, ref=BRANCH)
        else:
            await client.wait_for_checks(repo_url=REPO, ref=BRANCH)
        with pytest.raises(CheckObservationError):
            await client.observed_checks(repo_url=REPO, ref=BRANCH)
    finally:
        await client.close()
