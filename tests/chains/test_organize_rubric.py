"""Native rubric suppliers and admission roles meet at the production factory.

Transport tests supply no model verdict. Refusal tests separately inject typed
judgments to exercise convergence; they do not claim to evaluate an LLM's grading.
"""

import tomllib
from dataclasses import replace

import pytest

from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.composition.organize import build_organize_owner
from kodezart.core.config import AppConfig
from kodezart.core.errors import NoStructuredOutputError, PromptResolutionError
from kodezart.core.organize_settings import OrganizeSettings
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.prompts import PromptKey, PromptSetMetadata
from kodezart.types.domain.session import SessionType
from tests.chains.test_organize import RecordingExecutor, RecordingWorkspace, result
from tests.chains.test_organize_owner import run_owner
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeGitService, PassThroughGate
from tests.prompts.test_organize_mandate_bindings import declared_operation
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_run_surface_lease import _Board
from tests.tracker.conftest import CLAIMED_ISSUE
from tests.tracker.test_linear_mcp_tracker import tracker_over


def native_fields():
    fields = declared_operation().model_dump()
    fields["issue_labels"]["decision"] = "needs decision"
    fields["marker_prefixes"]["escalation"] = "organize-question"
    rubric_keys = {
        "groom": "grooming_pass",
        "ticket": "ticket_review",
        "criteria": "criteria_validation",
    }
    for phase in fields["organize_mandates"]:
        phase["rubric_prompt_key"] = rubric_keys[phase["kind"]]
        phase["admission_prompt_key"] = "organize_assess"
    return fields


def build(
    *, prompt_set="claude-opus", fields=None, executor=None, changes=None, body=None
):
    board = _Board()
    operation = OperationConfig.model_validate(fields or native_fields())
    parent = board.server.issues[CLAIMED_ISSUE]
    parent.description = body or "Current native organizational evidence."
    parent.labels = ["candidate scope"]
    tracker = tracker_over(
        board.server,
        caller=board,
        clock=lambda: board.now,
        issue_labels=operation.issue_labels,
        scope_labels=operation.scope_labels,
    )
    executor = executor if executor is not None else RecordingExecutor([])
    workspace = RecordingWorkspace()
    registry = load_registry(
        default_set=prompt_set, bindings=operation_bindings(operation)
    )
    if changes is not None:
        templates = {key: registry.template_for(key) for key in PromptKey}
        for key, updates in changes.items():
            templates[key] = replace(templates[key], **updates)
        registry = InRepoPromptRegistry(
            templates=templates,
            default_metadata=PromptSetMetadata.model_validate(
                tomllib.loads(
                    (default_sets_root() / prompt_set / "set.toml").read_text()
                )
            ),
        )
    owner = build_organize_owner(
        config=AppConfig(
            organize=OrganizeSettings(max_admission_rounds=1, max_convergence_rounds=1),
            write_back={"max_verify_rounds": 1},
        ),
        operation=operation,
        tracker=tracker,
        runner=AgentService(
            executor=executor,
            workspace=workspace,
            git_base_url="https://example.invalid",
        ),
        workspace=workspace,
        git=FakeGitService(),
        prompts=registry,
        skills=SUPPRESS_ALL_SKILLS,
        gate=PassThroughGate(),
        repo_url="https://example.invalid/repository",
    )
    return owner, board, executor, registry


@pytest.mark.parametrize("prompt_set", ["claude-opus", "anthropic_v5"])
async def test_real_factory_dispatches_declared_groom_rubric_with_native_inputs(
    prompt_set,
):
    owner, board, executor, registry = build(prompt_set=prompt_set)
    with pytest.raises(NoStructuredOutputError):
        await run_owner(owner)
    assert len(executor.calls) == 1
    call = executor.calls[0]
    rubric = registry.template_for(PromptKey.GROOMING_PASS).rubric_template().render({})
    assert f"<mandate_rubric>\n{rubric}\n</mandate_rubric>" in call["prompt"]
    assert "Current native organizational evidence." in call["prompt"]
    assert "{{" not in call["prompt"]
    assert call["output_format"]["schema"]["title"] == "AdmissionJudgment"
    assert call["session_type"] is SessionType.ORGANIZE_PASS
    assert call["session_id"] is None
    assert board.server.issues[CLAIMED_ISSUE].labels == ["candidate scope"]
    assert not [(name, args) for name, args in board.calls if name.startswith("save_")]


@pytest.mark.parametrize("key", ["ticket_review", "commit_message"])
def test_boot_rejects_incompatible_native_admission_roles(key):
    fields = native_fields()
    fields["organize_mandates"][0]["admission_prompt_key"] = key
    with pytest.raises(PromptResolutionError, match=key):
        build(fields=fields)


def test_boot_rejects_key_without_a_declared_rubric_supplier():
    fields = native_fields()
    fields["organize_mandates"][0]["rubric_prompt_key"] = "organize_assess"
    with pytest.raises(PromptResolutionError, match="organize_assess"):
        build(fields=fields)


@pytest.mark.parametrize(
    "body, missing",
    [
        ("Read {{task}} and {{draft_md}}.", "draft_md, task"),
        ("{{#if unavailable}}{{unavailable}}{{/if}}", "unavailable"),
        ("{{mandate_rubric}}", "own per-call binding"),
        ("{{issue_body.draft_md}}", "issue_body.draft_md"),
    ],
)
def test_boot_rejects_unavailable_rubric_inputs(body, missing):
    with pytest.raises(PromptResolutionError, match=missing):
        build(changes={PromptKey.GROOMING_PASS: {"rubric_body": body}})


def test_boot_rejects_unavailable_native_role_input():
    with pytest.raises(PromptResolutionError, match="draft_md"):
        build(changes={PromptKey.ORGANIZE_ASSESS: {"body": "{{draft_md}}"}})


async def test_rubric_native_inputs_are_rendered_from_each_fresh_issue_read():
    owner, board, executor, _ = build(
        changes={
            PromptKey.GROOMING_PASS: {"rubric_body": "Current source: {{issue_body}}"}
        }
    )
    for body in ("First native body", "Fresh changed native body"):
        board.server.issues[CLAIMED_ISSUE].description = body
        with pytest.raises(NoStructuredOutputError):
            await run_owner(owner)
        assert (
            f"<mandate_rubric>\nCurrent source: {body}\n</mandate_rubric>"
            in executor.calls[-1]["prompt"]
        )
    assert len(executor.calls) == 2


ORGANIZATIONAL_EVIDENCE = {
    "blockers": (
        "An issue is blocked by work in another project without an ownership edge."
    ),
    "decisions": (
        "The documented API choice is open and has not been asked of its owner."
    ),
    "dates_order": (
        "The deadline and priority conflict with the recorded dependency order."
    ),
    "measurable_goal": (
        "The issue ends with 'improve it' and has no observable completion condition."
    ),
}


class UnrepairedDefectExecutor:
    """Typed response oracles keep one defect present through the repair round."""

    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = []

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        outputs = {
            "AdmissionJudgment": {
                "issue_id": CLAIMED_ISSUE,
                "verdict": "not_buildable",
                "evidence": self.evidence,
                "refusal_kind": "spec_gap",
                "invented_decision": self.evidence,
            },
            "OrganizeProposal": {
                "kind": "body",
                "issue_id": CLAIMED_ISSUE,
                "body": self.evidence,
            },
            "WriteBackFinding": {
                "verdict": "holds",
                "evidence": "The unchanged body still records the same defect.",
                "cited_refs": [],
            },
        }
        yield result(
            structured_output=outputs[kwargs["output_format"]["schema"]["title"]]
        )


@pytest.mark.parametrize("defect", ORGANIZATIONAL_EVIDENCE)
async def test_each_organizational_refusal_prevents_groom_convergence(defect):
    evidence = ORGANIZATIONAL_EVIDENCE[defect]
    # A typed grader response is an input oracle for the owner. No prompt-text
    # classifier or fake model judgment is used to derive it.
    owner, board, executor, _ = build(
        executor=UnrepairedDefectExecutor(evidence), body=evidence
    )
    report = await run_owner(owner)
    assert report.halt is not None
    assert report.halt.cause == "admission_exhausted"
    assert report.completed_phases == ()
    assert "graph complete" not in board.server.issues[CLAIMED_ISSUE].labels
    assert "approved scope" not in board.server.issues[CLAIMED_ISSUE].labels
    assert executor.calls
    assert all(evidence in call["prompt"] for call in executor.calls)
    assert {call["output_format"]["schema"]["title"] for call in executor.calls} == {
        "AdmissionJudgment",
        "OrganizeProposal",
        "WriteBackFinding",
    }
