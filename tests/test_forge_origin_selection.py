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
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

import httpx
import pytest

from kodezart.adapters.github.api import GitHubAPIClient
from kodezart.chains.authored_checks import AuthoredChecks
from kodezart.chains.authored_delivery import AuthoredDeliveryCoordinator
from kodezart.chains.authored_publication import AuthoredPublication
from kodezart.chains.fire_specification import FireSpecification
from kodezart.chains.lane_delivery import LaneDeliveryCoordinator
from kodezart.composition.delivery import build_native_lane_workflow
from kodezart.composition.engine import (
    OriginRoutedWorkflowEngine,
    build_workflow_engine,
)
from kodezart.composition.forge import (
    build_forge_client,
    pr_state_reader_for_origin,
)
from kodezart.composition.jobs import build_job_queue
from kodezart.composition.scope_runtime import build_scope_runtime
from kodezart.config.app import AppConfig
from kodezart.core import protocols
from kodezart.core.constants import DEFAULT_LANE
from kodezart.core.protocols import (
    CIMonitor,
    DeliveryProbe,
    ForgeQuery,
    PRCreator,
    PRStateReader,
    RepoVisibilityResolver,
    WorkflowEngine,
)
from kodezart.domain.errors import ScopedExecutionUnavailableError
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AgentEvent,
    ErrorEvent,
    WorkflowCompleteEvent,
    WorkflowPREvent,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.check_observation import ObservedChecks
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.job import JobState
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.ticket_review import TicketReviewMode
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
    FakeWorkspaceProvider,
    PassThroughGate,
    make_passing_evaluation_of_fake_criteria,
    make_prompt_provider,
    no_delay_floor,
)
from tests.workflow_factory import make_authored_workflow

#: The origin the hundred-minute fire ran over: a local bare repository,
#: the sanctioned smoke shape, with no forge behind it to be asked.
FILE_ORIGIN = "file:///tmp/smoke-origin.git"
FORGE_ORIGIN = "https://github.com/owner/repo"
SETTLE_SECONDS = 5.0

SRC = Path(__file__).resolve().parents[1] / "src" / "kodezart"
COMPOSITION = SRC / "composition"

#: The authored arm's forge-touching consumers.  The engine binds every
#: forge capability they take to one value, so no capability can be
#: chosen apart from its peers.
AUTHORED_FORGE_CONSUMERS: tuple[Callable[..., object], ...] = (
    FireSpecification.__init__,
    AuthoredPublication.__init__,
    AuthoredChecks.__init__,
)

#: The native lane's forge-touching consumer: one coordinator taking the
#: pull-request writes and the forge reads it travels with.
LANE_FORGE_CONSUMERS: tuple[Callable[..., object], ...] = (
    LaneDeliveryCoordinator.__init__,
)

#: The builders the composition root hands a whole selected forge to, for
#: the native lane and for the dispatch tick's probe.
LANE_FORGE_BUILDERS: tuple[Callable[..., object], ...] = (
    build_native_lane_workflow,
    build_scope_runtime,
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
    PRStateReader: "pr_state",
    ForgeQuery: "forge_query",
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
    ) -> ObservedChecks:
        self.calls.append("wait_for_checks")
        return ObservedChecks(
            commit_sha="a" * 40,
            checks_passed=True,
            check_names=frozenset({"unit"}),
            failed_check_names=frozenset(),
            summary="All CI checks passed.",
        )

    async def resolve_visibility(self, *, repo_url: str) -> RepoVisibility:
        self.calls.append("resolve_visibility")
        return RepoVisibility.PUBLIC


def _arm(*, forge: RecordingForge | None) -> AuthoredDeliveryCoordinator:
    """One engine arm, wired exactly as the composition root wires it."""
    return make_authored_workflow(
        repositories=(),
        max_concurrent_watches=4,
        red_rerun_max_attempts=0,
        service=AgentService(
            git_base_url="https://github.com",
            executor=FakeAgentExecutor(events=[]),
            workspace=FakeWorkspaceProvider(),
            persister=FakeChangePersister(),
        ),
        quality_gate=FakeQualityGate(
            events=[],
            evaluation=make_passing_evaluation_of_fake_criteria(),
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
            permission_mode=PermissionMode.UNATTENDED,
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]


class ForbiddenWorkflowEngine:
    """A scoped entry must never select a legacy execution arm."""

    def run(self, *, scope: ScopeRef | None, **_: object) -> AsyncIterator[AgentEvent]:
        raise AssertionError("a scoped submission entered the legacy workflow arm")


@pytest.mark.parametrize("kind", tuple(ScopeKind))
@pytest.mark.parametrize("repo_url", [FILE_ORIGIN, FORGE_ORIGIN])
async def test_scoped_queue_jobs_publish_typed_refusal_without_resolving(
    kind: ScopeKind,
    repo_url: str,
) -> None:
    """Unsupported scopes fail at dequeue before I/O or the legacy pipeline."""
    ref = ScopeRef(kind=kind, key="opaque-address")
    queue = build_job_queue(
        settings=AppConfig().queue,
        workflow_engine=OriginRoutedWorkflowEngine(
            forge_arm=ForbiddenWorkflowEngine(),
            forge_less_arm=ForbiddenWorkflowEngine(),
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
                permission_mode=PermissionMode.UNATTENDED,
                allowed_tools=["Read"],
            ),
        )
        async with asyncio.timeout(SETTLE_SECONDS):
            events = [event async for event in queue.attach(job_id=record.job_id)]
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


async def test_scope_without_a_tracker_refuses_before_selecting_a_legacy_arm() -> None:
    ref = ScopeRef(kind=ScopeKind.ISSUE, key="ENG-1")
    engine = OriginRoutedWorkflowEngine(
        forge_arm=ForbiddenWorkflowEngine(),
        forge_less_arm=ForbiddenWorkflowEngine(),
    )

    with pytest.raises(ScopedExecutionUnavailableError, match="not implemented"):
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
    )

    assert engine.arm_for(repo_url) is forge_arm


@pytest.mark.parametrize("kind", tuple(ScopeKind))
async def test_the_builder_wires_both_arms_and_refuses_scope_without_io(kind) -> None:
    """The composition root's own builder, not a hand-assembled analogue."""
    client = build_forge_client(config=AppConfig(github_token=FAKE_TOKEN))
    assert client is not None
    executor = FakeAgentExecutor(events=[])
    workspace = FakeWorkspaceProvider()
    cache = FakeRepoCache()
    git = FakeGitService()
    try:
        engine = build_workflow_engine(
            # The shared prompt fixture resolves its set for the reviewed
            # mode, and the ticket loop refuses a config that asks for a
            # guarantee the resolved set cannot deliver.
            repositories=(),
            config=AppConfig(ticket_review_mode=TicketReviewMode.REVIEWED),
            agent_service=AgentService(
                git_base_url="https://github.com",
                executor=executor,
                workspace=workspace,
                persister=FakeChangePersister(),
            ),
            git=git,
            cache=cache,
            workspace=workspace,
            merger=FakeBranchMerger(),
            artifact_persister=FakeArtifactPersister(),
            ref_publisher=FakeRefPublisher(),
            prompts=make_prompt_provider(),
            skills=SUPPRESS_ALL_SKILLS,
            gate=PassThroughGate(),
            github_api=client,
            checkpointer=None,
        )

        assert isinstance(engine, OriginRoutedWorkflowEngine)
        assert engine.arm_for(FILE_ORIGIN) is not engine.arm_for(FORGE_ORIGIN)
        assert engine.arm_for(None) is engine.arm_for(FORGE_ORIGIN)
        ref = ScopeRef(kind=kind, key="opaque-scope")
        with pytest.raises(ScopedExecutionUnavailableError, match="not implemented"):
            await _drive(engine, repo_url=FORGE_ORIGIN, scope=ref)
        assert executor.calls == []
        assert workspace.calls == []
        assert cache.calls == []
        assert git.calls == []
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


def _annotated_types(annotation: object) -> set[type]:
    """The classes *annotation* admits, unwrapping an optional capability."""
    if get_origin(annotation) in (Union, UnionType):
        return {
            argument for argument in get_args(annotation) if isinstance(argument, type)
        }
    return {annotation} if isinstance(annotation, type) else set()


async def _forge_answered_types() -> frozenset[type]:
    """Every type the built forge adapter answers, the adapter itself included.

    The adapter's own class counts: a builder that takes the whole forge
    rather than one of its ports selects a capability just as much.
    """
    client = build_forge_client(config=AppConfig(github_token=FAKE_TOKEN))
    assert client is not None
    try:
        return frozenset(_answered_protocols(client) | {type(client)})
    finally:
        await client.close()


async def _forge_slots(*consumers: Callable[..., object]) -> frozenset[str]:
    """The keyword slots *consumers* take a forge-answered capability through.

    Read from the signatures, never typed out here: a capability added to
    a consumer joins the covered set without an edit to this module, and
    the exclusivity checks below go red until its binding is accounted
    for.
    """
    answered = await _forge_answered_types()
    slots = frozenset(
        name
        for consumer in consumers
        for name, annotation in get_type_hints(consumer).items()
        if name != "return" and _annotated_types(annotation) & answered
    )
    assert slots, "a forge consumer with no forge-answered parameter is a defect here"
    return slots


def _forge_slot_keywords(module: Path, *, slots: frozenset[str]) -> list[ast.keyword]:
    """Every keyword argument in *module* naming one of *slots*."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    return [
        keyword
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg in slots
    ]


def _binding_modules(slots: frozenset[str]) -> dict[str, frozenset[str]]:
    """Every composition module binding one of *slots*, with the slots it binds.

    The whole enumeration for every exclusivity check below, so a new
    selection site is one that this mapping does not already name.
    """
    found = {
        module.name: frozenset(
            keyword.arg
            for keyword in _forge_slot_keywords(module, slots=slots)
            if keyword.arg is not None
        )
        for module in sorted(COMPOSITION.glob("*.py"))
    }
    return {name: bound for name, bound in found.items() if bound}


def _bound_values(module: Path, *, slots: frozenset[str]) -> set[str]:
    """The distinct expressions *module* binds *slots* to."""
    return {
        ast.unparse(keyword.value)
        for keyword in _forge_slot_keywords(module, slots=slots)
    }


async def test_the_engine_forge_slots_are_bound_as_one_set() -> None:
    """All of them, one expression: no capability moves on its own."""
    slots = await _forge_slots(*AUTHORED_FORGE_CONSUMERS)
    keywords = _forge_slot_keywords(COMPOSITION / "engine.py", slots=slots)

    assert {keyword.arg for keyword in keywords} == slots
    bound = {ast.unparse(keyword.value) for keyword in keywords}
    assert len(bound) == 1, (
        f"the engine's forge capabilities are bound to {sorted(bound)}; "
        "one value for all of them is what makes the selection a set."
    )


async def test_no_engine_forge_slot_is_bound_to_the_forge_client() -> None:
    """Client presence selects nothing. Origin selects everything."""
    slots = await _forge_slots(*AUTHORED_FORGE_CONSUMERS, *LANE_FORGE_CONSUMERS)
    for module in sorted(COMPOSITION.glob("*.py")):
        for keyword in _forge_slot_keywords(module, slots=slots):
            assert ast.unparse(keyword.value) != FORGE_CLIENT_PARAM, (
                f"{module.name} binds {keyword.arg} to {FORGE_CLIENT_PARAM}, "
                "which is the defect KOD-148 exists to remove."
            )


async def test_only_delivery_builders_bind_the_engine_forge_slots() -> None:
    """Authored and native builders receive the one selected capability set."""
    # Restored (KOD-827): the set of modules binding an engine forge slot is closed.
    slots = await _forge_slots(*AUTHORED_FORGE_CONSUMERS)

    assert _binding_modules(slots) == {
        "delivery.py": frozenset({"pr_creator"}),
        "engine.py": frozenset({"visibility_resolver", "pr_creator", "ci_monitor"}),
    }
    assert _bound_values(COMPOSITION / "delivery.py", slots=slots) == {"forge"}


async def test_every_lane_forge_slot_is_bound_only_where_a_lane_is_composed() -> None:
    """One selection site for the lane's whole forge surface, reads included.

    The exclusivity the engine's write slots carry above, restated for
    every capability the lane coordinator takes: the pull-request writes,
    the checks surface, the PR lifecycle reader and the forge query reach
    the lane from a single composition module, bound to the one selected
    client.  The other two modules named here bind only the checks
    surface, for the audit sweep, and neither can reach the lane.
    """
    # Restored (KOD-827): the set of modules binding a lane forge slot is closed.
    slots = await _forge_slots(*LANE_FORGE_CONSUMERS)

    assert _binding_modules(slots) == {
        "audit.py": frozenset({"ci"}),
        "delivery.py": frozenset(
            {"pr_creator", "pr_state_reader", "forge_query", "ci"},
        ),
        "engine.py": frozenset({"pr_creator"}),
        "passes.py": frozenset({"ci"}),
    }
    assert _bound_values(COMPOSITION / "delivery.py", slots=slots) == {"forge"}


async def test_the_lane_builders_are_handed_a_forge_from_one_composition_site() -> None:
    """The selected forge itself travels to the lane and the probe once.

    The originally dropped check, in the shape the composition now has.
    Only the engine root hands a builder the whole client, and it calls
    the lane builder twice — once with the client for the forge-shaped
    origins, once with nothing for the rest, which is the selection the
    probe then repeats per origin.  The other two modules named here bind
    a keyword of the same name for the audit sweep and the dispatch tick,
    never to one of these builders.
    """
    # Restored (KOD-827): the set of modules handing a builder a whole forge is closed.
    slots = await _forge_slots(*LANE_FORGE_BUILDERS)
    engine = COMPOSITION / "engine.py"

    assert _binding_modules(slots) == {
        "audit.py": frozenset({"forge"}),
        "engine.py": frozenset({"forge", "forge_probe"}),
        "passes.py": frozenset({"forge"}),
    }
    assert _bound_values(engine, slots=frozenset({"forge"})) == {
        FORGE_CLIENT_PARAM,
        "None",
    }
    assert _bound_values(engine, slots=frozenset({"forge_probe"})) == {
        FORGE_CLIENT_PARAM
    }


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
