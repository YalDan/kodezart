"""Independent native supplier boundary probes; judgments are transport inputs."""

import pytest

from kodezart.core.errors import (
    NoStructuredOutputError,
    PromptResolutionError,
)
from kodezart.types.domain.prompts import PromptKey
from tests.chains.test_organize import result
from tests.chains.test_organize_owner import run_owner
from tests.chains.test_organize_rubric import build
from tests.tracker.conftest import CLAIMED_ISSUE


class RepairThenStop:
    def __init__(self):
        self.calls = []

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        schema = kwargs["output_format"]["schema"]["title"]
        if schema == "AdmissionJudgment":
            if (
                sum(c["output_format"]["schema"]["title"] == schema for c in self.calls)
                > 1
            ):
                return
            payload = {
                "issue_id": CLAIMED_ISSUE,
                "verdict": "not_buildable",
                "evidence": "The issue lacks a measurable completion condition.",
                "refusal_kind": "spec_gap",
                "invented_decision": "Specify the observable completion condition.",
            }
        elif schema == "OrganizeProposal":
            payload = {
                "kind": "body",
                "issue_id": CLAIMED_ISSUE,
                "body": "Fresh measurable goal after repair.",
            }
        else:
            assert schema == "WriteBackFinding"
            payload = {
                "verdict": "holds",
                "evidence": "The requested body was written.",
                "cited_refs": [],
            }
        yield result(structured_output=payload)


@pytest.mark.parametrize("prompt_set", ["claude-opus", "anthropic_v5"])
async def test_dynamic_rubric_uses_repaired_body_at_fresh_verify(prompt_set):
    executor = RepairThenStop()
    owner, board, _, _ = build(
        prompt_set=prompt_set,
        executor=executor,
        body="Original vague activity.",
        changes={
            PromptKey.GROOMING_PASS: {
                "rubric_body": "Grade this current goal: {{issue_body}}"
            }
        },
    )
    with pytest.raises(NoStructuredOutputError):
        await run_owner(owner)
    assert board.server.issues[CLAIMED_ISSUE].description == (
        "Fresh measurable goal after repair."
    )
    verify = executor.calls[-1]
    assert (
        "<issue_body>\nFresh measurable goal after repair.\n</issue_body>"
        in (verify["prompt"])
    )
    assert (
        "<mandate_rubric>\nGrade this current goal: "
        "Fresh measurable goal after repair.\n</mandate_rubric>" in verify["prompt"]
    )


@pytest.mark.parametrize(
    "body",
    ["{{refusal_evidence}}", "{{#each issue_body}}{{this}}{{/each}}"],
)
def test_factory_rejects_native_binding_shapes_that_cannot_render(body):
    with pytest.raises(PromptResolutionError):
        build(changes={PromptKey.GROOMING_PASS: {"rubric_body": body}})


async def test_conditional_absent_evidence_and_native_sequence_remain_valid():
    owner, _, executor, _ = build(
        changes={
            PromptKey.GROOMING_PASS: {
                "rubric_body": (
                    "Grade the current source."
                    "{{#if refusal_evidence}}{{refusal_evidence}}{{/if}}"
                    "{{#each linked_issue_bodies}}{{this}}{{/each}}"
                )
            }
        }
    )
    with pytest.raises(NoStructuredOutputError):
        await run_owner(owner)
    assert len(executor.calls) == 1
    assert (
        "<mandate_rubric>\nGrade the current source.\n</mandate_rubric>"
        in (executor.calls[0]["prompt"])
    )
