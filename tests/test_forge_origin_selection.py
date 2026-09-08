"""Forge capability is chosen by ORIGIN, never by client presence (KOD-148).

The measured failure: a fire ran a hundred minutes, did the entire job
correctly, produced its pull-request title and body — and then died on the
literal last act, because the composition root had handed it the forge
adapter for a ``file://`` origin that has no pull requests to open.  The
adapter's first act is to parse an owner and a repository out of the URL,
so it raised after all the expensive work was done.

The dispatch tick's delivery probe was repaired the same day under
KOD-145, per origin, at the composition root.  These tests generalise that
shape to the rest of the forge surface and pin the generalisation: the set
of capabilities the forge adapter answers is closed, and every member of
it is chosen by the same predicate.
"""

import ast
import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import httpx
import pytest

from kodezart.adapters.github_api import GitHubAPIClient
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.composition.engine import (
    OriginRoutedWorkflowEngine,
    build_workflow_engine,
)
from kodezart.composition.forge import (
    build_forge_client,
    ci_observation_reader_for_origin,
    forge_query_for_origin,
    pr_content_editor_for_origin,
    pr_state_reader_for_origin,
)
from kodezart.composition.jobs import build_job_queue
from kodezart.core import protocols
from kodezart.core.config import AppConfig
from kodezart.core.constants import DEFAULT_LANE
from kodezart.core.protocols import (
    CIMonitor,
    CIObservationReader,
    DeliveryProbe,
    ForgeQuery,
    PRContentEditor,
    PRCreator,
    PRStateReader,
    RepoVisibilityResolver,
    WorkflowEngine,
)
from kodezart.domain.errors import ScopedExecutionUnavailableError, ScopeReadError
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AgentEvent,
    ErrorEvent,
    WorkflowCompleteEvent,
    WorkflowPREvent,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.job import JobState
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.ticket_review import TicketReviewMode
from kodezart.types.domain.tracker import TrackerIssue
from kodezart.types.domain.workflow import WorkflowSubmission
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeChangePersister,
    FakeGitService,
    FakeQualityGate,
    FakeRefPublisher,
    FakeRepoCache,
    FakeTicketGenerator,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_passing_evaluation,
    make_prompt_provider,
    make_tracker_issue,
    no_delay_floor,
)

#: The origin the hundred-minute fire ran over: a local bare repository,
#: the sanctioned smoke shape, with no forge behind it to be asked.
FILE_ORIGIN = "file:///tmp/smoke-origin.git"
FORGE_ORIGIN = "https://github.com/owner/repo"
SETTLE_SECONDS = 5.0

SRC = Path(__file__).resolve().parents[1] / "src" / "kodezart"
COMPOSITION = SRC / "composition"

#: The keyword slots the workflow engine takes a forge-backed capability
#: through.  Bound as a set at exactly one site, so a capability cannot be
#: selected apart from its peers.
ENGINE_FORGE_SLOTS: frozenset[str] = frozenset(
    {"visibility_resolver", "pr_creator", "ci_monitor"},
)

#: The forge client parameter of the composition root.  Its presence is
#: what used to decide every capability above, and must decide none.
FORGE_CLIENT_PARAM = "github_api"

#: Enough to make the composition root build a client. Nothing here dials
#: the forge: every client this module builds is closed unused.
FAKE_TOKEN = "not-a-real-token"

#: Every protocol the forge adapter answers, against the per-origin
#: selection that covers it.  ``DeliveryProbe`` is the dispatch tick's,
#: repaired under KOD-145; three are the engine's and queries have an
#: explicit per-origin selector for their downstream consumer.
COVERED_BY_ORIGIN: dict[type, str] = {
    PRCreator: "pr_creator",
    CIMonitor: "ci_monitor",
    RepoVisibilityResolver: "visibility_resolver",
    DeliveryProbe: "delivery",
    ForgeQuery: "query",
    PRContentEditor: "pr_content",
    CIObservationReader: "ci_observations",
    PRStateReader: "pr_state",
}


class RecordingForge:
    """One object answering the whole forge surface, counting every ask.

    The shape of the real wiring: a single client satisfies pull-request
    creation, the checks surface and visibility resolution at once, so a
    test that substituted three separate doubles could not observe the
    thing the defect is about — that they are chosen together.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def create_pr(
        self,
        *,
        repo_url: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> tuple[str, int]:
        self.calls.append("create_pr")
        return (f"{repo_url}/pull/1", 1)

    async def comment_on_pr(
        self,
        *,
        repo_url: str,
        pr_number: int,
        body: str,
    ) -> None:
        self.calls.append("comment_on_pr")

    async def wait_for_checks(
        self,
        *,
        repo_url: str,
        ref: str,
    ) -> tuple[bool | None, str]:
        self.calls.append("wait_for_checks")
        return (True, "All CI checks passed.")

    async def resolve_visibility(self, *, repo_url: str) -> RepoVisibility:
        self.calls.append("resolve_visibility")
        return RepoVisibility.PUBLIC


def _arm(*, forge: RecordingForge | None) -> RalphWorkflowEngine:
    """One engine arm, wired exactly as the composition root wires it."""
    return RalphWorkflowEngine(
        service=AgentService(
            git_base_url="https://github.com",
            executor=FakeAgentExecutor(events=[]),
            workspace=FakeWorkspaceProvider(),
            persister=FakeChangePersister(),
        ),
        quality_gate=FakeQualityGate(
            events=[],
            evaluation=make_passing_evaluation(),
            total_iterations=1,
            last_commit_sha="a" * 40,
        ),
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        visibility_resolver=forge,
        pr_creator=forge,
        ci_monitor=forge,
        artifact_persister=FakeArtifactPersister(),
        retry_max_attempts=3,
        retry_initial_interval=1.0,
        remediation_max_rounds=1,
        criteria_max_regeneration_rounds=1,
        fan_in_max_attempts=2,
        delay_floor_for=no_delay_floor,
    )


async def _drive(
    engine: WorkflowEngine,
    *,
    repo_url: str,
    scope: ScopeRef | None = None,
) -> list[AgentEvent]:
    return [
        event
        async for event in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url=repo_url,
            scope=scope,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]


class ForbiddenWorkflowEngine:
    """A scoped entry must never select a legacy execution arm."""

    def run(self, *, scope: ScopeRef | None, **_: object) -> AsyncIterator[AgentEvent]:
        raise AssertionError("a scoped submission entered the legacy workflow arm")


class RecordingScopeTracker(FakeTrackerPort):
    """Record the exact scope address the production resolver receives."""

    def __init__(self, *, failure: ScopeReadError | None = None) -> None:
        super().__init__(issues=[make_tracker_issue("ENG-1")])
        self.scope_reads: list[ScopeRef] = []
        self.failure = failure

    async def scope_issues(self, *, ref: ScopeRef) -> Sequence[TrackerIssue]:
        self.scope_reads.append(ref)
        if self.failure is not None:
            raise self.failure
        return tuple(self.issues.values())


@pytest.mark.parametrize("kind", tuple(ScopeKind))
@pytest.mark.parametrize("repo_url", [FILE_ORIGIN, FORGE_ORIGIN])
async def test_scoped_queue_jobs_resolve_then_publish_the_typed_refusal(
    kind: ScopeKind,
    repo_url: str,
) -> None:
    """Scope is consumed at dequeue and never runs the legacy prompt pipeline."""
    ref = ScopeRef(kind=kind, key="opaque-address")
    tracker = RecordingScopeTracker()
    queue = build_job_queue(
        config=AppConfig(),
        workflow_engine=OriginRoutedWorkflowEngine(
            forge_arm=ForbiddenWorkflowEngine(),
            forge_less_arm=ForbiddenWorkflowEngine(),
            tracker=tracker,
        ),
    )
    await queue.start()
    try:
        record = await queue.submit(
            lane=DEFAULT_LANE,
            request=WorkflowSubmission(
                prompt="execute the scope",
                repo_path=None,
                repo_url=repo_url,
                base_spec=trunk_base("main"),
                implied_base=None,
                scope=ref,
                permission_mode="bypassPermissions",
                allowed_tools=["Read"],
            ),
        )
        async with asyncio.timeout(SETTLE_SECONDS):
            events = [event async for event in queue.attach(job_id=record.job_id)]
        assert tracker.scope_reads == [ref]
        (error,) = events
        assert isinstance(error, ErrorEvent)
        assert error.error_kind == "ScopedExecutionUnavailableError"
        assert f"scope: {ref.kind.value}:{ref.key}" in error.error
        assert "not implemented" in error.error
        terminal = await queue.get(job_id=record.job_id)
        assert terminal is not None
        assert terminal.state is JobState.TERMINAL
        assert terminal.outcome is WorkflowOutcome.engine_error
    finally:
        await queue.stop()


async def test_scope_read_failure_propagates_without_a_legacy_run() -> None:
    ref = ScopeRef(kind=ScopeKind.PROJECT, key="unreadable")
    failure = ScopeReadError("scope membership cannot be read", ref=ref)
    tracker = RecordingScopeTracker(failure=failure)
    engine = OriginRoutedWorkflowEngine(
        forge_arm=ForbiddenWorkflowEngine(),
        forge_less_arm=ForbiddenWorkflowEngine(),
        tracker=tracker,
    )

    with pytest.raises(ScopeReadError) as caught:
        await _drive(engine, repo_url=FORGE_ORIGIN, scope=ref)

    assert caught.value is failure
    assert tracker.scope_reads == [ref]


async def test_scope_without_a_tracker_refuses_before_selecting_a_legacy_arm() -> None:
    ref = ScopeRef(kind=ScopeKind.ISSUE, key="ENG-1")
    engine = OriginRoutedWorkflowEngine(
        forge_arm=ForbiddenWorkflowEngine(),
        forge_less_arm=ForbiddenWorkflowEngine(),
        tracker=None,
    )

    with pytest.raises(ScopedExecutionUnavailableError, match="configured tracker"):
        await _drive(engine, repo_url=FORGE_ORIGIN, scope=ref)


async def test_direct_legacy_engine_calls_cannot_drop_an_addressed_scope() -> None:
    forge = RecordingForge()
    engine = _arm(forge=forge)
    ref = ScopeRef(kind=ScopeKind.PROJECT, key="project-address")

    with pytest.raises(ScopedExecutionUnavailableError, match="scope entry pipeline"):
        await _drive(engine, repo_url=FORGE_ORIGIN, scope=ref)

    assert forge.calls == []


# ---------------------------------------------------------------------------
# The two arms, asserted at the wiring
# ---------------------------------------------------------------------------


async def test_a_fire_over_a_file_origin_reaches_the_no_pull_request_terminal() -> None:
    """The hundred-minute crash, as a fixture, no longer fatal.

    The run terminates through the path the codebase already had for
    "there is nowhere to open a pull request" — and the forge adapter is
    not asked anything at all, rather than asked and left to raise.
    """
    forge = RecordingForge()
    engine = OriginRoutedWorkflowEngine(
        forge_arm=_arm(forge=forge),
        forge_less_arm=_arm(forge=None),
        tracker=None,
    )

    events = await _drive(engine, repo_url=FILE_ORIGIN)

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.review_passed_no_pr_adapter
    assert complete.merged is True
    assert complete.pr_url is None
    assert [e for e in events if isinstance(e, WorkflowPREvent)] == []
    assert forge.calls == []


async def test_a_fire_over_a_forge_shaped_origin_still_opens_its_pull_request() -> None:
    """Selection, not removal: the other arm is unchanged from today."""
    forge = RecordingForge()
    engine = OriginRoutedWorkflowEngine(
        forge_arm=_arm(forge=forge),
        forge_less_arm=_arm(forge=None),
        tracker=None,
    )

    events = await _drive(engine, repo_url=FORGE_ORIGIN)

    (pr_event,) = [e for e in events if isinstance(e, WorkflowPREvent)]
    assert pr_event.pr_url == f"{FORGE_ORIGIN}.git/pull/1"
    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.ci_passed
    assert "create_pr" in forge.calls
    assert "resolve_visibility" in forge.calls
    assert "wait_for_checks" in forge.calls


def test_a_forge_less_origin_gets_the_forge_less_arm() -> None:
    forge_arm: WorkflowEngine = _arm(forge=RecordingForge())
    forge_less_arm: WorkflowEngine = _arm(forge=None)
    engine = OriginRoutedWorkflowEngine(
        forge_arm=forge_arm,
        forge_less_arm=forge_less_arm,
        tracker=None,
    )

    assert engine.arm_for(FILE_ORIGIN) is forge_less_arm


@pytest.mark.parametrize(
    "repo_url",
    [FORGE_ORIGIN, "https://gitlab.com/o/r.git", "owner/repo", None],
)
def test_everything_else_keeps_the_forge_arm(repo_url: str | None) -> None:
    """Shorthand and absence are forge-shaped, as the predicate says.

    An unrecognised URL is left to the adapter that owns the scheme, which
    raises on the ones it does not — the loud failure this issue keeps.
    """
    forge_arm: WorkflowEngine = _arm(forge=RecordingForge())
    forge_less_arm: WorkflowEngine = _arm(forge=None)
    engine = OriginRoutedWorkflowEngine(
        forge_arm=forge_arm,
        forge_less_arm=forge_less_arm,
        tracker=None,
    )

    assert engine.arm_for(repo_url) is forge_arm


async def test_the_builder_wires_both_arms_and_routes_between_them() -> None:
    """The composition root's own builder, not a hand-assembled analogue."""
    client = build_forge_client(config=AppConfig(github_token=FAKE_TOKEN))
    assert client is not None
    tracker = RecordingScopeTracker()
    try:
        engine = build_workflow_engine(
            # The shared prompt fixture resolves its set for the reviewed
            # mode, and the ticket loop refuses a config that asks for a
            # guarantee the resolved set cannot deliver.
            config=AppConfig(ticket_review_mode=TicketReviewMode.REVIEWED),
            agent_service=AgentService(
                git_base_url="https://github.com",
                executor=FakeAgentExecutor(events=[]),
                workspace=FakeWorkspaceProvider(),
                persister=FakeChangePersister(),
            ),
            git=FakeGitService(),
            cache=FakeRepoCache(),
            workspace=FakeWorkspaceProvider(),
            merger=FakeBranchMerger(),
            artifact_persister=FakeArtifactPersister(),
            ref_publisher=FakeRefPublisher(),
            prompts=make_prompt_provider(),
            skills=SUPPRESS_ALL_SKILLS,
            gate=PassThroughGate(),
            github_api=client,
            checkpointer=None,
            tracker=tracker,
        )

        assert isinstance(engine, OriginRoutedWorkflowEngine)
        assert engine.arm_for(FILE_ORIGIN) is not engine.arm_for(FORGE_ORIGIN)
        assert engine.arm_for(None) is engine.arm_for(FORGE_ORIGIN)
        ref = ScopeRef(kind=ScopeKind.PROJECT, key="configured-project")
        with pytest.raises(ScopedExecutionUnavailableError, match="not implemented"):
            await _drive(engine, repo_url=FORGE_ORIGIN, scope=ref)
        assert tracker.scope_reads == [ref]
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# The static assertion: a fourth site cannot be added silently
# ---------------------------------------------------------------------------


def _answered_protocols(client: GitHubAPIClient) -> set[type]:
    """Every runtime-checkable port of the domain *client* satisfies.

    Asked of an instance rather than the class: several ports carry
    non-method members, which ``issubclass`` refuses on a Protocol.
    """
    return {
        member
        for member in vars(protocols).values()
        if isinstance(member, type)
        and getattr(member, "_is_runtime_protocol", False)
        and isinstance(client, member)
    }


async def test_the_forge_adapter_answers_exactly_the_covered_capability_set() -> None:
    """Every protocol the client satisfies has a per-origin selection.

    This is the guard the issue asks for.  A fifth protocol added to the
    forge adapter fails here until the selection covers it, so the next
    hundred-minute run cannot discover an uncovered site the way the last
    one did.
    """
    client = build_forge_client(config=AppConfig(github_token=FAKE_TOKEN))
    assert client is not None
    try:
        answered = _answered_protocols(client)
    finally:
        await client.close()

    assert answered == set(COVERED_BY_ORIGIN)


def _forge_slot_keywords(module: Path) -> list[ast.keyword]:
    """Every keyword argument in *module* naming an engine forge slot."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    return [
        keyword
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg in ENGINE_FORGE_SLOTS
    ]


def test_the_engine_forge_slots_are_bound_as_one_set() -> None:
    """All three, one expression: no capability moves on its own."""
    keywords = _forge_slot_keywords(COMPOSITION / "engine.py")

    assert {keyword.arg for keyword in keywords} == ENGINE_FORGE_SLOTS
    bound = {ast.unparse(keyword.value) for keyword in keywords}
    assert len(bound) == 1, (
        f"the engine's forge capabilities are bound to {sorted(bound)}; "
        "one value for all of them is what makes the selection a set."
    )


def test_no_engine_forge_slot_is_bound_to_the_forge_client() -> None:
    """Client presence selects nothing. Origin selects everything."""
    for module in sorted(COMPOSITION.glob("*.py")):
        for keyword in _forge_slot_keywords(module):
            assert ast.unparse(keyword.value) != FORGE_CLIENT_PARAM, (
                f"{module.name} binds {keyword.arg} to {FORGE_CLIENT_PARAM}, "
                "which is the defect KOD-148 exists to remove."
            )


def test_only_the_engine_builder_binds_an_engine_forge_slot() -> None:
    """One selection site for the set, findable by this test."""
    binding = sorted(
        module.name
        for module in COMPOSITION.glob("*.py")
        if _forge_slot_keywords(module)
    )

    assert binding == ["engine.py"]


def test_the_delivery_capability_is_selected_by_the_same_predicate() -> None:
    """The fourth member of the set, repaired under KOD-145, still is.

    Read from the syntax tree rather than trusted: this asserts that the
    dispatcher's probe reaches it through the per-origin chooser and not
    through the client the pass builder was handed.
    """
    tree = ast.parse((COMPOSITION / "passes.py").read_text(encoding="utf-8"))
    dispatcher_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "FireDispatcher"
    ]

    assert len(dispatcher_calls) == 1
    (delivery,) = [
        keyword for keyword in dispatcher_calls[0].keywords if keyword.arg == "delivery"
    ]
    assert isinstance(delivery.value, ast.Call)
    assert isinstance(delivery.value.func, ast.Name)
    assert delivery.value.func.id == "delivery_probe_for"


@pytest.mark.parametrize("repo_url", [FORGE_ORIGIN, "https://github.example/o/r.git"])
async def test_query_capability_selects_repository_origin_before_read(repo_url):
    from tests.fakes import FakeForgeQuery

    expected = (f"{repo_url}/pull/7", 7)
    client = FakeForgeQuery(open_prs={(repo_url, "feature"): expected})
    selected = forge_query_for_origin(client=client, repo_url=repo_url)
    assert selected is client
    assert (
        await selected.open_pr_for_head(repo_url=repo_url, head="feature") == expected
    )
    assert client.calls == [
        {"method": "open_pr_for_head", "repo_url": repo_url, "head": "feature"}
    ]


def test_query_capability_is_absent_for_local_origin_despite_client():
    from tests.fakes import FakeForgeQuery

    client = FakeForgeQuery()
    assert forge_query_for_origin(client=client, repo_url=FILE_ORIGIN) is None
    assert client.calls == []


def test_query_capability_is_absent_without_configured_client():
    assert forge_query_for_origin(client=None, repo_url=FORGE_ORIGIN) is None


@pytest.mark.parametrize(
    "repo_url", [FORGE_ORIGIN, "https://github.example/owner/repo"]
)
async def test_content_capability_selects_origin_before_native_read_and_edit(repo_url):
    from tests.adapters.test_github_api import _make_client

    requests = []
    row = {
        "html_url": f"{repo_url}/pull/7",
        "number": 7,
        "head": {"ref": "feature"},
        "base": {"ref": "main"},
        "title": "Before",
        "body": "Body",
    }

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            assert request.url.path == "/repos/owner/repo/pulls"
            assert request.url.params["head"] == "owner:feature"
            return httpx.Response(200, json=[row])
        assert request.method == "PATCH"
        assert request.url.path == "/repos/owner/repo/pulls/7"
        assert json.loads(request.content) == {"title": "After"}
        row["title"] = "After"
        return httpx.Response(200, json=row)

    client = _make_client(handler)
    selected = pr_content_editor_for_origin(client=client, repo_url=repo_url)
    assert selected is client
    try:
        before = await selected.read_open_pr(
            repo_url=repo_url, head="feature", pr_number=7
        )
        after = await selected.edit_pr(
            repo_url=repo_url, expected=before, title="After", body="Body", base="main"
        )
    finally:
        await client.close()
    assert after.title == "After" and after.url == before.url and after.number == 7
    assert [request.method for request in requests] == ["GET", "GET", "PATCH"]


@pytest.mark.parametrize("repo_url", [FILE_ORIGIN, "file:///var/repository.git"])
async def test_content_capability_is_absent_for_local_origin_despite_client(repo_url):
    from tests.adapters.test_github_api import _make_client

    requests = []

    def handler(request):
        requests.append(request)
        raise AssertionError("a local origin cannot ask the forge")

    client = _make_client(handler)
    try:
        assert pr_content_editor_for_origin(client=client, repo_url=repo_url) is None
    finally:
        await client.close()
    assert requests == []


def test_content_capability_is_absent_without_configured_client():
    assert pr_content_editor_for_origin(client=None, repo_url=FORGE_ORIGIN) is None


async def test_native_watch_observation_reader_is_selected_by_origin():
    from tests.adapters.test_github_api import _completed_run, _make_client

    requests = []
    payload = _completed_run("failure").json()
    payload["check_runs"][0]["head_sha"] = "a" * 40

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    client = _make_client(handler)
    try:
        assert (
            ci_observation_reader_for_origin(client=None, repo_url=FORGE_ORIGIN) is None
        )
        assert (
            ci_observation_reader_for_origin(client=client, repo_url=FILE_ORIGIN)
            is None
        )
        selected = ci_observation_reader_for_origin(
            client=client, repo_url=FORGE_ORIGIN
        )
        assert selected is client
        await client.wait_for_checks(repo_url=FORGE_ORIGIN, ref="feature")
        count = len(requests)
        observed = await selected.observed_checks(repo_url=FORGE_ORIGIN, ref="feature")
        assert observed.commit_sha == "a" * 40 and observed.checks_passed is False
        assert len(requests) == count
    finally:
        await client.close()


async def test_native_pr_state_reader_is_selected_before_any_forge_read():
    from tests.adapters.test_github_api import _make_client
    from tests.adapters.test_pr_state_reader import REPO, payload

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload())

    client = _make_client(handler)
    try:
        assert pr_state_reader_for_origin(client=None, repo_url=REPO) is None
        assert pr_state_reader_for_origin(client=client, repo_url=FILE_ORIGIN) is None
        assert requests == []
        selected = pr_state_reader_for_origin(client=client, repo_url=REPO)
        assert selected is client
        observed = await selected.read_pr_state(repo_url=REPO, pr_number=7)
        assert observed.url == f"{REPO}/pull/7"
        assert observed.head_repo_url == REPO
        assert [(request.method, request.url.path) for request in requests] == [
            ("GET", "/repos/example/project/pulls/7")
        ]
    finally:
        await client.close()
