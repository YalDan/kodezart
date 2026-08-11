"""KOD-86 harness-capability probes.

These probes measure the HARNESS, not the repository: what a headless
Claude Agent SDK session can actually do under kodezart's production
session configuration.  Each probe is an instrument -- it dispatches a
real session and records what it observed.  The recorded table is the
deliverable; the verdicts are read off measurements, never assumed.

The six probes carry ``@pytest.mark.live`` and are gated by the shared
``tests/conftest.py`` mechanism (run them with ``pytest -m live``).
``test_probe_config_matches_production`` carries no marker: it runs in
the default suite and pins the probe's session configuration to the
production constants.
"""

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, query

from kodezart.adapters._sdk_mapping import map_message
from kodezart.adapters._skills_mapping import map_setting_sources, map_skills
from kodezart.core.config import AppConfig
from kodezart.core.constants import (
    EVAL_PERMISSION_MODE,
    EVAL_TOOLS,
    TICKET_TOOLS,
)
from kodezart.types.domain.agent import (
    AssistantTextEvent,
    ResultEvent,
    SystemEvent,
    TaskNotificationEvent,
    TaskProgressEvent,
    TaskStartedEvent,
    ToolResultEvent,
    ToolUseEvent,
)

# ---------------------------------------------------------------------------
# Probe vocabulary
# ---------------------------------------------------------------------------

DISPATCH_TOOL_NAMES: tuple[str, ...] = ("Agent", "Task")
WORKFLOW_TOOL_NAME = "Workflow"
WRITE_TOOL_NAME = "Write"
WORKFLOW_TASK_TYPE = "local_workflow"

VERDICT_LIVE = "live"
VERDICT_DEAD = "dead"
VERDICT_PRESENT = "present"
VERDICT_ABSENT = "absent"

# The probes that reach for the workflow primitive have to leave the
# evaluative session shape in exactly two respects, one field at a time.
UNGATED_PERMISSION_MODE: Literal["default"] = "default"
WORKFLOW_ALLOWED_TOOLS: list[str] = [*TICKET_TOOLS, WORKFLOW_TOOL_NAME]
WORKFLOW_WRITE_ALLOWED_TOOLS: list[str] = [*WORKFLOW_ALLOWED_TOOLS, WRITE_TOOL_NAME]

# A session that parks itself on a scheduled wakeup stops being a bounded
# measurement, so the probes deny that one tool.
STALL_GUARD_DISALLOWED_TOOLS: list[str] = ["ScheduleWakeup"]

ENUMERATION_TURNS = 1
DISPATCH_TURNS = 6
WORKFLOW_TURNS = 6

TRIVIAL_WORKFLOW_NAME = "kod86-trivial"
TYPED_WORKFLOW_NAME = "kod86-typed"
BOUNDED_AGENT_TYPE = "kod86-bounded"
GRANTED_AGENT_TYPE = "kod86-granted"
BOUNDED_ARTIFACT = "kod86-bounded.txt"
GRANTED_ARTIFACT = "kod86-granted.txt"

PROBE_WORKFLOW_SOURCE = Path(__file__).parent / "workflows"

EVALUATIVE_CONFIGURATION = (
    "evaluative: plan mode, EVAL_TOOLS allowlist, no permission callback"
)
GENERATIVE_CONFIGURATION = "generative: plan mode, TICKET_TOOLS allowlist"

ENUMERATION_PROMPT = "Reply with the single word: ok"

# The wording the evaluator template mandates, narrowed to one dispatch.
DISPATCH_PROMPT = (
    "Send a SINGLE message containing one Agent tool call: dispatch one "
    "subagent whose entire task is to reply with the word pear. Do not do "
    "the work yourself. After the subagent returns, reply with the single "
    "word: done."
)

NAMED_INVOCATION_PROMPT = f"Run the workflow named {TRIVIAL_WORKFLOW_NAME}."
TYPED_INVOCATION_PROMPT = f"Run the workflow named {TYPED_WORKFLOW_NAME}."
BARE_TOKEN_PROMPT = "ultracode\n\nReply with the single word: ok"

TYPED_AGENT_DEFINITIONS: dict[str, AgentDefinition] = {
    BOUNDED_AGENT_TYPE: AgentDefinition(
        description="Probe subagent whose definition grants Read only",
        prompt="You are a probe subagent. Report exactly what you observe.",
        tools=["Read"],
    ),
    GRANTED_AGENT_TYPE: AgentDefinition(
        description="Probe subagent whose definition grants Read and Write",
        prompt="You are a probe subagent. Report exactly what you observe.",
        tools=["Read", WRITE_TOOL_NAME],
    ),
}


# ---------------------------------------------------------------------------
# Results ledger
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeRecord:
    """One row of the recorded results table."""

    probe: str
    question: str
    configuration: str
    observed: str
    verdict: str


RECORDS: list[ProbeRecord] = []


def record(
    *,
    probe: str,
    question: str,
    configuration: str,
    observed: str,
    verdict: str,
) -> None:
    RECORDS.append(
        ProbeRecord(
            probe=probe,
            question=question,
            configuration=configuration,
            observed=observed,
            verdict=verdict,
        )
    )


def render_table(records: list[ProbeRecord]) -> str:
    rows = [
        "",
        "KOD-86 harness capability probes -- measured results",
        "",
        "| Probe | Question | Configuration | Observed | Verdict |",
        "| --- | --- | --- | --- | --- |",
    ]
    rows.extend(
        f"| {r.probe} | {r.question} | {r.configuration} | {r.observed} | {r.verdict} |"
        for r in records
    )
    return "\n".join(rows)


@pytest.fixture(scope="module", autouse=True)
def emit_results_table(request: pytest.FixtureRequest) -> Iterator[None]:
    yield
    if not RECORDS:
        return
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    reporter.write_line(render_table(RECORDS))


# ---------------------------------------------------------------------------
# Session construction
# ---------------------------------------------------------------------------


def session_options(
    *,
    cwd: Path,
    permission_mode: str,
    allowed_tools: list[str],
    max_turns: int,
    agents: dict[str, AgentDefinition] | None = None,
) -> ClaudeAgentOptions:
    """Build a probe session carrying every field a production dispatch sets."""
    config = AppConfig()
    return ClaudeAgentOptions(
        cwd=str(cwd),
        permission_mode=permission_mode,
        allowed_tools=allowed_tools,
        disallowed_tools=STALL_GUARD_DISALLOWED_TOOLS,
        skills=map_skills(config.skills_selection()),
        setting_sources=map_setting_sources(config.setting_sources),
        max_turns=max_turns,
        agents=agents,
    )


def evaluator_options(*, cwd: Path, max_turns: int) -> ClaudeAgentOptions:
    """The exact configuration the ralph evaluator and post-merge review dispatch."""
    return session_options(
        cwd=cwd,
        permission_mode=EVAL_PERMISSION_MODE,
        allowed_tools=EVAL_TOOLS,
        max_turns=max_turns,
    )


def generative_options(*, cwd: Path, max_turns: int) -> ClaudeAgentOptions:
    """The configuration the ticket create/review sessions dispatch."""
    return session_options(
        cwd=cwd,
        permission_mode=EVAL_PERMISSION_MODE,
        allowed_tools=TICKET_TOOLS,
        max_turns=max_turns,
    )


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """Everything a probe session emitted, keyed by the event types that matter."""

    session_tools: tuple[str, ...]
    session_agents: tuple[str, ...]
    tool_uses: tuple[ToolUseEvent, ...]
    tool_results: tuple[ToolResultEvent, ...]
    tasks_started: tuple[TaskStartedEvent, ...]
    task_progress: tuple[TaskProgressEvent, ...]
    task_notifications: tuple[TaskNotificationEvent, ...]
    texts: tuple[str, ...]
    results: tuple[ResultEvent, ...]

    def used(self, *names: str) -> tuple[ToolUseEvent, ...]:
        return tuple(event for event in self.tool_uses if event.name in names)

    def executed(self, uses: tuple[ToolUseEvent, ...]) -> tuple[TaskStartedEvent, ...]:
        use_ids = {use.id for use in uses}
        return tuple(task for task in self.tasks_started if task.tool_use_id in use_ids)

    def result_text_for(self, uses: tuple[ToolUseEvent, ...]) -> tuple[str, ...]:
        use_ids = {use.id for use in uses}
        return tuple(
            str(result.content)
            for result in self.tool_results
            if result.tool_use_id in use_ids
        )


def string_list(data: dict[str, object], key: str) -> tuple[str, ...]:
    raw = data.get(key)
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw)


async def observe(*, prompt: str, options: ClaudeAgentOptions) -> Observation:
    """Run one live session and collect its typed event stream."""
    session_tools: tuple[str, ...] = ()
    session_agents: tuple[str, ...] = ()
    tool_uses: list[ToolUseEvent] = []
    tool_results: list[ToolResultEvent] = []
    tasks_started: list[TaskStartedEvent] = []
    task_progress: list[TaskProgressEvent] = []
    task_notifications: list[TaskNotificationEvent] = []
    texts: list[str] = []
    results: list[ResultEvent] = []

    async for message in query(prompt=prompt, options=options):
        for event in map_message(message):
            if isinstance(event, SystemEvent):
                if event.subtype == "init" and not session_tools:
                    session_tools = string_list(event.data, "tools")
                    session_agents = string_list(event.data, "agents")
            elif isinstance(event, ToolUseEvent):
                tool_uses.append(event)
            elif isinstance(event, ToolResultEvent):
                tool_results.append(event)
            elif isinstance(event, TaskStartedEvent):
                tasks_started.append(event)
            elif isinstance(event, TaskProgressEvent):
                task_progress.append(event)
            elif isinstance(event, TaskNotificationEvent):
                task_notifications.append(event)
            elif isinstance(event, AssistantTextEvent):
                texts.append(event.text)
            elif isinstance(event, ResultEvent):
                results.append(event)

    return Observation(
        session_tools=session_tools,
        session_agents=session_agents,
        tool_uses=tuple(tool_uses),
        tool_results=tuple(tool_results),
        tasks_started=tuple(tasks_started),
        task_progress=tuple(task_progress),
        task_notifications=tuple(task_notifications),
        texts=tuple(texts),
        results=tuple(results),
    )


@pytest.fixture()
def probe_cwd(tmp_path: Path) -> Path:
    """A disposable workspace carrying the probe's repo-owned workflow scripts."""
    workflows = tmp_path / ".claude" / "workflows"
    workflows.mkdir(parents=True)
    for script in sorted(PROBE_WORKFLOW_SOURCE.glob("*.js")):
        shutil.copy(script, workflows / script.name)
    return tmp_path


# ---------------------------------------------------------------------------
# The default-suite pin
# ---------------------------------------------------------------------------


def test_probe_config_matches_production(tmp_path: Path) -> None:
    """The probe dispatches the production objects, and drift fails here."""
    options = evaluator_options(cwd=tmp_path, max_turns=ENUMERATION_TURNS)

    assert options.permission_mode is EVAL_PERMISSION_MODE
    assert options.allowed_tools is EVAL_TOOLS
    assert options.can_use_tool is None

    assert EVAL_PERMISSION_MODE == "plan"
    assert EVAL_TOOLS == ["Read", "Glob", "Grep", "Bash"]


# ---------------------------------------------------------------------------
# Probe A -- evaluator toolset enumeration
# ---------------------------------------------------------------------------


@pytest.mark.live
async def test_probe_a_evaluator_toolset(probe_cwd: Path) -> None:
    observation = await observe(
        prompt=ENUMERATION_PROMPT,
        options=evaluator_options(cwd=probe_cwd, max_turns=ENUMERATION_TURNS),
    )

    assert observation.session_tools, "no session toolset was enumerated"

    dispatch_present = tuple(
        name for name in DISPATCH_TOOL_NAMES if name in observation.session_tools
    )
    record(
        probe="A",
        question="Does the evaluator session's actual toolset contain the Agent tool?",
        configuration=EVALUATIVE_CONFIGURATION,
        observed=(
            f"{len(observation.session_tools)} tools enumerated; "
            f"dispatch tool names present: {', '.join(dispatch_present) or 'none'}; "
            f"{WORKFLOW_TOOL_NAME} present: "
            f"{WORKFLOW_TOOL_NAME in observation.session_tools}; "
            f"subagent types: {', '.join(observation.session_agents) or 'none'}"
        ),
        verdict=(
            VERDICT_PRESENT if "Agent" in observation.session_tools else VERDICT_ABSENT
        ),
    )


# ---------------------------------------------------------------------------
# Probe B -- Agent execution under the evaluator configuration
# ---------------------------------------------------------------------------


@pytest.mark.live
async def test_probe_b_agent_execution(probe_cwd: Path) -> None:
    observation = await observe(
        prompt=DISPATCH_PROMPT,
        options=evaluator_options(cwd=probe_cwd, max_turns=DISPATCH_TURNS),
    )

    assert observation.results, "the probe session produced no result event"

    dispatch_uses = observation.used(*DISPATCH_TOOL_NAMES)
    executed = observation.executed(dispatch_uses)
    notified = observation.task_notifications

    record(
        probe="B",
        question=(
            "Does an Agent dispatch reach execution under the evaluator configuration?"
        ),
        configuration=EVALUATIVE_CONFIGURATION,
        observed=(
            f"tool_use events named {DISPATCH_TOOL_NAMES}: "
            f"{[use.name for use in dispatch_uses]}; "
            f"task_started matching those tool_use ids: {len(executed)}; "
            f"task_notification events: {len(notified)}"
        ),
        verdict=VERDICT_LIVE if dispatch_uses and executed else VERDICT_DEAD,
    )


# ---------------------------------------------------------------------------
# Probe C -- Workflow tool presence under the generative configuration
# ---------------------------------------------------------------------------


@pytest.mark.live
async def test_probe_c_workflow_tool_presence(probe_cwd: Path) -> None:
    observation = await observe(
        prompt=ENUMERATION_PROMPT,
        options=generative_options(cwd=probe_cwd, max_turns=ENUMERATION_TURNS),
    )

    assert observation.session_tools, "no session toolset was enumerated"

    present = WORKFLOW_TOOL_NAME in observation.session_tools
    record(
        probe="C",
        question="Is the Workflow tool in a headless generative session's toolset?",
        configuration=GENERATIVE_CONFIGURATION,
        observed=(
            f"{len(observation.session_tools)} tools enumerated; "
            f"{WORKFLOW_TOOL_NAME} in toolset: {present}; "
            f"{WORKFLOW_TOOL_NAME} in the allowlist: "
            f"{WORKFLOW_TOOL_NAME in TICKET_TOOLS}"
        ),
        verdict=VERDICT_PRESENT if present else VERDICT_ABSENT,
    )


# ---------------------------------------------------------------------------
# Probe D -- named-workflow invocation
# ---------------------------------------------------------------------------


@pytest.mark.live
async def test_probe_d_named_workflow_invocation(probe_cwd: Path) -> None:
    plan_mode = await observe(
        prompt=NAMED_INVOCATION_PROMPT,
        options=session_options(
            cwd=probe_cwd,
            permission_mode=EVAL_PERMISSION_MODE,
            allowed_tools=WORKFLOW_ALLOWED_TOOLS,
            max_turns=WORKFLOW_TURNS,
        ),
    )
    ungated = await observe(
        prompt=NAMED_INVOCATION_PROMPT,
        options=session_options(
            cwd=probe_cwd,
            permission_mode=UNGATED_PERMISSION_MODE,
            allowed_tools=WORKFLOW_ALLOWED_TOOLS,
            max_turns=WORKFLOW_TURNS,
        ),
    )

    assert plan_mode.results, "the plan-mode probe session produced no result event"
    assert ungated.results, "the ungated probe session produced no result event"

    plan_uses = plan_mode.used(WORKFLOW_TOOL_NAME)
    ungated_uses = ungated.used(WORKFLOW_TOOL_NAME)
    plan_launched = tuple(
        task for task in plan_mode.tasks_started if task.task_type == WORKFLOW_TASK_TYPE
    )
    launched = tuple(
        task for task in ungated.tasks_started if task.task_type == WORKFLOW_TASK_TYPE
    )
    statuses = tuple(note.status for note in ungated.task_notifications)

    record(
        probe="D",
        question="Does a repo-owned named workflow fire from a headless SDK session?",
        configuration=(
            "matched pair, permission mode varied: "
            f"plan vs {UNGATED_PERMISSION_MODE}; "
            f"TICKET_TOOLS + {WORKFLOW_TOOL_NAME} allowlist in both"
        ),
        observed=(
            f"plan mode: {len(plan_uses)} {WORKFLOW_TOOL_NAME} tool_use events, "
            f"{len(plan_launched)} workflow task_started; "
            f"{UNGATED_PERMISSION_MODE} mode: {len(ungated_uses)} "
            f"{WORKFLOW_TOOL_NAME} tool_use events, {len(launched)} workflow "
            f"task_started, notification statuses: {list(statuses) or 'none'}"
        ),
        verdict=VERDICT_LIVE if ungated_uses and launched else VERDICT_DEAD,
    )


# ---------------------------------------------------------------------------
# Probe E -- agentType resolution and subagent bounding
# ---------------------------------------------------------------------------


@pytest.mark.live
async def test_probe_e_agent_type_and_bounding(probe_cwd: Path) -> None:
    observation = await observe(
        prompt=TYPED_INVOCATION_PROMPT,
        options=session_options(
            cwd=probe_cwd,
            permission_mode=UNGATED_PERMISSION_MODE,
            allowed_tools=WORKFLOW_WRITE_ALLOWED_TOOLS,
            max_turns=WORKFLOW_TURNS,
            agents=TYPED_AGENT_DEFINITIONS,
        ),
    )

    assert observation.results, "the probe session produced no result event"

    definitions_offered = tuple(
        name for name in TYPED_AGENT_DEFINITIONS if name in observation.session_agents
    )
    stage_labels = tuple(
        sorted({event.description for event in observation.task_progress})
    )
    bounded_wrote = (probe_cwd / BOUNDED_ARTIFACT).exists()
    granted_wrote = (probe_cwd / GRANTED_ARTIFACT).exists()

    record(
        probe="E",
        question=(
            "Does workflow agentType resolve session-supplied definitions, and "
            "does a definition's tool list bound the subagent?"
        ),
        configuration=(
            f"{UNGATED_PERMISSION_MODE} mode, TICKET_TOOLS + "
            f"{WORKFLOW_TOOL_NAME} + {WRITE_TOOL_NAME} allowlist, two typed "
            "definitions supplied through session options"
        ),
        observed=(
            f"definitions offered to the session: "
            f"{', '.join(definitions_offered) or 'none'}; "
            f"workflow stages observed: {', '.join(stage_labels) or 'none'}; "
            f"Read-only definition produced its file: {bounded_wrote}; "
            f"Write-granted definition produced its file: {granted_wrote}"
        ),
        verdict=(
            VERDICT_LIVE
            if definitions_offered and granted_wrote and not bounded_wrote
            else VERDICT_DEAD
        ),
    )


# ---------------------------------------------------------------------------
# Probe F -- the origin gate, negative and positive
# ---------------------------------------------------------------------------


@pytest.mark.live
async def test_probe_f_origin_gate(probe_cwd: Path) -> None:
    negative = await observe(
        prompt=BARE_TOKEN_PROMPT,
        options=session_options(
            cwd=probe_cwd,
            permission_mode=UNGATED_PERMISSION_MODE,
            allowed_tools=WORKFLOW_ALLOWED_TOOLS,
            max_turns=WORKFLOW_TURNS,
        ),
    )
    positive = await observe(
        prompt=NAMED_INVOCATION_PROMPT,
        options=session_options(
            cwd=probe_cwd,
            permission_mode=UNGATED_PERMISSION_MODE,
            allowed_tools=WORKFLOW_ALLOWED_TOOLS,
            max_turns=WORKFLOW_TURNS,
        ),
    )

    assert negative.results, "the bare-token probe session produced no result event"
    assert positive.results, (
        "the named-invocation probe session produced no result event"
    )

    negative_uses = negative.used(WORKFLOW_TOOL_NAME)
    positive_uses = positive.used(WORKFLOW_TOOL_NAME)

    record(
        probe="F",
        question=(
            "Does a bare ultracode token in an SDK-sent prompt start a workflow, "
            "while the named invocation does?"
        ),
        configuration=(
            f"identical sessions, prompt varied: {UNGATED_PERMISSION_MODE} mode, "
            f"TICKET_TOOLS + {WORKFLOW_TOOL_NAME} allowlist"
        ),
        observed=(
            f"bare token: {len(negative_uses)} {WORKFLOW_TOOL_NAME} tool_use "
            f"events, {len(negative.tasks_started)} task_started; "
            f"named invocation: {len(positive_uses)} {WORKFLOW_TOOL_NAME} "
            f"tool_use events, {len(positive.tasks_started)} task_started"
        ),
        verdict=(VERDICT_LIVE if positive_uses and not negative_uses else VERDICT_DEAD),
    )
