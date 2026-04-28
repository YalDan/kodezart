"""Shared async test fixtures — no mocking, full chain exercised."""

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from kodezart.main import create_app
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import AssistantTextEvent, ResultEvent
from tests.fakes import FakeAgentExecutor, FakeWorkspaceProvider


@pytest.fixture(scope="session", autouse=True)
def _git_test_identity() -> None:
    """Provide a git identity to subprocess git commands invoked by tests.

    Tests shell out to `git commit` in tmp repos; CI runners have no global
    git config, so without this they fail with "Author identity unknown".
    """
    os.environ.setdefault("GIT_AUTHOR_NAME", "kodezart-test")
    os.environ.setdefault("GIT_AUTHOR_EMAIL", "test@kodezart-test.invalid")
    os.environ.setdefault("GIT_COMMITTER_NAME", "kodezart-test")
    os.environ.setdefault("GIT_COMMITTER_EMAIL", "test@kodezart-test.invalid")


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    marker_expr = config.getoption("-m", default="")
    if "live" in marker_expr:
        return
    reason = "live tests need Claude CLI (run with: pytest -m live)"
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
async def client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
async def agent_client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    app.state.agent_service = AgentService(
        executor=FakeAgentExecutor(
            events=[
                AssistantTextEvent(text="analysis complete", model="test-model"),
                ResultEvent(
                    subtype="result",
                    duration_ms=100,
                    duration_api_ms=80,
                    is_error=False,
                    num_turns=1,
                    session_id="test-session",
                ),
            ]
        ),
        workspace=FakeWorkspaceProvider(),
        persister=None,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
