"""KOD-87-AC-1, AC-2, AC-3, AC-8 — the widened executor seam.

The port used to be construction-scoped for everything a session role
needs, so a role could only be expressed by reaching around it.  These
tests assert the two halves that make the widening real: every new option
reaches ``ClaudeAgentOptions`` with the value the dispatch declared, and a
dispatch that declares nothing constructs exactly the options this port
constructed before it widened.

The baseline is measured against the signature the tree HAS — eight
parameters, ``skills`` and ``session_type`` included, both landed by
earlier lanes — per the fire-time ruling of 2026-08-11.  Measuring it
against the six the issue body describes would make the no-change proof a
tautology about a shape that no longer exists.
"""

import inspect
import json
from collections.abc import Sequence

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from pydantic import ValidationError

from kodezart.adapters._agents_mapping import (
    map_agents,
    map_effort,
    map_system_prompt,
    map_workflow_env,
    map_workflow_settings,
)
from kodezart.core.protocols import AgentExecutor
from kodezart.types.domain.subagents import (
    NO_SUBAGENTS,
    UNCONFIGURED_SESSION_POLICY,
    AgentDefinition,
    SessionEffort,
    SessionPolicy,
    WorkflowAccess,
)
from tests.fakes import (
    EXECUTOR_MODULES,
    RecordedSession,
    executor_for,
    recorded_session,
)

EXPLORER = AgentDefinition(
    name="explorer",
    description="Read-only repository investigation.",
    prompt="Answer the question by reading this repository.",
    tools=("Read", "Glob", "Grep"),
)
DOC_VERIFIER = AgentDefinition(
    name="doc-verifier",
    description="Verifies claims against first-party documentation.",
    prompt="Verify each claim against first-party sources only.",
    tools=("WebSearch", "WebFetch", "Read"),
)

WORKFLOW_ACCESS = WorkflowAccess(
    workflows_path=".claude/workflows",
    size_guideline=6,
    enabled=True,
)

FULL_POLICY = SessionPolicy(
    system_prompt_append="kodezart house rules: be terse.",
    effort=SessionEffort.HIGH,
    model="per-call-engine",
    fallback_model="fallback-engine",
    workflow_access=WORKFLOW_ACCESS,
)


def options_of(session: RecordedSession) -> ClaudeAgentOptions:
    """The recorded session's options, typed."""
    assert isinstance(session.options, ClaudeAgentOptions)
    return session.options


# ---------------------------------------------------------------------------
# KOD-87-AC-1 — every new option reaches the SDK, and absence changes nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_agents_reach_the_sdk_options(module: str) -> None:
    """Definitions cross the port as domain models and land as SDK agents."""
    session = await recorded_session(module, agents=(EXPLORER, DOC_VERIFIER))
    agents = options_of(session).agents
    assert agents is not None
    assert sorted(agents) == ["doc-verifier", "explorer"]
    assert agents["explorer"].prompt == EXPLORER.prompt
    assert agents["explorer"].description == EXPLORER.description
    assert agents["explorer"].tools == list(EXPLORER.tools)


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_system_prompt_append_reaches_the_sdk_options(module: str) -> None:
    """The house-rules append rides the preset rather than replacing it."""
    session = await recorded_session(module, session_policy=FULL_POLICY)
    system_prompt = options_of(session).system_prompt
    assert system_prompt == {
        "type": "preset",
        "preset": "claude_code",
        "append": FULL_POLICY.system_prompt_append,
    }


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_effort_reaches_the_sdk_options(module: str) -> None:
    """The cost lever is a declared level, never a literal in a chain."""
    session = await recorded_session(module, session_policy=FULL_POLICY)
    assert options_of(session).effort == "high"


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_fallback_model_reaches_the_sdk_options(module: str) -> None:
    """The refusal fallback was an unplumbed SDK field; it is plumbed here."""
    session = await recorded_session(module, session_policy=FULL_POLICY)
    assert options_of(session).fallback_model == "fallback-engine"


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_workflow_gates_reach_the_sdk_env_and_settings(module: str) -> None:
    """The env/settings passthrough that decides whether a workflow can fire."""
    options = options_of(await recorded_session(module, session_policy=FULL_POLICY))
    assert options.env == {"CLAUDE_CODE_WORKFLOWS": ".claude/workflows"}
    assert options.settings is not None
    assert json.loads(options.settings) == {"workflowSizeGuideline": 6}


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_disabled_workflow_access_sets_the_disable_variable(module: str) -> None:
    """Declaring access and disabling it is not the same as declaring none."""
    policy = SessionPolicy(
        workflow_access=WorkflowAccess(
            workflows_path=".claude/workflows",
            size_guideline=6,
            enabled=False,
        ),
    )
    options = options_of(await recorded_session(module, session_policy=policy))
    assert options.env == {"CLAUDE_CODE_DISABLE_WORKFLOWS": "1"}
    assert options.settings is None


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_declaring_nothing_constructs_todays_options(module: str) -> None:
    """The no-behavioural-change proof, over every new option at once.

    Measured field by field rather than by comparing two option objects,
    so a future option added to the SDK cannot make this pass by being
    absent from both sides.
    """
    options = options_of(await recorded_session(module))
    assert options.agents is None
    assert options.system_prompt is None
    assert options.effort is None
    assert options.fallback_model is None
    assert options.env == {}
    assert options.settings is None


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
async def test_declaring_nothing_leaves_the_pre_existing_options_intact(
    module: str,
) -> None:
    """The eight parameters the port already carried still land unchanged."""
    options = options_of(await recorded_session(module, cwd="/tmp/seam"))
    assert options.cwd == "/tmp/seam"
    assert options.permission_mode == "plan"
    assert options.allowed_tools == []
    assert options.skills == []
    assert options.resume is None
    assert options.output_format is None


# ---------------------------------------------------------------------------
# KOD-87-AC-2 — the per-call model overrides the construction-time one
# ---------------------------------------------------------------------------

CLIENT_MODULE = "kodezart.adapters.claude_client_executor"


async def test_per_call_model_overrides_construction_model() -> None:
    """A dispatch that names an engine gets it; one that does not keeps "a"."""
    overridden = await recorded_session(
        CLIENT_MODULE,
        model="a",
        session_policy=SessionPolicy(model="b"),
    )
    assert options_of(overridden).model == "b"

    inherited = await recorded_session(CLIENT_MODULE, model="a")
    assert options_of(inherited).model == "a"


async def test_the_one_shot_executor_gains_the_per_call_model_path() -> None:
    """It passed no model at all before; a declared one now reaches the SDK."""
    module = "kodezart.adapters.claude_agent_executor"
    session = await recorded_session(module, session_policy=SessionPolicy(model="b"))
    assert options_of(session).model == "b"
    assert options_of(await recorded_session(module)).model is None


# ---------------------------------------------------------------------------
# KOD-87-AC-3 — both implementations still satisfy the port
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
def test_both_executors_still_satisfy_the_widened_port(module: str) -> None:
    """The runtime-checkable protocol, after widening."""
    assert isinstance(executor_for(module), AgentExecutor)


@pytest.mark.parametrize("module", EXECUTOR_MODULES)
def test_both_executors_declare_the_widened_signature(module: str) -> None:
    """Structural conformance is not name conformance — check the parameters."""
    port = inspect.signature(AgentExecutor.stream).parameters
    shipped = inspect.signature(executor_for(module).stream).parameters
    assert set(port) - {"self"} <= set(shipped)
    assert "agents" in shipped
    assert "session_policy" in shipped


# ---------------------------------------------------------------------------
# KOD-87-AC-8 — no raw dictionary crosses the port
# ---------------------------------------------------------------------------


def test_the_ports_new_parameters_are_declared_as_domain_models() -> None:
    """Declared types, not conventions: a dict is not an accepted shape."""
    parameters = inspect.signature(AgentExecutor.stream).parameters
    assert parameters["agents"].annotation == Sequence[AgentDefinition]
    assert parameters["session_policy"].annotation is SessionPolicy


def test_the_runner_port_carries_the_same_two_parameters() -> None:
    """A widened executor behind a narrow runner is a widening nothing reaches."""
    from kodezart.core.protocols import AgentRunner

    for method in ("stream", "stream_workflow", "stream_in_workspace"):
        parameters = inspect.signature(getattr(AgentRunner, method)).parameters
        assert parameters["agents"].annotation == Sequence[AgentDefinition]
        assert parameters["session_policy"].annotation is SessionPolicy


def test_the_sdk_shapes_are_built_inside_the_adapter_layer() -> None:
    """The mapping module owns every SDK dataclass the seam constructs."""
    assert map_agents(NO_SUBAGENTS) is None
    assert map_system_prompt(UNCONFIGURED_SESSION_POLICY) is None
    assert map_effort(None) is None
    assert map_workflow_env(None) == {}
    assert map_workflow_settings(None) is None


def test_a_definition_is_frozen_and_carries_a_non_empty_tool_list() -> None:
    """Read-only tool lists are one of the two bounds on a lens's reach."""
    with pytest.raises(ValidationError):
        EXPLORER.name = "other"
    with pytest.raises(ValidationError, match="tools"):
        AgentDefinition(
            name="toolless",
            description="d",
            prompt="p",
            tools=(),
        )
