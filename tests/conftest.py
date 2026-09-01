"""Shared async test fixtures — no mocking, full chain exercised."""

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from kodezart.adapters.git_branch_merger import GitBranchMerger
from kodezart.adapters.subprocess_git_service import SubprocessGitService
from kodezart.core.config import AppConfig
from kodezart.main import create_app
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import AssistantTextEvent, ResultEvent
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeWorkspaceProvider,
)

# The suite is HERMETIC: it never reads the developer's environment or a
# working-directory .env. Either one carrying a real deployment turned
# green runs red — a configured operator could not run the gate beside
# their own service, which is the opposite of the fresh-environment
# requirement (KOD-168). Applied at import, before any test constructs a
# config; a test that needs a value sets it itself, and the env-file
# behaviour tests pass their own file explicitly.
for _ambient in [name for name in os.environ if name.startswith("KODEZART_")]:
    del os.environ[_ambient]
AppConfig.model_config["env_file"] = None


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


_GATED_MARKERS: dict[str, str] = {
    "live": "live tests need Claude CLI (run with: pytest -m live)",
    "postgres": (
        "postgres tests need a database at KODEZART_TEST_POSTGRES_URL "
        "(run with: pytest -m postgres)"
    ),
}


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    marker_expr = config.getoption("-m", default="")
    for marker, reason in _GATED_MARKERS.items():
        if marker in marker_expr:
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
async def client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def subprocess_git_service() -> SubprocessGitService:
    """Shared SubprocessGitService configured with the default ``origin`` remote.

    Absorbs the explicit ``remote="origin"`` kwarg sites the inherited
    19aca0d sprinkled across the adapter test files; collapses the
    repeat-construction pattern.  Tests that need a non-default remote
    still construct directly with a different remote name.
    """
    return SubprocessGitService(remote="origin")


@pytest.fixture
def git_branch_merger(
    subprocess_git_service: SubprocessGitService,
) -> GitBranchMerger:
    """Shared GitBranchMerger constructed atop the shared git fixture.

    Uses ``FakeWorkspaceProvider`` so the fixture is usable from tests
    that exercise the four-status decision tree against fakes without
    spinning up real bare clones.  Tests that need a real workspace
    provider (e.g. the new REAL-bare-clone integration test) construct
    a ``GitBranchMerger`` directly.
    """
    return GitBranchMerger(
        git=subprocess_git_service,
        workspace=FakeWorkspaceProvider(),
        remote="origin",
    )


@pytest.fixture
async def agent_client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    app.state.skills = SUPPRESS_ALL_SKILLS
    app.state.agent_service = AgentService(
        git_base_url="https://github.com",
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
