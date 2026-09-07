"""Same-commit CI retries read the newly requested Actions attempt."""

import asyncio

import httpx
import pytest

from kodezart.core.protocols import CIMonitor
from kodezart.domain.errors import ForgeAPIError, TransientAPIError
from tests.adapters.test_github_api import _make_client
from tests.fakes import FakeCIMonitor

REPO = "https://github.com/example/project"
SHA = "a" * 40


class ActionsAPI:
    def __init__(self, *, run_ids=(101,), names=("lint",), fresh_failed=(), sha=SHA):
        self.sha = sha
        self.run_ids = run_ids
        self.names = names
        self.fresh_failed = set(fresh_failed)
        self.attempts = dict.fromkeys(run_ids, 1)
        self.requests = []
        self.stale_runs = 0
        self.stale_jobs = 0
        self.page_size = len(names)
        self.latest_checks = False

    def run(self, run_id, attempt):
        failed = attempt == 1 or bool(self.fresh_failed)
        return {
            "id": run_id,
            "check_suite_id": run_id + 1000,
            "head_sha": self.sha,
            "run_attempt": attempt,
            "status": "completed",
            "conclusion": "failure" if failed else "success",
        }

    def jobs(self, run_id, attempt):
        return [
            {
                "id": run_id * 1000 + attempt * 10 + index,
                "name": name,
                "run_id": run_id,
                "head_sha": self.sha,
                "status": "completed",
                "conclusion": "failure"
                if attempt == 1 or name in self.fresh_failed
                else "success",
                "check_run_url": (
                    f"https://api.github.com/repos/example/project/check-runs/"
                    f"{run_id * 1000 + attempt * 10 + index}"
                ),
            }
            for index, name in enumerate(self.names)
        ]

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST":
            run_id = int(path.split("/")[-2])
            assert path == f"/repos/example/project/actions/runs/{run_id}/rerun"
            assert request.content == b""
            self.attempts[run_id] += 1
            return httpx.Response(201)
        if path.endswith("/check-runs"):
            assert path == f"/repos/example/project/commits/{self.sha}/check-runs"
            checks = []
            for run_id in self.run_ids:
                attempt = self.attempts[run_id] if self.latest_checks else 1
                for job in self.jobs(run_id, attempt):
                    checks.append({**job, "check_suite": {"id": run_id + 1000}})
            return httpx.Response(
                200, json={"total_count": len(checks), "check_runs": checks}
            )
        if "/commits/" in path:
            return httpx.Response(200, json={"sha": self.sha})
        if path.endswith("/actions/runs"):
            assert request.url.params["head_sha"] == self.sha
            run_id = int(request.url.params["check_suite_id"]) - 1000
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "workflow_runs": [self.run(run_id, self.attempts[run_id])],
                },
            )
        run_id = int(path.split("/")[6])
        attempt = int(path.split("/")[8])
        if path.endswith("/jobs"):
            if attempt > 1 and self.stale_jobs:
                self.stale_jobs -= 1
                attempt -= 1
            jobs = self.jobs(run_id, attempt)
            page = int(request.url.params["page"])
            start = (page - 1) * self.page_size
            return httpx.Response(
                200,
                json={
                    "total_count": len(jobs),
                    "jobs": jobs[start : start + self.page_size],
                },
            )
        if attempt > 1 and self.stale_runs:
            self.stale_runs -= 1
            attempt -= 1
        return httpx.Response(200, json=self.run(run_id, attempt))

    @property
    def writes(self):
        return [request for request in self.requests if request.method == "POST"]


async def test_rerun_wait_and_names_ignore_stale_completed_checks_at_same_sha():
    server = ActionsAPI()
    server.stale_runs = 1
    server.stale_jobs = 1
    client = _make_client(server)
    assert isinstance(client, CIMonitor)
    await client.rerun_checks(repo_url=REPO, ref=SHA)
    assert await client.wait_for_checks(repo_url=REPO, ref=SHA) == (
        True,
        "All CI checks passed.",
    )
    assert await client.failed_check_names(repo_url=REPO, ref=SHA) == frozenset()
    assert len(server.writes) == 1
    assert (
        len(
            [
                request
                for request in server.requests
                if request.url.path.endswith("/check-runs")
            ]
        )
        == 1
    )
    assert (
        len(
            [
                request
                for request in server.requests
                if request.url.path.endswith("/attempts/2")
            ]
        )
        == 4
    )


async def test_branch_resolves_once_and_attempt_reads_stay_on_that_sha():
    server = ActionsAPI(fresh_failed=("lint",))
    client = _make_client(server)
    await client.rerun_checks(repo_url=REPO, ref="feature/one")
    passed, _ = await client.wait_for_checks(repo_url=REPO, ref="feature/one")
    assert passed is False
    assert await client.failed_check_names(
        repo_url=REPO, ref="feature/one"
    ) == frozenset({"lint"})
    commit_reads = [
        request
        for request in server.requests
        if "/commits/" in request.url.path
        and not request.url.path.endswith("/check-runs")
    ]
    assert len(commit_reads) == 1
    assert commit_reads[0].url.raw_path.endswith(b"/commits/feature%2Fone")


async def test_multiple_runs_are_preflighted_before_writes_and_all_reobserved():
    server = ActionsAPI(
        run_ids=(102, 101), names=("lint", "test"), fresh_failed=("test",)
    )
    server.page_size = 1
    client = _make_client(server)
    await client.rerun_checks(repo_url=REPO, ref=SHA)
    assert (await client.wait_for_checks(repo_url=REPO, ref=SHA))[0] is False
    assert await client.failed_check_names(repo_url=REPO, ref=SHA) == frozenset(
        {"test"}
    )
    assert [request.url.path for request in server.writes] == [
        f"/repos/example/project/actions/runs/{run_id}/rerun" for run_id in (101, 102)
    ]
    first_write = server.requests.index(server.writes[0])
    assert (
        len(
            [
                request
                for request in server.requests[:first_write]
                if request.url.path.endswith("/attempts/1/jobs")
            ]
        )
        == 4
    )


async def test_next_rerun_advances_from_the_preceding_completed_attempt():
    server = ActionsAPI(fresh_failed=("lint",))
    server.latest_checks = True
    client = _make_client(server)
    await client.rerun_checks(repo_url=REPO, ref=SHA)
    assert (await client.wait_for_checks(repo_url=REPO, ref=SHA))[0] is False
    server.fresh_failed.clear()
    await client.rerun_checks(repo_url=REPO, ref=SHA)
    assert (await client.wait_for_checks(repo_url=REPO, ref=SHA))[0] is True
    assert len(server.writes) == 2
    assert server.attempts == {101: 3}
    assert any(
        request.url.path.endswith("/attempts/3/jobs") for request in server.requests
    )


async def test_repeated_rerun_refuses_a_listing_older_than_its_observed_attempt():
    server = ActionsAPI(fresh_failed=("lint",))

    def handler(request):
        response = server(request)
        if request.url.path.endswith("/actions/runs"):
            return httpx.Response(
                200,
                json={"total_count": 1, "workflow_runs": [server.run(101, 1)]},
            )
        return response

    client = _make_client(handler)
    await client.rerun_checks(repo_url=REPO, ref=SHA)
    assert (await client.wait_for_checks(repo_url=REPO, ref=SHA))[0] is False
    with pytest.raises(ForgeAPIError, match="already observed"):
        await client.rerun_checks(repo_url=REPO, ref=SHA)
    assert len(server.writes) == 1
    assert server.attempts == {101: 2}


async def test_supplied_commit_sha_cannot_resolve_to_another_commit():
    server = ActionsAPI()
    client = _make_client(server)
    with pytest.raises(ForgeAPIError, match="requested SHA"):
        await client.rerun_checks(repo_url=REPO, ref="b" * 40)
    assert server.writes == []


@pytest.mark.parametrize("kind", ["old_attempt", "old_jobs", "not_visible", "pending"])
async def test_missing_fresh_attempt_exhausts_poll_bound_as_error(kind):
    server = ActionsAPI()
    server.stale_runs = 100 if kind == "old_attempt" else 0
    server.stale_jobs = 100 if kind == "old_jobs" else 0

    def handler(request):
        response = server(request)
        if request.url.path.endswith("/attempts/2"):
            if kind == "not_visible":
                return httpx.Response(404)
            if kind == "pending":
                return httpx.Response(
                    200,
                    json={
                        **response.json(),
                        "status": "in_progress",
                        "conclusion": None,
                    },
                )
        return response

    client = _make_client(handler, ci_poll_max_attempts=2)
    await client.rerun_checks(repo_url=REPO, ref=SHA)
    with pytest.raises(TransientAPIError, match="poll bound"):
        await client.wait_for_checks(repo_url=REPO, ref=SHA)
    with pytest.raises(ForgeAPIError):
        await client.failed_check_names(repo_url=REPO, ref=SHA)
    assert len(server.writes) == 1
    assert (
        len(
            [
                request
                for request in server.requests
                if request.url.path.endswith("/attempts/2")
            ]
        )
        == 3
    )


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "empty",
        "partial",
        "duplicate_check",
        "check_sha",
        "check_suite",
        "unmapped",
        "ambiguous",
        "run_sha",
        "run_suite",
        "run_pending",
        "job_link",
        "job_host",
    ],
)
async def test_invalid_initial_mapping_refuses_before_any_write(kind):
    server = ActionsAPI(run_ids=(101, 102))

    def handler(request):
        response = server(request)
        data = response.json()
        if request.url.path.endswith("/check-runs"):
            if kind == "missing":
                return httpx.Response(404)
            if kind == "empty":
                data = {"total_count": 0, "check_runs": []}
            if kind == "partial":
                data["total_count"] += 1
            if kind == "duplicate_check":
                data["check_runs"][1]["id"] = data["check_runs"][0]["id"]
            if kind == "check_sha":
                data["check_runs"][0]["head_sha"] = "b" * 40
            if kind == "check_suite":
                del data["check_runs"][0]["check_suite"]
        if (
            request.url.path.endswith("/actions/runs")
            and request.url.params["check_suite_id"] == "1102"
        ):
            if kind == "unmapped":
                data = {"total_count": 0, "workflow_runs": []}
            if kind == "ambiguous":
                data["total_count"] = 2
            if kind == "run_sha":
                data["workflow_runs"][0]["head_sha"] = "b" * 40
            if kind == "run_suite":
                data["workflow_runs"][0]["check_suite_id"] = 1101
            if kind == "run_pending":
                data["workflow_runs"][0]["status"] = "queued"
        if request.url.path.endswith("/attempts/1/jobs") and kind == "job_link":
            data["jobs"][0]["check_run_url"] = (
                "https://api.github.com/repos/other/repo/check-runs/1"
            )
        if request.url.path.endswith("/attempts/1/jobs") and kind == "job_host":
            data["jobs"][0]["check_run_url"] = data["jobs"][0]["check_run_url"].replace(
                "api.github.com", "other.example"
            )
        return httpx.Response(response.status_code, json=data)

    client = _make_client(handler, ci_check_runs_max_pages=1)
    with pytest.raises(ForgeAPIError):
        await client.rerun_checks(repo_url=REPO, ref=SHA)
    assert server.writes == []


@pytest.mark.parametrize(
    "kind",
    [
        "wrong_sha",
        "wrong_run",
        "wrong_suite",
        "wrong_attempt",
        "unknown_status",
        "unknown_conclusion",
        "malformed",
        "forbidden",
    ],
)
async def test_invalid_attempt_metadata_cannot_establish_a_flake(kind):
    server = ActionsAPI()

    def handler(request):
        response = server(request)
        if not request.url.path.endswith("/attempts/2"):
            return response
        if kind == "forbidden":
            return httpx.Response(403)
        data = response.json()
        if kind == "wrong_sha":
            data["head_sha"] = "b" * 40
        if kind == "wrong_run":
            data["id"] = 999
        if kind == "wrong_suite":
            data["check_suite_id"] = 999
        if kind == "wrong_attempt":
            data["run_attempt"] = 3
        if kind == "unknown_status":
            data["status"] = "future_state"
        if kind == "unknown_conclusion":
            data["conclusion"] = "future_conclusion"
        if kind == "malformed":
            del data["run_attempt"]
        return httpx.Response(200, json=data)

    client = _make_client(handler)
    await client.rerun_checks(repo_url=REPO, ref=SHA)
    with pytest.raises(ForgeAPIError):
        await client.wait_for_checks(repo_url=REPO, ref=SHA)


@pytest.mark.parametrize(
    "kind",
    [
        "wrong_sha",
        "wrong_run",
        "empty",
        "unknown",
        "disagree",
        "duplicate",
        "short",
        "changing",
        "over_bound",
    ],
)
async def test_incomplete_or_inconsistent_attempt_jobs_cannot_establish_a_flake(kind):
    server = ActionsAPI(names=("lint", "test"))
    server.page_size = 1

    def handler(request):
        response = server(request)
        if not request.url.path.endswith("/attempts/2/jobs"):
            return response
        data = response.json()
        page = int(request.url.params["page"])
        if kind == "wrong_sha":
            data["jobs"][0]["head_sha"] = "b" * 40
        if kind == "wrong_run":
            data["jobs"][0]["run_id"] = 999
        if kind == "empty":
            data = {"total_count": 0, "jobs": []}
        if kind == "unknown":
            data["jobs"][0]["conclusion"] = "future_conclusion"
        if kind == "disagree":
            data["jobs"][0]["conclusion"] = "failure"
        if kind == "duplicate":
            data["jobs"][0]["id"] = 101020
        if kind == "short":
            data["jobs"] = []
        if kind == "changing":
            data["total_count"] = 3 if page == 1 else 2
        if kind == "over_bound":
            data["total_count"] = 3
        return httpx.Response(200, json=data)

    client = _make_client(handler, ci_check_runs_max_pages=2)
    await client.rerun_checks(repo_url=REPO, ref=SHA)
    with pytest.raises(ForgeAPIError):
        await client.wait_for_checks(repo_url=REPO, ref=SHA)


@pytest.mark.parametrize(
    "kind", ["forbidden", "transport", "server", "unexpected_success"]
)
async def test_failed_or_uncertain_post_never_retries_or_falls_back_to_old_checks(kind):
    server = ActionsAPI(run_ids=(101, 102))

    def handler(request):
        if request.method == "POST" and request.url.path.endswith("/102/rerun"):
            server.requests.append(request)
            if kind == "transport":
                raise httpx.ReadError("lost response")
            return httpx.Response(
                {"forbidden": 403, "server": 503, "unexpected_success": 200}[kind]
            )
        return server(request)

    client = _make_client(handler, max_retries=3, retry_backoff_factor=0)
    with pytest.raises((ForgeAPIError, TransientAPIError)):
        await client.rerun_checks(repo_url=REPO, ref=SHA)
    assert len(server.writes) == 2
    with pytest.raises(ForgeAPIError, match="incomplete"):
        await client.wait_for_checks(repo_url=REPO, ref=SHA)
    with pytest.raises(ForgeAPIError, match="incomplete"):
        await client.failed_check_names(repo_url=REPO, ref=SHA)
    assert len(server.writes) == 2


async def test_fake_advances_observation_only_when_rerun_is_requested():
    fake = FakeCIMonitor(
        passed=False,
        failed_names=frozenset({"lint"}),
        rerun_results=[(True, "fresh", frozenset())],
    )
    assert isinstance(fake, CIMonitor)
    assert (await fake.wait_for_checks(repo_url=REPO, ref=SHA))[0] is False
    await fake.rerun_checks(repo_url=REPO, ref=SHA)
    assert await fake.wait_for_checks(repo_url=REPO, ref=SHA) == (True, "fresh")
    assert await fake.failed_check_names(repo_url=REPO, ref=SHA) == frozenset()
    assert fake.rerun_calls == [(REPO, SHA)]


async def test_enterprise_api_prefix_and_authority_survive_check_job_mapping():
    server = ActionsAPI()
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "forge.example"
        assert request.url.path.startswith("/api/v3/")
        relative = request.url.copy_with(path=request.url.path.removeprefix("/api/v3"))
        response = server(httpx.Request(request.method, relative))
        if request.url.path.endswith("/jobs"):
            data = response.json()
            for job in data["jobs"]:
                job["check_run_url"] = job["check_run_url"].replace(
                    "https://api.github.com", "https://forge.example/api/v3"
                )
            return httpx.Response(200, json=data)
        return response

    client = _make_client(handler)
    client._client.base_url = "https://forge.example/api/v3"
    await client.rerun_checks(repo_url="https://forge.example/example/project", ref=SHA)
    assert (
        await client.wait_for_checks(
            repo_url="https://forge.example/example/project", ref=SHA
        )
    )[0] is True
    assert any(request.method == "POST" for request in requests)


@pytest.mark.parametrize("refs", [(SHA, SHA), ("feature/one", "feature/two")])
async def test_parallel_alias_dispatches_refuse_stale_shared_baselines(refs):
    server = ActionsAPI(fresh_failed=("lint",))
    barrier = asyncio.Barrier(2)

    async def handler(request):
        if "/commits/" in request.url.path and not request.url.path.endswith(
            "/check-runs"
        ):
            await barrier.wait()
        response = server(request)
        if request.url.path.endswith("/actions/runs"):
            response = httpx.Response(
                200, json={"total_count": 1, "workflow_runs": [server.run(101, 1)]}
            )
        await asyncio.sleep(0)
        return response

    client = _make_client(handler)

    async def sequence(ref):
        try:
            await client.rerun_checks(repo_url=REPO, ref=ref)
        except ForgeAPIError as exc:
            assert "already observed" in str(exc)
            return "refused"
        assert (await client.wait_for_checks(repo_url=REPO, ref=ref))[0] is False
        return "observed"

    results = await asyncio.gather(*(sequence(ref) for ref in refs))
    assert sorted(results) == ["observed", "refused"]
    assert len(server.writes) == 1
    assert server.attempts == {101: 2}


async def test_parallel_tasks_keep_their_own_attempt_when_later_rerun_completes():
    server = ActionsAPI(fresh_failed=("lint",))
    server.latest_checks = True
    first_requested = asyncio.Event()
    second_requested = asyncio.Event()

    def handler(request):
        response = server(request)
        if "/attempts/3" in request.url.path:
            data = response.json()
            if request.url.path.endswith("/jobs"):
                for job in data["jobs"]:
                    job["conclusion"] = "success"
            else:
                data["conclusion"] = "success"
            return httpx.Response(200, json=data)
        return response

    client = _make_client(handler)

    async def first_sequence():
        await client.rerun_checks(repo_url=REPO, ref=SHA)
        first_requested.set()
        await second_requested.wait()
        passed, _ = await client.wait_for_checks(repo_url=REPO, ref=SHA)
        names = await client.failed_check_names(repo_url=REPO, ref=SHA)
        return passed, names

    async def second_sequence():
        await first_requested.wait()
        await client.rerun_checks(repo_url=REPO, ref=SHA)
        second_requested.set()
        passed, _ = await client.wait_for_checks(repo_url=REPO, ref=SHA)
        names = await client.failed_check_names(repo_url=REPO, ref=SHA)
        return passed, names

    results = await asyncio.gather(first_sequence(), second_sequence())
    assert results == [(False, frozenset({"lint"})), (True, frozenset())]
    assert len(server.writes) == 2
    assert server.attempts == {101: 3}


async def test_child_task_does_not_inherit_its_parents_attempt_observation():
    server = ActionsAPI()
    client = _make_client(server)
    await client.rerun_checks(repo_url=REPO, ref=SHA)
    assert (await client.wait_for_checks(repo_url=REPO, ref=SHA))[0] is True
    result = await asyncio.create_task(client.wait_for_checks(repo_url=REPO, ref=SHA))
    assert result[0] is False


async def test_distinct_shas_can_dispatch_before_either_completes():
    servers = {SHA: ActionsAPI(), "b" * 40: ActionsAPI(run_ids=(202,), sha="b" * 40)}
    first_post = asyncio.Event()
    release_post = asyncio.Event()

    async def handler(request):
        path = request.url.path
        if "/commits/" in path:
            sha = path.split("/")[5]
        elif path.endswith("/actions/runs"):
            sha = request.url.params["head_sha"]
        else:
            sha = SHA if "/101/" in path else "b" * 40
        response = servers[sha](request)
        if request.method == "POST" and sha == SHA:
            first_post.set()
            await release_post.wait()
        return response

    client = _make_client(handler)

    async def sequence(sha):
        await client.rerun_checks(repo_url=REPO, ref=sha)
        return (await client.wait_for_checks(repo_url=REPO, ref=sha))[0]

    first = asyncio.create_task(sequence(SHA))
    try:
        async with asyncio.timeout(2):
            await first_post.wait()
            assert await sequence("b" * 40) is True
    finally:
        release_post.set()
        assert await first is True
    assert all(len(server.writes) == 1 for server in servers.values())


async def test_fake_preserves_each_tasks_requested_attempt():
    fake = FakeCIMonitor(
        passed=False,
        failed_names=frozenset({"original"}),
        rerun_results=[
            (False, "second", frozenset({"second"})),
            (True, "third", frozenset()),
        ],
    )
    first_requested = asyncio.Event()
    second_requested = asyncio.Event()

    async def first_sequence():
        await fake.rerun_checks(repo_url=REPO, ref=SHA)
        first_requested.set()
        await second_requested.wait()
        return await fake.wait_for_checks(
            repo_url=REPO, ref=SHA
        ), await fake.failed_check_names(repo_url=REPO, ref=SHA)

    async def second_sequence():
        await first_requested.wait()
        await fake.rerun_checks(repo_url=REPO, ref=SHA)
        second_requested.set()
        return await fake.wait_for_checks(
            repo_url=REPO, ref=SHA
        ), await fake.failed_check_names(repo_url=REPO, ref=SHA)

    assert await asyncio.gather(first_sequence(), second_sequence()) == [
        ((False, "second"), frozenset({"second"})),
        ((True, "third"), frozenset()),
    ]
