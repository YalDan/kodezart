"""Independent production-boundary counterexamples; only external facts change."""

import json
import re

import pytest

from kodezart.domain.errors import OrganizeWriteRefusalError, StaleWriteError
from tests.chains.test_organize import result
from tests.chains.test_organize_owner import factory, run_owner
from tests.tracker.conftest import CLAIMED_ISSUE


@pytest.mark.parametrize("amend_parent", [False, True])
async def test_criterion_mint_requires_the_parent_revision_its_author_read(
    monkeypatch, amend_parent
):
    owner, board, executor = factory()
    original = executor.stream
    criterion_inputs = []

    async def changed_parent(**kwargs):
        async for event in original(**kwargs):
            if (
                kwargs["output_format"]["schema"].get("title") == "OrganizeProposal"
                and event.structured_output.get("kind") == "criteria"
            ):
                criterion_inputs.append(kwargs["prompt"])
                if amend_parent:
                    board.server.issues[
                        CLAIMED_ISSUE
                    ].description = "The revised source requires a different criterion."
            yield event

    monkeypatch.setattr(executor, "stream", changed_parent)
    try:
        await run_owner(owner)
    except (OrganizeWriteRefusalError, StaleWriteError):
        assert amend_parent
    assert len(criterion_inputs) == 1
    assert "Prepared body grounded in the source." in criterion_inputs[0]
    creates = [
        arguments
        for name, arguments in board.calls
        if name == "save_issue" and "parentId" in arguments and "id" not in arguments
    ]
    assert len(creates) == (0 if amend_parent else 1)


async def test_writeback_exhaustion_retains_its_actual_cited_refutation(monkeypatch):
    owner, _board, executor = factory(bound=1)
    original = executor.stream
    checked_bad_body = []

    async def refute_landed_body(**kwargs):
        async for event in original(**kwargs):
            if kwargs["output_format"]["schema"].get("title") == "WriteBackFinding":
                artifact = json.loads(
                    re.search(
                        r"<written_artifact>\s*(.*?)\s*</written_artifact>",
                        kwargs["prompt"],
                        re.S,
                    )[1]
                )
                if artifact["content"] == "Prepared body grounded in the source.":
                    checked_bad_body.append(artifact)
                    event = result(
                        structured_output={
                            "verdict": "refuted",
                            "evidence": (
                                "tests/absent.py is absent at this exact commit."
                            ),
                            "cited_refs": ["tests/absent.py"],
                        }
                    )
            yield event

    monkeypatch.setattr(executor, "stream", refute_landed_body)
    report = await run_owner(owner)
    assert len(checked_bad_body) == 1
    assert report.halt.cause == "admission_exhausted"
    assert report.halt.bound.loop == "write_back"
    assert report.halt.bound.rounds_used == report.halt.bound.value == 1
    assert "tests/absent.py" in report.halt.model_dump_json()


@pytest.mark.parametrize("approve_during_assessment", [False, True])
async def test_scope_approval_ends_organize_before_escalation_writes(
    monkeypatch, approve_during_assessment
):
    owner, board, executor = factory()
    original = executor.stream
    assessed = []

    async def human_decision(**kwargs):
        async for event in original(**kwargs):
            if kwargs["output_format"]["schema"].get("title") == "AdmissionJudgment":
                assessed.append(kwargs["prompt"])
                event = result(
                    structured_output={
                        "issue_id": CLAIMED_ISSUE,
                        "verdict": "not_buildable",
                        "invented_decision": "Choose the authoritative source version.",
                        "evidence": "Two current sources conflict.",
                        "refusal_kind": "human_decision",
                    }
                )
                if approve_during_assessment:
                    board.server.issues[CLAIMED_ISSUE].labels.append("approved scope")
            yield event

    monkeypatch.setattr(executor, "stream", human_decision)
    try:
        await run_owner(owner)
    except OrganizeWriteRefusalError:
        assert approve_during_assessment
    assert len(assessed) == 1
    writes = [name for name, _ in board.calls if name.startswith("save_")]
    assert bool(writes) is (not approve_during_assessment)
