"""KOD-89-AC-7 — the workflow primitive, measured end to end.

Live only.  The criterion asks for a generative session with the WORKFLOW
primitive selected, which is not the primitive the enumeration selected for
production (fire-time ruling FR-5), so this probe reports what it measured
**under stated conditions** rather than as a capability the shipped
configuration has: it supplies the two session fields the enumeration
measured as necessary — a non-plan permission mode and the workflow tool in
the allowlist — and records both alongside the result.

What is under test is the whole path: the v5 template rendered with the
workflow fragment, the repo-owned script it names by name, the typed lens
definitions the set declares, and the counted report coming back.
"""

import shutil
from pathlib import Path

import pytest

from kodezart.adapters._agents_mapping import map_agents
from kodezart.types.domain.prompts import OrchestrationPrimitive, PromptKey
from tests.probes.recording import record
from tests.probes.test_harness_capabilities import (
    UNGATED_PERMISSION_MODE,
    VERDICT_LIVE,
    WORKFLOW_ALLOWED_TOOLS,
    WORKFLOW_TASK_TYPE,
    WORKFLOW_TOOL_NAME,
    WORKFLOW_TURNS,
    observe,
    session_options,
)
from tests.prompts.test_claude_opus_goldens import V5_SET
from tests.prompts.test_v5_orchestration import set_with_primitive

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIPPED_WORKFLOW = REPO_ROOT / ".claude" / "workflows" / "kodezart-investigate.js"

#: Three questions with answers that exist in the probe workspace, so the
#: measurement is of the fan-out and not of the investigators' luck.
QUESTIONS = (
    "What is the entire contents of note-one.txt in this directory?",
    "What is the entire contents of note-two.txt in this directory?",
    "What is the entire contents of note-three.txt in this directory?",
)
NOTES = {
    "note-one.txt": "alpha",
    "note-two.txt": "beta",
    "note-three.txt": "gamma",
}

TASK = (
    "Draft acceptance criteria for a change that renames the three notes in "
    "this directory. Your open questions, one per agent, are:\n"
    + "\n".join(f"- {question}" for question in QUESTIONS)
)


@pytest.fixture()
def workflow_cwd(tmp_path: Path) -> Path:
    """A disposable workspace carrying the SHIPPED workflow and its answers."""
    workflows = tmp_path / ".claude" / "workflows"
    workflows.mkdir(parents=True)
    shutil.copy(SHIPPED_WORKFLOW, workflows / SHIPPED_WORKFLOW.name)
    for name, content in NOTES.items():
        (tmp_path / name).write_text(f"{content}\n", encoding="utf-8")
    return tmp_path


@pytest.mark.live
async def test_probe_workflow_primitive_dispatches_and_counts(
    workflow_cwd: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The rendered prompt names the workflow, and the report counts what it sent."""
    registry = set_with_primitive(
        tmp_path_factory.mktemp("v5-workflow"),
        OrchestrationPrimitive.WORKFLOW,
    )
    prompt = registry.template_for(PromptKey.ACCEPTANCE_CRITERIA).render(
        {
            "task_description": TASK,
            "validation_findings": None,
            "base_ref": "main",
            "skills_reference": "",
        },
    )
    assert "kodezart-investigate" in prompt

    observed = await observe(
        prompt=prompt,
        options=session_options(
            cwd=workflow_cwd,
            permission_mode=UNGATED_PERMISSION_MODE,
            allowed_tools=WORKFLOW_ALLOWED_TOOLS,
            max_turns=WORKFLOW_TURNS,
            agents=map_agents(registry.definitions()),
        ),
    )

    uses = observed.used(WORKFLOW_TOOL_NAME)
    launched = tuple(
        task for task in observed.tasks_started if task.task_type == WORKFLOW_TASK_TYPE
    )
    returned = " ".join(
        (*observed.result_text_for(uses), *(text for text in observed.texts))
    )
    counted = f"{len(QUESTIONS)} questions answered" in returned or (
        f'"dispatched": {len(QUESTIONS)}' in returned
    )

    record(
        probe="KOD-89-AC-7",
        question=(
            "Does a generative session with the workflow primitive emit a "
            "Workflow tool use, and does the report's dispatched count equal "
            "the questions sent?"
        ),
        configuration=(
            f"generative, {V5_SET} rendered with the workflow primitive; "
            f"{UNGATED_PERMISSION_MODE} permission mode and "
            f"{WORKFLOW_TOOL_NAME} in the allowlist — the two fields the "
            "enumeration measured as necessary, neither of which the "
            "production session configuration carries"
        ),
        observed=(
            f"{WORKFLOW_TOOL_NAME} tool_use events: {len(uses)}; "
            f"workflow task_started: {len(launched)}; "
            f"questions sent: {len(QUESTIONS)}; "
            f"count reported back: {counted}"
        ),
        verdict=VERDICT_LIVE if uses and launched else "dead",
    )

    assert observed.results, "the probe session produced no result event"
    assert uses, "no Workflow tool use was emitted"
    assert launched, "the Workflow tool use never reached execution"
    assert counted, (
        "the run reported no count over the questions dispatched: the "
        "criterion's second clause is the fan-in coming back counted, not "
        "the tool merely firing"
    )
