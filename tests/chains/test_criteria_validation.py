"""The criteria feasibility gate, exercised through the workflow graph.

Every assertion here is about what the RUN does, not about what the sweep
function returns: whether the loop was reached, which criteria were handed
to it, how many regeneration rounds were spent, and what the completion
event says happened.
"""

from collections.abc import AsyncGenerator

import pytest

from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.core.config import AppConfig
from kodezart.domain.errors import CriteriaFanInError
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import (
    AgentEvent,
    ResultEvent,
    WorkflowCompleteEvent,
    WorkflowCriteriaEvent,
    WorkflowCriteriaValidationEvent,
)
from kodezart.types.domain.criteria import (
    CriteriaArtifact,
    CriterionClassification,
    FeasibilityVerdict,
    LimitArm,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from kodezart.types.domain.skills import SkillsSelection
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeChangePersister,
    FakeGitService,
    FakeQualityGate,
    FakeRepoCache,
    FakeTicketGenerator,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_passing_evaluation,
    make_prompt_provider,
)

INFEASIBLE_A = {
    "criterionId": "AC-1",
    "smallestRepair": "criterion_text",
    "refutation": (
        "the package's lint boundary forbids `app.api` from exporting `Foo` "
        "to any consumer, so no implementation can satisfy this at base"
    ),
}
UNVERIFIABLE_B = {
    "criterionId": "AC-2",
    "smallestRepair": "environment_supply",
    "missingResource": "a PostgreSQL server reachable from the runner",
}
FEASIBLE_A = {"criterionId": "AC-1", "smallestRepair": "none"}
FEASIBLE_B = {"criterionId": "AC-2", "smallestRepair": "none"}


class ValidatorScriptExecutor:
    """Answers every workflow schema; scripts the validator round by round."""

    def __init__(self, sweeps: list[dict[str, object]]) -> None:
        self._sweeps = list(sweeps)
        self.sweep_calls = 0
        self.criteria_calls = 0
        self.prompts: list[str] = []

    async def stream(
        self,
        *,
        prompt: str,
        cwd: str,
        permission_mode: str,
        allowed_tools: list[str],
        skills: SkillsSelection = SUPPRESS_ALL_SKILLS,
        session_id: str | None = None,
        output_format: dict[str, object] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        self.prompts.append(prompt)
        props: dict[str, object] = {}
        if output_format is not None:
            schema = output_format.get("schema")
            if isinstance(schema, dict):
                raw = schema.get("properties", {})
                if isinstance(raw, dict):
                    props = raw

        structured: dict[str, object]
        if "slug" in props:
            structured = {"slug": "gated"}
        elif "findings" in props:
            self.sweep_calls += 1
            structured = self._sweeps.pop(0)
        elif "criteria" in props and "criteriaResults" not in props:
            self.criteria_calls += 1
            structured = {
                "criteria": [
                    {
                        "text": "`Foo` is importable from `app.api`.",
                        "classification": "hard_gate",
                    },
                    {
                        "text": (
                            "A record round-trips through the store "
                            'preserving its  "id" verbatim.'
                        ),
                        "classification": "hard_gate",
                    },
                ],
                "reasoning": "Generated from codebase analysis.",
            }
        elif "criteriaResults" in props:
            structured = {
                "criteriaResults": [
                    {
                        "criterionId": cid,
                        "criterion": "echo",
                        "passed": True,
                        "reasoning": "review passed",
                    }
                    for cid in ("AC-1", "AC-2")
                ],
            }
        elif "title" in props and "description" in props:
            structured = {"title": "feat: t", "description": "d"}
        else:
            structured = {}

        yield ResultEvent(
            subtype="result",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="validator-script",
            structured_output=structured or None,
        )


def _engine(
    executor: ValidatorScriptExecutor,
    *,
    max_rounds: int = 1,
    quality_gate: FakeQualityGate | None = None,
    artifact_persister: FakeArtifactPersister | None = None,
) -> RalphWorkflowEngine:
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    return RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=make_prompt_provider(),
        service=service,
        quality_gate=quality_gate
        or FakeQualityGate(
            events=[],
            evaluation=make_passing_evaluation(),
            last_commit_sha="a" * 40,
        ),
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        criteria_regeneration_max_rounds=max_rounds,
        artifact_persister=artifact_persister,
    )


async def _run(engine: RalphWorkflowEngine) -> list[AgentEvent]:
    return [
        event
        async for event in engine.run(
            prompt="do the thing",
            repo_path="/tmp/fake",
            repo_url=None,
            base_branch="main",
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key="criteria-gate",
        )
    ]


# ---------------------------------------------------------------------------
# The bound is an AppConfig field, not a literal
# ---------------------------------------------------------------------------


def test_regeneration_bound_is_a_named_config_field() -> None:
    """`KODEZART_CRITERIA_REGENERATION_MAX_ROUNDS`, never a bare number."""
    field = AppConfig.model_fields["criteria_regeneration_max_rounds"]
    assert field.annotation is int
    assert AppConfig().criteria_regeneration_max_rounds >= 0


def test_regeneration_bound_reads_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KODEZART_CRITERIA_REGENERATION_MAX_ROUNDS", "3")
    assert AppConfig().criteria_regeneration_max_rounds == 3


# ---------------------------------------------------------------------------
# The permanent-infeasibility halt
# ---------------------------------------------------------------------------


async def test_permanently_infeasible_criteria_halt_before_the_loop() -> None:
    """The run terminates pre-loop with the sweep as its report."""
    executor = ValidatorScriptExecutor(
        sweeps=[
            {"findings": [INFEASIBLE_A, FEASIBLE_B], "contradictions": []},
            {"findings": [INFEASIBLE_A, FEASIBLE_B], "contradictions": []},
        ],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        last_commit_sha="a" * 40,
    )
    events = await _run(_engine(executor, max_rounds=1, quality_gate=gate))

    assert gate.calls == [], "run_ralph_loop must not be reached"
    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.criteria_infeasible
    assert complete.accepted is False
    assert complete.total_iterations == 0

    # The sweep verdicts ride the report payload.
    assert complete.criteria_validation is not None
    verdicts = {v.criterion_id: v for v in complete.criteria_validation.verdicts}
    assert verdicts["AC-1"].verdict is FeasibilityVerdict.infeasible
    assert verdicts["AC-1"].refutation is not None
    assert "lint boundary" in verdicts["AC-1"].refutation


async def test_the_bound_is_spent_before_the_halt_not_at_the_first_sweep() -> None:
    """One regeneration round is attempted, then the run halts."""
    executor = ValidatorScriptExecutor(
        sweeps=[
            {"findings": [INFEASIBLE_A, FEASIBLE_B], "contradictions": []},
            {"findings": [INFEASIBLE_A, FEASIBLE_B], "contradictions": []},
        ],
    )
    events = await _run(_engine(executor, max_rounds=1))

    assert executor.criteria_calls == 2, "one regeneration round was spent"
    assert executor.sweep_calls == 2
    sweeps = [e for e in events if isinstance(e, WorkflowCriteriaValidationEvent)]
    assert [e.regeneration_round for e in sweeps] == [0, 1]
    assert [e.regeneration_targets for e in sweeps] == [["AC-1"], ["AC-1"]]


async def test_a_zero_bound_halts_without_regenerating() -> None:
    """Exceeding the bound routes to the halt, never to another round."""
    executor = ValidatorScriptExecutor(
        sweeps=[{"findings": [INFEASIBLE_A, FEASIBLE_B], "contradictions": []}],
    )
    events = await _run(_engine(executor, max_rounds=0))

    assert executor.criteria_calls == 1
    assert executor.sweep_calls == 1
    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.criteria_infeasible


async def test_a_corrected_second_draft_reaches_the_loop() -> None:
    """A regeneration round that clears the sweep proceeds normally."""
    executor = ValidatorScriptExecutor(
        sweeps=[
            {"findings": [INFEASIBLE_A, FEASIBLE_B], "contradictions": []},
            {"findings": [FEASIBLE_A, FEASIBLE_B], "contradictions": []},
        ],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        last_commit_sha="a" * 40,
    )
    events = await _run(_engine(executor, max_rounds=1, quality_gate=gate))

    assert len(gate.calls) == 1
    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is not WorkflowOutcome.criteria_infeasible


async def test_the_refutation_is_inlined_into_the_regeneration_prompt() -> None:
    """The second drafter pass sees `<validation_findings>` naming AC-1."""
    executor = ValidatorScriptExecutor(
        sweeps=[
            {"findings": [INFEASIBLE_A, UNVERIFIABLE_B], "contradictions": []},
            {"findings": [FEASIBLE_A, UNVERIFIABLE_B], "contradictions": []},
        ],
    )
    await _run(_engine(executor, max_rounds=1))

    regeneration = [p for p in executor.prompts if "<validation_findings>" in p]
    assert len(regeneration) == 1
    body = regeneration[0]
    assert "AC-1 infeasible" in body
    assert "lint boundary" in body
    # The unverifiable criterion is regenerated by nobody, so it is not named.
    assert "AC-2 infeasible" not in body
    assert "PostgreSQL" not in body


# ---------------------------------------------------------------------------
# The fault line, end to end (KOD-66 item 1a-1b, bound accounting)
# ---------------------------------------------------------------------------


async def test_unverifiable_only_set_neither_regenerates_nor_halts() -> None:
    """No round is consumed, no halt is reached, and the loop still runs."""
    executor = ValidatorScriptExecutor(
        sweeps=[{"findings": [FEASIBLE_A, UNVERIFIABLE_B], "contradictions": []}],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        last_commit_sha="a" * 40,
    )
    events = await _run(_engine(executor, max_rounds=1, quality_gate=gate))

    assert executor.criteria_calls == 1, "no regeneration round was performed"
    assert len(gate.calls) == 1, "the criterion was handed to the loop"

    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome != WorkflowOutcome.criteria_infeasible

    sweep_event = next(
        e for e in events if isinstance(e, WorkflowCriteriaValidationEvent)
    )
    assert sweep_event.regeneration_targets == []
    verdicts = {v.criterion_id: v for v in sweep_event.validation.verdicts}
    assert verdicts["AC-2"].verdict is FeasibilityVerdict.unverifiable
    assert verdicts["AC-2"].limit_arm is LimitArm.resource_absent
    assert (
        verdicts["AC-2"].missing_resource
        == "a PostgreSQL server reachable from the runner"
    )


async def test_the_unverifiable_criterion_reaches_the_loop_byte_identical() -> None:
    """B's text leaves the sweep exactly as it entered it."""
    executor = ValidatorScriptExecutor(
        sweeps=[{"findings": [FEASIBLE_A, UNVERIFIABLE_B], "contradictions": []}],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        last_commit_sha="a" * 40,
    )
    events = await _run(_engine(executor, max_rounds=1, quality_gate=gate))

    generated = next(e for e in events if isinstance(e, WorkflowCriteriaEvent))
    dispatched = gate.calls[0]["acceptance_criteria"]
    assert isinstance(dispatched, list)
    assert [c.text for c in dispatched] == [c.text for c in generated.criteria]
    assert dispatched[1].text.encode() == generated.criteria[1].text.encode()
    assert [c.id for c in dispatched] == ["AC-1", "AC-2"]


# ---------------------------------------------------------------------------
# Conjunction failure routes like an infeasible criterion
# ---------------------------------------------------------------------------


async def test_an_unsatisfiable_conjunction_regenerates_then_halts() -> None:
    executor = ValidatorScriptExecutor(
        sweeps=[
            {
                "findings": [FEASIBLE_A, FEASIBLE_B],
                "contradictions": [
                    {
                        "criterionIds": ["AC-1", "AC-2"],
                        "explanation": "one export cannot also be two exports",
                    },
                ],
            },
            {
                "findings": [FEASIBLE_A, FEASIBLE_B],
                "contradictions": [
                    {
                        "criterionIds": ["AC-1", "AC-2"],
                        "explanation": "one export cannot also be two exports",
                    },
                ],
            },
        ],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        last_commit_sha="a" * 40,
    )
    events = await _run(_engine(executor, max_rounds=1, quality_gate=gate))

    assert gate.calls == []
    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.criteria_infeasible
    assert complete.criteria_validation is not None
    assert complete.criteria_validation.conjunction.satisfiable is False
    assert complete.criteria_validation.conjunction.conflicting_ids == ["AC-1", "AC-2"]


# ---------------------------------------------------------------------------
# Fan-in is fail-closed at the graph seam too
# ---------------------------------------------------------------------------


async def test_a_missing_validator_finding_fails_the_run_closed() -> None:
    """A verdict nobody produced never defaults to a pass."""
    executor = ValidatorScriptExecutor(
        sweeps=[{"findings": [FEASIBLE_A], "contradictions": []}],
    )
    with pytest.raises(CriteriaFanInError) as excinfo:
        await _run(_engine(executor, max_rounds=1))
    assert excinfo.value.missing_ids == ("AC-2",)


async def test_a_duplicate_validator_finding_fails_the_run_closed() -> None:
    executor = ValidatorScriptExecutor(
        sweeps=[
            {"findings": [FEASIBLE_A, FEASIBLE_A, FEASIBLE_B], "contradictions": []},
        ],
    )
    with pytest.raises(CriteriaFanInError) as excinfo:
        await _run(_engine(executor, max_rounds=1))
    assert excinfo.value.duplicate_ids == ("AC-1",)


# ---------------------------------------------------------------------------
# The persisted artifact carries what the sweep found
# ---------------------------------------------------------------------------


async def test_the_persisted_artifact_carries_ids_verdicts_and_evidence() -> None:
    executor = ValidatorScriptExecutor(
        sweeps=[{"findings": [FEASIBLE_A, UNVERIFIABLE_B], "contradictions": []}],
    )
    persister = FakeArtifactPersister()
    await _run(_engine(executor, max_rounds=1, artifact_persister=persister))

    assert persister.persist_calls
    artifact = CriteriaArtifact.model_validate_json(
        persister.artifacts[0]["criteria.json"],
    )
    assert [c.id for c in artifact.criteria] == ["AC-1", "AC-2"]
    assert artifact.criteria[0].classification is CriterionClassification.hard_gate
    assert artifact.criteria[1].feasibility.verdict is FeasibilityVerdict.unverifiable
    assert (
        artifact.criteria[1].feasibility.missing_resource
        == "a PostgreSQL server reachable from the runner"
    )
    assert artifact.conjunction.satisfiable is True
