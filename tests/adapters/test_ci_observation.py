"""CI facts are read from complete structured responses, never summaries."""

import httpx
import pytest

from kodezart.domain.errors import ForgeAPIError
from tests.adapters.test_github_api import _make_client

REPO = "https://github.com/example/project"


@pytest.mark.parametrize("active", [True, False])
async def test_declaration_walks_to_second_page(active):
    pages = []

    def handler(request):
        page = int(request.url.params["page"])
        pages.append(page)
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "workflows": [
                    {
                        "id": page,
                        "state": "active"
                        if page == 2 and active
                        else "disabled_manually",
                    }
                ],
            },
        )

    client = _make_client(handler)
    assert await client.checks_declared(repo_url=REPO) is active
    assert pages == [1, 2]


@pytest.mark.parametrize(
    "kind", ["duplicate", "short", "growing", "malformed", "forbidden"]
)
async def test_unreadable_declaration_is_never_absence(kind):
    def handler(request):
        page = int(request.url.params["page"])
        if kind == "forbidden":
            return httpx.Response(403)
        if kind == "malformed":
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={
                "total_count": 10 if kind != "growing" else page + 1,
                "workflows": []
                if kind == "short"
                else [
                    {
                        "id": 1 if kind == "duplicate" else page,
                        "state": "disabled_manually",
                    }
                ],
            },
        )

    client = _make_client(handler, ci_check_runs_max_pages=2)
    with pytest.raises(ForgeAPIError):
        await client.checks_declared(repo_url=REPO)


async def test_failed_names_walks_all_pages_and_ignores_output_prose():
    def handler(request):
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "check_runs": [
                    {
                        "id": page,
                        "name": f"check-{page}",
                        "status": "completed",
                        "conclusion": "failure" if page == 2 else "success",
                        "output": {"summary": "" if page == 2 else "all tests failed"},
                    }
                ],
            },
        )

    client = _make_client(handler)
    assert await client.failed_check_names(repo_url=REPO, ref="sha") == frozenset(
        {"check-2"}
    )


@pytest.mark.parametrize(
    "kind", ["unknown", "pending", "duplicate", "short", "missing"]
)
async def test_failed_names_refuses_incomplete_or_ambiguous_observation(kind):
    def handler(request):
        if kind == "missing":
            return httpx.Response(404)
        runs = [
            {"id": 1, "name": "first", "status": "completed", "conclusion": "failure"}
        ]
        if kind != "short":
            runs.append(
                {
                    "id": 1 if kind == "duplicate" else 2,
                    "name": "second",
                    "status": "in_progress" if kind == "pending" else "completed",
                    "conclusion": "future_result" if kind == "unknown" else "success",
                }
            )
        return httpx.Response(200, json={"total_count": 2, "check_runs": runs})

    client = _make_client(handler, ci_check_runs_max_pages=1)
    with pytest.raises(ForgeAPIError):
        await client.failed_check_names(repo_url=REPO, ref="sha")


@pytest.mark.parametrize("kind", ["unknown_state", "shrinking_total"])
async def test_declaration_refuses_unsupported_absence_evidence(kind):
    def handler(request):
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={
                "total_count": 1 if kind == "unknown_state" else 4 - page,
                "workflows": [
                    {
                        "id": page,
                        "state": "future_state"
                        if kind == "unknown_state"
                        else "disabled_manually",
                    }
                ],
            },
        )

    client = _make_client(handler)
    with pytest.raises(ForgeAPIError):
        await client.checks_declared(repo_url=REPO)


async def test_failed_names_refuses_shrinking_total():
    def handler(request):
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={
                "total_count": 4 - page,
                "check_runs": [
                    {
                        "id": page,
                        "name": "test",
                        "status": "completed",
                        "conclusion": "failure",
                    }
                ],
            },
        )

    client = _make_client(handler)
    with pytest.raises(ForgeAPIError):
        await client.failed_check_names(repo_url=REPO, ref="sha")
