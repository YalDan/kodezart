"""Actual authored consumers retain the dispatch-base prompt bytes."""

import hashlib
import json
from pathlib import Path

import pytest

from kodezart.chains import (
    authored_delivery,
    delivery_coordinator,
    ralph_workflow,
    remediation,
)
from kodezart.domain.ticket import format_fire_spec
from kodezart.types.domain.criteria import ConjunctionVerdict, CriteriaArtifact
from kodezart.types.domain.fire_spec import AuthoredSpec
from kodezart.types.domain.gating import RepoVisibility
from tests.chains.test_delivery_runtime import context, deliver, description, setup
from tests.chains.test_ralph_workflow import _make_engine
from tests.chains.test_remediation import _chain, _request, _ticket_result
from tests.domain.test_fire_spec_formatter import tickets
from tests.fakes import (
    FakeAgentRunner,
    FakePRCreator,
    FakeQualityGate,
    RecordingPromptProvider,
    make_passing_evaluation,
)
from tests.prompts.sets import OPUS_SET, V5_SET
from tests.prompts.test_prompt_wiring import load_registry

GOLDENS = Path(__file__).with_name("fire_spec_prompt_goldens.json")
CORPUS = list(tickets())


async def capture_prompts(family, ticket, monkeypatch):
    provider = RecordingPromptProvider(load_registry(default_set=family))
    facts = context(spec=AuthoredSpec(ticket=ticket))
    quality_gate = FakeQualityGate(events=[], evaluation=make_passing_evaluation())
    engine = _make_engine(
        quality_gate=quality_gate, pr_creator=FakePRCreator(), prompts=provider
    )
    runner = FakeAgentRunner([description()])
    engine._service = runner
    monkeypatch.setattr(ralph_workflow, "get_stream_writer", lambda: lambda _: None)
    monkeypatch.setattr(authored_delivery, "get_stream_writer", lambda: lambda _: None)
    state = {
        "issue_key": "subject/42",
        "ticket": ticket,
        "remediation_ticket": None,
        "feature_branch": "feature",
        "ralph_branch": "loop",
        "work_base_ref": "selected-base",
        "feature_tip_sha": "a" * 40,
        "criteria_artifact": CriteriaArtifact(
            criteria=list(facts.criteria),
            conjunction=ConjunctionVerdict(satisfiable=True),
        ),
        "total_iterations": 7,
        "repo_visibility": RepoVisibility.PUBLIC,
        "flagged_items": [],
    }
    config = {"configurable": facts.execution.model_dump()}
    await engine._run_ralph_loop_node(state, config)
    await engine._open_pr_node(state, config)

    fix_runner = FakeAgentRunner([_ticket_result()])
    request = _request().model_copy(update={"original_ticket": ticket})
    _ = [
        event
        async for event in _chain(fix_runner, provider).run(
            request, repo_path="/checkout", repo_url=None, cache_key="fix"
        )
    ]
    delivery = setup(family=family)
    await deliver(delivery.coordinator, facts=facts)
    return {
        "implementation": quality_gate.calls[0]["prompt"],
        "fix": fix_runner.calls[0]["prompt"],
        "workflow_pr": runner.calls[0]["prompt"],
        "delivery_pr": delivery.runner.calls[0]["prompt"],
    }


@pytest.mark.parametrize("family", [V5_SET, OPUS_SET])
@pytest.mark.parametrize("index", range(len(CORPUS)))
async def test_real_authored_consumer_prompts_match_recorded_base(
    family, index, monkeypatch
):
    golden = json.loads(GOLDENS.read_text())
    assert golden["source_commit"] == "51d83d5"
    calls = []

    def formatted(spec):
        calls.append(spec)
        return format_fire_spec(spec)

    for module in (
        ralph_workflow,
        authored_delivery,
        remediation,
        delivery_coordinator,
    ):
        monkeypatch.setattr(module, "format_fire_spec", formatted)
    actual = await capture_prompts(family, CORPUS[index], monkeypatch)
    assert len(calls) == 4
    assert all(isinstance(spec, AuthoredSpec) for spec in calls)
    assert all(spec.ticket == CORPUS[index] for spec in calls)
    assert {
        name: hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        for name, prompt in actual.items()
    } == golden["prompts"][family][str(index)]
