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
import uuid
from pathlib import Path

import pytest

from kodezart.adapters.github_api import GitHubAPIClient
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.composition.engine import (
    OriginRoutedWorkflowEngine,
    build_workflow_engine,
)
from kodezart.composition.forge import build_forge_client
from kodezart.core import protocols
from kodezart.core.config import AppConfig
from kodezart.core.protocols import (
    CIMonitor,
    DeliveryProbe,
    PRCreator,
    RepoVisibilityResolver,
    WorkflowEngine,
)
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AgentEvent,
    WorkflowCompleteEvent,
    WorkflowPREvent,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.ticket_review import TicketReviewMode
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
    make_passing_evaluation,
    make_prompt_provider,
)

#: The origin the hundred-minute fire ran over: a local bare repository,
#: the sanctioned smoke shape, with no forge behind it to be asked.
FILE_ORIGIN = "file:///tmp/smoke-origin.git"
FORGE_ORIGIN = "https://github.com/owner/repo"

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
#: repaired under KOD-145; the other three are the engine's.
COVERED_BY_ORIGIN: dict[type, str] = {
    PRCreator: "pr_creator",
    CIMonitor: "ci_monitor",
    RepoVisibilityResolver: "visibility_resolver",
    DeliveryProbe: "delivery",
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
    )


async def _drive(
    engine: OriginRoutedWorkflowEngine,
    *,
    repo_url: str,
) -> list[AgentEvent]:
    return [
        event
        async for event in engine.run(
            prompt="fix it",
            repo_path="/tmp/fake",
            repo_url=repo_url,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key=uuid.uuid4().hex,
        )
    ]


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


async def test_the_builder_wires_both_arms_and_routes_between_them() -> None:
    """The composition root's own builder, not a hand-assembled analogue."""
    client = build_forge_client(config=AppConfig(github_token=FAKE_TOKEN))
    assert client is not None
    try:
        engine = build_workflow_engine(
            # The shared prompt fixture resolves its set for the reviewed
            # mode, and the ticket loop refuses a config that asks for a
            # guarantee the resolved set cannot deliver.
            config=AppConfig(ticket_review_mode=TicketReviewMode.REVIEWED),
            agent_service=AgentService(
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
        )

        assert isinstance(engine, OriginRoutedWorkflowEngine)
        assert engine.arm_for(FILE_ORIGIN) is not engine.arm_for(FORGE_ORIGIN)
        assert engine.arm_for(None) is engine.arm_for(FORGE_ORIGIN)
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
