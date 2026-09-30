"""The forge's read side: check-before-create, and where to look at a branch.

Every case here drives the adapter over a mock transport and asserts the
REQUEST it made as well as the answer it gave: an implementation that paged
every open pull request and matched heads itself would return the same value
on a one-entry fixture, and the point of this port is that the forge's own
filter answers the question.
"""

import httpx
import pytest

from kodezart.composition.forge import forge_query_for_origin
from kodezart.core.protocols import ForgeQuery
from kodezart.domain.errors import ForgeAPIError
from tests.adapters.test_github_api import _make_client
from tests.fakes import FakeForgeQuery

REPO = "https://github.com/example/project"
HEAD = "kodezart/topic"
OPEN_PR = {
    "number": 42,
    "title": "feat: a delivery",
    "body": "delivers FIX-1",
    "html_url": f"{REPO}/pull/42",
}


def _listing(*entries: dict[str, object]):
    calls: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.url.query.decode()))
        return httpx.Response(200, json=list(entries))

    return handler, calls


class TestTheOpenPullRequestForAHead:
    """The check-before-create question, asked of the forge's own filter."""

    async def test_an_open_pull_request_on_the_head_is_returned(self) -> None:
        handler, calls = _listing(OPEN_PR)
        client = _make_client(handler)
        assert isinstance(client, ForgeQuery)

        try:
            found = await client.open_pr_for_head(repo_url=REPO, head=HEAD)
        finally:
            await client.close()

        assert found == (f"{REPO}/pull/42", 42)
        assert calls == [
            (
                "GET",
                "/repos/example/project/pulls",
                "state=open&head=example%3Akodezart%2Ftopic&per_page=100",
            ),
        ]

    async def test_a_head_with_nothing_open_on_it_answers_nothing(self) -> None:
        """An empty listing is an ANSWER, and never an error."""
        handler, calls = _listing()
        client = _make_client(handler)

        try:
            assert await client.open_pr_for_head(repo_url=REPO, head=HEAD) is None
        finally:
            await client.close()

        assert [call[1] for call in calls] == ["/repos/example/project/pulls"]

    async def test_the_head_the_caller_names_is_the_head_asked_about(self) -> None:
        """The filter carries the caller's branch, owner-qualified."""
        handler, calls = _listing(OPEN_PR)
        client = _make_client(handler)

        try:
            await client.open_pr_for_head(repo_url=REPO, head="release-1.2")
        finally:
            await client.close()

        assert "head=example%3Arelease-1.2" in calls[0][2]

    async def test_two_open_pull_requests_on_one_head_are_refused(self) -> None:
        """The forge contradicting the question is not something to pick from."""
        handler, _ = _listing(OPEN_PR, {**OPEN_PR, "number": 43})
        client = _make_client(handler)

        try:
            with pytest.raises(ForgeAPIError, match="2 open pull requests"):
                await client.open_pr_for_head(repo_url=REPO, head=HEAD)
        finally:
            await client.close()

    async def test_a_read_that_could_not_be_made_raises(self) -> None:
        """ "Nobody opened one" is never what an unavailable forge means."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "forbidden"})

        client = _make_client(handler)

        try:
            with pytest.raises(ForgeAPIError):
                await client.open_pr_for_head(repo_url=REPO, head=HEAD)
        finally:
            await client.close()


class TestTheBranchWebUrl:
    """Composed by the adapter, from the origin's own owner and repository."""

    @pytest.mark.parametrize(
        "repo_url,branch,expected",
        [
            (REPO, "main", "https://github.com/example/project/tree/main"),
            (
                f"{REPO}.git",
                HEAD,
                "https://github.com/example/project/tree/kodezart/topic",
            ),
            (
                "https://github.example.invalid/acme/tools.git",
                "release 1.2",
                "https://github.example.invalid/acme/tools/tree/release%201.2",
            ),
        ],
    )
    async def test_the_page_is_composed_from_the_origin(
        self, repo_url: str, branch: str, expected: str
    ) -> None:
        client = _make_client(lambda _request: httpx.Response(200, json=[]))
        try:
            assert client.branch_web_url(repo_url=repo_url, branch=branch) == expected
        finally:
            await client.close()

    async def test_composing_asks_the_forge_nothing(self) -> None:
        """An address is not an observation: no request is made for one."""
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.path)
            return httpx.Response(200, json=[])

        client = _make_client(handler)
        try:
            client.branch_web_url(repo_url=REPO, branch="main")
        finally:
            await client.close()

        assert requests == []

    @pytest.mark.parametrize(
        "repo_url", ["file:///srv/bare/project.git", "example/project"]
    )
    async def test_an_origin_with_no_web_host_is_refused(self, repo_url: str) -> None:
        """A shorthand and a bare clone name no page, and none is invented."""
        client = _make_client(lambda _request: httpx.Response(200, json=[]))
        try:
            with pytest.raises(ValueError, match=r"branch page|owner/repo"):
                client.branch_web_url(repo_url=repo_url, branch="main")
        finally:
            await client.close()


class TestTheDouble:
    """The fake answers what the adapter answers, and it is a ``ForgeQuery``."""

    def test_the_double_satisfies_the_port(self) -> None:
        assert isinstance(FakeForgeQuery(), ForgeQuery)

    async def test_the_double_answers_a_seeded_head_and_nothing_else(self) -> None:
        double = FakeForgeQuery(open_prs={(REPO, HEAD): (f"{REPO}/pull/42", 42)})

        assert await double.open_pr_for_head(repo_url=REPO, head=HEAD) == (
            f"{REPO}/pull/42",
            42,
        )
        assert await double.open_pr_for_head(repo_url=REPO, head="other") is None
        assert double.lookups == [(REPO, HEAD), (REPO, "other")]

    async def test_a_failing_read_on_the_double_raises_like_the_adapter(
        self,
    ) -> None:
        double = FakeForgeQuery(
            fail_lookup=ForgeAPIError(
                "forbidden", status_code=403, detail="GET /repos/example/project/pulls"
            ),
        )

        with pytest.raises(ForgeAPIError):
            await double.open_pr_for_head(repo_url=REPO, head=HEAD)

    @pytest.mark.parametrize(
        "repo_url,branch",
        [
            (REPO, "main"),
            (f"{REPO}.git", HEAD),
            ("https://github.example.invalid/acme/tools.git", "release 1.2"),
        ],
    )
    async def test_the_double_composes_the_same_page_as_the_adapter(
        self, repo_url: str, branch: str
    ) -> None:
        """A double that drifted here would send consumers to another page."""
        client = _make_client(lambda _request: httpx.Response(200, json=[]))
        try:
            expected = client.branch_web_url(repo_url=repo_url, branch=branch)
        finally:
            await client.close()

        assert (
            FakeForgeQuery().branch_web_url(repo_url=repo_url, branch=branch)
            == expected
        )


class TestSelectionByOrigin:
    """Chosen by origin, like every other forge capability (KOD-148).

    The per-origin rule and its peers are stated in
    ``tests/test_forge_origin_selection.py``; this pins the read side's own
    selection beside the read side itself.
    """

    async def test_a_forge_less_origin_selects_nothing(self) -> None:
        """A bare local origin has no pull requests and no pages."""
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.path)
            return httpx.Response(200, json=[])

        client = _make_client(handler)
        try:
            assert (
                forge_query_for_origin(
                    client=client, repo_url="file:///srv/bare/project.git"
                )
                is None
            )
            assert forge_query_for_origin(client=None, repo_url=REPO) is None
            assert forge_query_for_origin(client=client, repo_url=REPO) is client
        finally:
            await client.close()

        assert requests == []
