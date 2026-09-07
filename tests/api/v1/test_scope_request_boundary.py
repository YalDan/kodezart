"""Scope input crosses both HTTP paths as domain data before enqueueing."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient, Response

from kodezart.adapters.asyncio_job_queue import AsyncioJobQueue
from kodezart.api.v1.endpoints.agent import router
from kodezart.main import create_app
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import AgentEvent, AssistantTextEvent
from kodezart.types.domain.branch import BaseSpec
from kodezart.types.domain.job import JobRecord
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.workflow import WorkflowSubmission
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeChangePersister,
    FakeWorkspaceProvider,
)

ROUTES = ("/api/v1/agent/fire", "/api/v1/agent/workflow")
SETTLE_SECONDS = 5.0


def test_scope_input_uses_only_the_existing_agent_routes() -> None:
    assert {route.path for route in router.routes} == {"/query", "/workflow", "/fire"}


async def test_query_does_not_acquire_the_workflow_scope_field() -> None:
    async with scope_app() as (client, queue, engine):
        response = await client.post(
            "/api/v1/agent/query",
            json={
                "prompt": "query",
                "repoPath": "/tmp/fixture",
                "scope": {"kind": "project", "key": "project-key"},
            },
        )
        assert response.status_code == 422
        assert queue.submissions == []
        assert engine.scopes == []


class RecordingEngine:
    def __init__(self) -> None:
        self.scopes: list[ScopeRef | None] = []
        self.bases: list[BaseSpec] = []
        self.finished = asyncio.Event()

    async def run(
        self,
        *,
        prompt: str,
        repo_path: str | None,
        repo_url: str | None,
        base_spec: BaseSpec,
        implied_base: BaseSpec | None = None,
        scope: ScopeRef | None,
        permission_mode: str,
        allowed_tools: list[str],
        cache_key: str,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.scopes.append(scope)
        self.bases.append(base_spec)
        yield AssistantTextEvent(text="scope observed", model="fixture")
        self.finished.set()


class RecordingQueue(AsyncioJobQueue):
    def __init__(self, engine: RecordingEngine) -> None:
        super().__init__(
            engine=engine,
            max_concurrent_runs_per_lane=1,
            max_depth_per_lane=64,
            terminal_retention_seconds=86400.0,
            event_buffer_retention_seconds=900.0,
            event_buffer_capacity=512,
        )
        self.submissions: list[WorkflowSubmission] = []

    async def submit(self, *, lane: str, request: WorkflowSubmission) -> JobRecord:
        self.submissions.append(request)
        return await super().submit(lane=lane, request=request)


@asynccontextmanager
async def scope_app() -> AsyncGenerator[
    tuple[AsyncClient, RecordingQueue, RecordingEngine], None
]:
    app = create_app()
    app.state.skills = SUPPRESS_ALL_SKILLS
    app.state.agent_service = AgentService(
        git_base_url="https://github.com",
        executor=FakeAgentExecutor(events=[]),
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    engine = RecordingEngine()
    queue = RecordingQueue(engine)
    app.state.job_queue = queue
    await queue.start()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, queue, engine
    finally:
        await queue.stop()


def assert_accepted(response: Response, route: str) -> None:
    assert response.status_code == (202 if route.endswith("/fire") else 200)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("kind", list(ScopeKind))
async def test_scope_is_domain_typed_before_queue_and_reaches_engine(
    route: str, kind: ScopeKind
) -> None:
    async with scope_app() as (client, queue, engine):
        response = await client.post(
            route,
            json={
                "prompt": "implement scope",
                "repoUrl": "https://github.com/example/repository.git",
                "baseBranch": "feature/base",
                "scope": {"kind": kind.value, "key": "opaque-address"},
            },
        )
        assert_accepted(response, route)
        await asyncio.wait_for(engine.finished.wait(), timeout=SETTLE_SECONDS)
        (submission,) = queue.submissions
        assert type(submission) is WorkflowSubmission
        assert type(submission.scope) is ScopeRef
        assert submission.scope == ScopeRef(kind=kind, key="opaque-address")
        assert engine.scopes == [submission.scope]
        assert engine.scopes[0] is submission.scope
        assert engine.bases[0].base_branch == "feature/base"


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("explicit_null", [False, True])
async def test_absent_and_null_scope_preserve_unscoped_submission(
    route: str, explicit_null: bool
) -> None:
    body: dict[str, object] = {"prompt": "existing run", "repoPath": "/tmp/fixture"}
    if explicit_null:
        body["scope"] = None
    async with scope_app() as (client, queue, engine):
        response = await client.post(route, json=body)
        assert_accepted(response, route)
        await asyncio.wait_for(engine.finished.wait(), timeout=SETTLE_SECONDS)
        assert queue.submissions[0].scope is None
        assert engine.scopes == [None]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "scope",
    [
        {"kind": "project", "key": ""},
        {"kind": "cycle", "key": "address"},
        {"kind": "project"},
        {"key": "address"},
        {"kind": "project", "key": "address", "vendor": "linear"},
        "project:address",
    ],
)
async def test_invalid_scope_is_refused_before_queue(route: str, scope: object) -> None:
    async with scope_app() as (client, queue, engine):
        response = await client.post(
            route, json={"prompt": "run", "repoPath": "/tmp/fixture", "scope": scope}
        )
        assert response.status_code == 422
        assert queue.submissions == []
        assert engine.scopes == []


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "repo",
    [{}, {"repoPath": "/tmp/fixture", "repoUrl": "https://github.com/example/repo"}],
)
async def test_scoped_request_still_requires_exactly_one_repository(
    route: str, repo: dict[str, str]
) -> None:
    async with scope_app() as (client, queue, engine):
        response = await client.post(
            route,
            json={
                "prompt": "run",
                "scope": {"kind": "issue", "key": "FIX-1"},
                **repo,
            },
        )
        assert response.status_code == 422
        assert queue.submissions == []
        assert engine.scopes == []


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("fallback", ["must-not-replace-recorded", ""])
async def test_recorded_base_and_implied_base_survive_scope_conversion(
    route: str,
    fallback: str,
) -> None:
    recorded = {"inputs": [], "baseBranch": "recorded-base"}
    implied = {"inputs": [], "baseBranch": "implied-base"}
    async with scope_app() as (client, queue, engine):
        response = await client.post(
            route,
            json={
                "prompt": "run",
                "repoPath": "/tmp/fixture",
                "scope": {"kind": "project", "key": "project-key"},
                "baseBranch": fallback,
                "baseSpec": recorded,
                "impliedBase": implied,
            },
        )
        assert_accepted(response, route)
        await asyncio.wait_for(engine.finished.wait(), timeout=SETTLE_SECONDS)
        (submission,) = queue.submissions
        assert submission.base_spec == BaseSpec.model_validate(recorded)
        assert submission.implied_base == BaseSpec.model_validate(implied)
        assert engine.bases == [submission.base_spec]


@pytest.mark.parametrize("route", ROUTES)
async def test_empty_fallback_base_is_refused_before_queue(route: str) -> None:
    async with scope_app() as (client, queue, engine):
        response = await client.post(
            route,
            json={"prompt": "run", "repoPath": "/tmp/fixture", "baseBranch": ""},
        )
        assert response.status_code == 422
        assert queue.submissions == []
        assert engine.scopes == []
