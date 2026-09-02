"""KOD-87-AC-4, AC-5, AC-6 — which dispatches may spawn, and which may not.

The rule is mechanical rather than stated: the generative sites hand the
set's lens definitions to the session, and the evaluative sites hand an
empty sequence.  Both halves are measured through a set that ACTUALLY
DECLARES three lenses — a guarantee checked against a set with nothing to
leak is a guarantee about the fixture.

Because the runner double records every dispatch, the same run answers
"did the creator get the three?" and "did the evaluator get none?" from one
observation rather than from two differently configured ones.
"""

from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, field

import pytest

from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.core.errors import NoStructuredOutputError
from kodezart.types.domain.agent import AgentEvent, ResultEvent
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionPolicy,
)
from kodezart.types.domain.ticket_review import TicketReviewMode
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeGitService,
    FakeRepoCache,
    make_criteria,
    no_delay_floor,
)
from tests.prompt_census import configured_investigation_cap
from tests.prompts.test_claude_opus_goldens import V5_SET

LENS_NAMES = ("doc-verifier", "draft-critic", "explorer")


def v5_provider(
    mode: TicketReviewMode = TicketReviewMode.REVIEWED,
) -> InRepoPromptRegistry:
    """A registry over the set that declares the three lenses, under *mode*.

    One loader for both modes: a second copy resolved a different way is
    how two suites come to disagree about what the set says.
    """
    return InRepoPromptRegistry.load(
        sets_root=default_sets_root(),
        default_set=V5_SET,
        set_overrides={},
        template_overrides={},
        bindings={},
        investigation_cap=configured_investigation_cap(),
        ticket_review_mode=mode,
    )


@dataclass
class RecordedDispatch:
    """One dispatch, reduced to what the role rules are about."""

    agent_names: tuple[str, ...]
    method: str = "stream"
    policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY
    prompt: str = ""
    skills: SkillsSelection = SUPPRESS_ALL_SKILLS


@dataclass
class RecordingRunner:
    """``AgentRunner`` double that records the definitions of every dispatch.

    ``structured_output``, when set, is streamed back from ``stream`` as a
    result: a node that must survive its own dispatch to be observed twice
    needs an answer, and one that only has to dispatch does not.
    """

    dispatches: list[RecordedDispatch] = field(default_factory=list)
    structured_output: dict[str, object] | None = None

    def _record(
        self,
        agents: Sequence[AgentDefinition],
        method: str,
        policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        prompt: str = "",
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
    ) -> None:
        self.dispatches.append(
            RecordedDispatch(
                agent_names=tuple(definition.name for definition in agents),
                method=method,
                policy=policy,
                prompt=prompt,
                skills=skills,
            ),
        )

    def _answer(self) -> tuple[AgentEvent, ...]:
        """What ``stream`` yields back — nothing unless an answer was configured."""
        if self.structured_output is None:
            return ()
        return (
            ResultEvent(
                subtype="result",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="recording",
                structured_output=self.structured_output,
            ),
        )

    async def stream(
        self,
        *,
        prompt: str,
        repo_path: str | None = None,
        repo_url: str | None = None,
        branch: str | None = None,
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = SessionType.TICKET_FIRE,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
        cache_key: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Record the dispatch and stream the configured answer, if any."""
        self._record(agents, "stream", session_policy, prompt, skills)
        for event in self._answer():
            yield event

    async def stream_in_workspace(
        self,
        *,
        prompt: str,
        workspace_path: str,
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = SessionType.TICKET_FIRE,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Record the dispatch and stream nothing."""
        self._record(agents, "stream_in_workspace", session_policy, prompt, skills)
        for event in ():
            yield event

    async def stream_workflow(
        self,
        *,
        prompt: str,
        repo_path: str | None = None,
        repo_url: str | None = None,
        base_branch: str = "main",
        branch_name: str | None = None,
        ralph_branch: str | None = None,
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_type: SessionType = SessionType.TICKET_FIRE,
        visibility: RepoVisibility = RepoVisibility.UNKNOWN,
        agents: Sequence[AgentDefinition] = NO_SUBAGENTS,
        session_policy: SessionPolicy = UNCONFIGURED_SESSION_POLICY,
        create_branch: bool = True,
        cache_key: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Record the dispatch and stream nothing."""
        self._record(agents, "stream_workflow", session_policy, prompt, skills)
        for event in ():
            yield event


def test_the_set_under_test_actually_declares_three_lenses() -> None:
    """Non-vacuity: an empty set would make every guarantee below trivial."""
    assert tuple(d.name for d in v5_provider().definitions()) == LENS_NAMES


# ---------------------------------------------------------------------------
# The site-by-site rule, read off the shipped sources
# ---------------------------------------------------------------------------

GENERATIVE_SITES = (
    ("ralph_workflow.py", "GENERATED_CRITERIA_SCHEMA"),
    ("ticket_generation.py", "TICKET_DRAFT_SCHEMA"),
)
EVALUATIVE_SITES = (
    ("ralph_loop.py", "ACCEPTANCE_CRITERIA_SCHEMA"),
    ("ralph_workflow.py", "CRITERIA_VALIDATION_SCHEMA"),
)


def chain_source(name: str) -> str:
    """One chain module's text."""
    from kodezart.chains import ralph_loop

    root = ralph_loop.__file__.rsplit("/", maxsplit=1)[0]
    with open(f"{root}/{name}", encoding="utf-8") as handle:
        return handle.read()


def dispatch_block(source: str, schema_name: str) -> str:
    """The dispatch whose output format names *schema_name*, as text."""
    end = source.index(f'"schema": {schema_name}')
    start = source.rindex("self._service.stream", 0, end)
    return source[start:end]


def evaluative_guarantee_holds(block: str) -> bool:
    """Whether one dispatch block names the empty definition set and nothing else."""
    return "agents=NO_SUBAGENTS" in block and "definitions()" not in block


def post_merge_review_block() -> str:
    """The post-merge review dispatch, as text — its own site, found by name."""
    source = chain_source("ralph_workflow.py")
    review = source.index('site="post_merge_review"')
    return source[source.rindex("self._service.stream", 0, review) : review]


@pytest.mark.parametrize(("module", "schema"), EVALUATIVE_SITES)
def test_every_evaluative_dispatch_declares_the_empty_definition_set(
    module: str,
    schema: str,
) -> None:
    """Named, so the guarantee is visible at the call site it binds."""
    assert evaluative_guarantee_holds(dispatch_block(chain_source(module), schema))


def test_the_post_merge_review_dispatch_declares_the_empty_definition_set() -> None:
    """Its own site, asserted separately: it is a second evaluative call."""
    assert evaluative_guarantee_holds(post_merge_review_block())


def test_injecting_definitions_into_an_evaluative_path_fails_the_guard() -> None:
    """KOD-87-AC-4, second clause — the check detects the injection it forbids.

    A guard that passes on the shipped source proves nothing until it is
    shown to reject the violation it exists to catch: the same blocks are
    re-read with the lenses injected, and every one of them must fail.
    """
    injected_blocks = [
        dispatch_block(chain_source(module), schema)
        for module, schema in EVALUATIVE_SITES
    ] + [post_merge_review_block()]

    for block in injected_blocks:
        injected = block.replace(
            "agents=NO_SUBAGENTS",
            "agents=self._prompts.definitions()",
        )
        assert injected != block, "the injection changed nothing — guard is vacuous"
        assert not evaluative_guarantee_holds(injected)


@pytest.mark.parametrize(("module", "schema"), GENERATIVE_SITES)
def test_every_generative_dispatch_hands_over_the_sets_definitions(
    module: str,
    schema: str,
) -> None:
    """The lenses come from the SET, so a set swap changes them with no code."""
    block = dispatch_block(chain_source(module), schema)
    assert "agents=self._prompts.definitions()" in block


def test_no_dispatch_site_builds_its_own_definition_list() -> None:
    """One definition per lens: a site that constructs one is a second copy."""
    for module in ("ralph_loop.py", "ralph_workflow.py", "ticket_generation.py"):
        assert "AgentDefinition(" not in chain_source(module)


def test_no_dispatch_site_passes_a_raw_dict_for_agents() -> None:
    """KOD-87-AC-8 at the call sites, not only at the port's declaration."""
    for module in ("ralph_loop.py", "ralph_workflow.py", "ticket_generation.py"):
        source = chain_source(module)
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped.startswith("agents="):
                continue
            assert stripped in (
                "agents=NO_SUBAGENTS,",
                "agents=self._prompts.definitions(),",
            )


# ---------------------------------------------------------------------------
# The same rule, observed at runtime through the recording runner
# ---------------------------------------------------------------------------


async def creator_dispatches(provider: InRepoPromptRegistry) -> RecordingRunner:
    """Run the ticket loop's create session once against a recording runner.

    The runner streams no result, so the creator raises its typed
    no-structured-output failure — which is the point: the dispatch has
    already happened by then, and what it carried is what is under test.
    """
    runner = RecordingRunner()
    loop = TicketGenerationLoop(
        runner,
        workspace=NullWorkspace(),
        prompts=provider,
        skills=SUPPRESS_ALL_SKILLS,
        retry_max_attempts=1,
        review_mode=TicketReviewMode.REVIEWED,
        retry_initial_interval=1.0,
        delay_floor_for=no_delay_floor,
    )
    with pytest.raises(NoStructuredOutputError):
        async for _event in loop.run(
            prompt="golden task",
            repo_path="/tmp/dispatch-fixture",
            repo_url=None,
            cache_key="dispatch-fixture",
            base_branch="main",
        ):
            pass
    assert runner.dispatches, "the creator never dispatched"
    return runner


async def test_the_ticket_creator_dispatches_exactly_the_three_lenses() -> None:
    """KOD-87-AC-6, measured rather than read: the create session carries all three."""
    runner = await creator_dispatches(v5_provider())
    assert runner.dispatches[0].agent_names == LENS_NAMES


async def test_a_set_with_no_definitions_dispatches_none() -> None:
    """The lenses are set content: a set declaring none hands over none."""
    runner = await creator_dispatches(legacy_provider())
    assert runner.dispatches[0].agent_names == ()


async def evaluator_dispatches(provider: InRepoPromptRegistry) -> RecordingRunner:
    """Run the ralph loop once against a recording runner and return it.

    The evaluator is answered with a passing verdict over the dispatched
    id, so the loop reaches its end rather than dying inside the node —
    what the evaluate dispatch CARRIED is the subject, and a crashed run
    would leave that observation resting on an error path.
    """
    criteria = make_criteria("Tests pass")
    runner = RecordingRunner(
        structured_output={
            "criteriaResults": [
                {
                    "criterionId": criteria[0].id,
                    "criterion": criteria[0].text,
                    "passed": True,
                    "reasoning": "Observed by reading the code.",
                },
            ],
        },
    )
    loop = RalphLoop(
        runner,
        max_iterations=1,
        plateau_window=2,
        git=FakeGitService(),
        cache=FakeRepoCache(),
        prompts=provider,
        skills=SUPPRESS_ALL_SKILLS,
        retry_max_attempts=3,
        retry_initial_interval=1.0,
        fan_in_max_attempts=2,
        delay_floor_for=no_delay_floor,
    )
    async for _event in loop.run(
        prompt="fix it",
        repo_path="/tmp/dispatch-fixture",
        repo_url=None,
        feature_branch="kodezart/test-12345678",
        ralph_branch="kodezart/test-12345678-ralph-abcdef01",
        base_spec=trunk_base("main"),
        work_base_ref="main",
        permission_mode="bypassPermissions",
        allowed_tools=["Bash"],
        acceptance_criteria=criteria,
        cache_key="dispatch-fixture",
        repo_visibility=RepoVisibility.UNKNOWN,
    ):
        pass
    assert runner.dispatches, "the loop never dispatched"
    return runner


async def test_the_evaluator_dispatches_none_of_the_sets_lenses() -> None:
    """KOD-87-AC-4, measured rather than read: the evaluate session gets none.

    The set under test declares three lenses and hands all three to the
    creator, so an empty list here is the role rule operating and not an
    empty fixture.
    """
    runner = await evaluator_dispatches(v5_provider())
    evaluations = [d for d in runner.dispatches if d.method == "stream"]
    assert evaluations, "the evaluate node never dispatched"
    for dispatch in evaluations:
        assert dispatch.agent_names == ()


async def test_no_dispatch_of_the_ralph_loop_carries_a_lens() -> None:
    """The loop's other session is implementation, and it spawns nothing either."""
    runner = await evaluator_dispatches(v5_provider())
    assert [d.agent_names for d in runner.dispatches] == [()] * len(runner.dispatches)


class NullWorkspace:
    """WorkspaceProvider double: the loop needs a path and nothing else."""

    async def acquire(self, **kwargs: object) -> str:
        """Return a path without touching the filesystem."""
        return "/tmp/dispatch-fixture"

    async def release(self, workspace_path: str) -> None:
        """Nothing was acquired, so nothing is released."""

    async def cleanup_branch(self, **kwargs: object) -> None:
        """No branch was ever created."""


def legacy_provider() -> InRepoPromptRegistry:
    """A registry over the set that declares no lenses."""
    return InRepoPromptRegistry.load(
        sets_root=default_sets_root(),
        default_set="claude-opus",
        set_overrides={},
        template_overrides={},
        bindings={},
        investigation_cap=configured_investigation_cap(),
        ticket_review_mode=TicketReviewMode.REVIEWED,
    )
