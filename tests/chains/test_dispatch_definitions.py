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
from kodezart.chains.ticket_generation import TicketGenerationLoop
from kodezart.core.errors import NoStructuredOutputError
from kodezart.types.domain.agent import AgentEvent
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionPolicy,
)
from tests.fakes import SUPPRESS_ALL_SKILLS
from tests.prompts.test_claude_opus_goldens import V5_SET

LENS_NAMES = ("doc-verifier", "draft-critic", "explorer")


def v5_provider() -> InRepoPromptRegistry:
    """A registry over the set that declares the three lenses."""
    return InRepoPromptRegistry.load(
        sets_root=default_sets_root(),
        default_set=V5_SET,
        set_overrides={},
        template_overrides={},
        bindings={},
    )


@dataclass
class RecordedDispatch:
    """One dispatch, reduced to what the role rule is about."""

    agent_names: tuple[str, ...]


@dataclass
class RecordingRunner:
    """``AgentRunner`` double that records the definitions of every dispatch."""

    dispatches: list[RecordedDispatch] = field(default_factory=list)

    def _record(self, agents: Sequence[AgentDefinition]) -> None:
        self.dispatches.append(
            RecordedDispatch(
                agent_names=tuple(definition.name for definition in agents),
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
        """Record the dispatch and stream nothing."""
        self._record(agents)
        for event in ():
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
        self._record(agents)
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
        self._record(agents)
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


@pytest.mark.parametrize(("module", "schema"), EVALUATIVE_SITES)
def test_every_evaluative_dispatch_declares_the_empty_definition_set(
    module: str,
    schema: str,
) -> None:
    """Named, so the guarantee is visible at the call site it binds."""
    block = dispatch_block(chain_source(module), schema)
    assert "agents=NO_SUBAGENTS" in block
    assert "definitions()" not in block


def test_the_post_merge_review_dispatch_declares_the_empty_definition_set() -> None:
    """Its own site, asserted separately: it is a second evaluative call."""
    source = chain_source("ralph_workflow.py")
    review = source.index('site="post_merge_review"')
    start = source.rindex("self._service.stream", 0, review)
    assert "agents=NO_SUBAGENTS" in source[start:review]


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
    )
