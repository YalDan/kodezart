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
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    AgentEvent,
    ResultEvent,
    WorkflowCompleteEvent,
    WorkflowCriteriaEvent,
    WorkflowCriteriaValidationEvent,
    WorkflowIterationEvent,
)
from kodezart.types.domain.base_spec import (
    BaseInput,
    BaseRefRole,
    BaseSpec,
    trunk_base,
)
from kodezart.types.domain.criteria import (
    CriteriaArtifact,
    CriterionClass,
    CriterionVerdict,
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
    FakePRCreator,
    FakeQualityGate,
    FakeRepoCache,
    FakeTicketGenerator,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_passing_evaluation,
    make_passing_evaluation_over,
    make_prompt_provider,
    make_ticket_draft,
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

    def __init__(
        self,
        sweeps: list[dict[str, object]],
        criteria_rounds: list[dict[str, object]] | None = None,
    ) -> None:
        self._sweeps = list(sweeps)
        self._criteria_rounds = list(criteria_rounds or [])
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
            if self._criteria_rounds:
                structured = self._criteria_rounds.pop(0)
                yield ResultEvent(
                    subtype="result",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id="validator-script",
                    structured_output=structured,
                )
                return
            structured = {
                "criteria": [
                    {
                        "text": "`Foo` is importable from `app.api`.",
                        "criterionClass": "hard_gate",
                    },
                    {
                        "text": (
                            "A record round-trips through the store "
                            'preserving its  "id" verbatim.'
                        ),
                        "criterionClass": "hard_gate",
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
    ticket_generator: FakeTicketGenerator | None = None,
    pr_creator: FakePRCreator | None = None,
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
        ticket_generator=ticket_generator or FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        criteria_max_regeneration_rounds=max_rounds,
        artifact_persister=artifact_persister,
        pr_creator=pr_creator,
    )


async def _run(
    engine: RalphWorkflowEngine,
    *,
    prompt: str = "do the thing",
    repo_url: str | None = None,
    base_spec: BaseSpec | None = None,
) -> list[AgentEvent]:
    return [
        event
        async for event in engine.run(
            prompt=prompt,
            repo_path=None if repo_url else "/tmp/fake",
            repo_url=repo_url,
            base_spec=base_spec or trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key="criteria-gate",
        )
    ]


# ---------------------------------------------------------------------------
# The bound is an AppConfig field, not a literal
# ---------------------------------------------------------------------------


def test_regeneration_bound_is_a_named_config_field() -> None:
    """`KODEZART_CRITERIA_MAX_REGENERATION_ROUNDS`, never a bare number."""
    field = AppConfig.model_fields["criteria_max_regeneration_rounds"]
    assert field.annotation is int
    assert AppConfig().criteria_max_regeneration_rounds >= 0


def test_regeneration_bound_reads_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KODEZART_CRITERIA_MAX_REGENERATION_ROUNDS", "3")
    assert AppConfig().criteria_max_regeneration_rounds == 3


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
    assert verdicts["AC-1"].verdict is CriterionVerdict.infeasible
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
    assert verdicts["AC-2"].verdict is CriterionVerdict.unverifiable
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


async def test_the_loop_receives_the_verdict_and_the_named_resource() -> None:
    """AC-12's second half: the criterion arrives CARRYING its verdict.

    An ``unverifiable`` criterion that reaches the loop indistinguishable
    from a ``feasible`` one is the collapse the three-state vocabulary
    exists to prevent, re-entering through the dispatch seam.
    """
    executor = ValidatorScriptExecutor(
        sweeps=[{"findings": [FEASIBLE_A, UNVERIFIABLE_B], "contradictions": []}],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        last_commit_sha="a" * 40,
    )
    await _run(_engine(executor, max_rounds=1, quality_gate=gate))

    dispatched = gate.calls[0]["acceptance_criteria"]
    assert isinstance(dispatched, list)
    feasible, unverifiable = dispatched
    assert feasible.feasibility.verdict is CriterionVerdict.feasible
    assert feasible.feasibility.missing_resource is None
    assert unverifiable.feasibility.verdict is CriterionVerdict.unverifiable
    assert unverifiable.feasibility.limit_arm is LimitArm.resource_absent
    assert (
        unverifiable.feasibility.missing_resource
        == "a PostgreSQL server reachable from the runner"
    )


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
    assert artifact.criteria[0].criterion_class is CriterionClass.hard_gate
    assert artifact.criteria[1].feasibility.verdict is CriterionVerdict.unverifiable
    assert (
        artifact.criteria[1].feasibility.missing_resource
        == "a PostgreSQL server reachable from the runner"
    )
    assert artifact.conjunction.satisfiable is True


# ---------------------------------------------------------------------------
# AC-32 — a scope criterion that names no base never reaches the loop
# ---------------------------------------------------------------------------


STACKED_BASE = BaseSpec(
    base_ref="kodezart/blocker-a-11111111",
    role=BaseRefRole.deliverable,
    inputs=(
        BaseInput(
            blocker_issue_id="KOD-101",
            branch="kodezart/blocker-a-11111111",
            sha="a" * 40,
        ),
    ),
)


def _base_line(base_ref: str) -> str:
    """The sentence the harness states the resolved base in."""
    return f"This lane's comparison base is `{base_ref}`"


BARE_DIFF_CRITERION = (
    "Running `git diff --name-only` shows changes only under `src/kodezart/api/`."
)
BASED_CRITERION = (
    "Against the lane's recorded base `kodezart/blocker-a-11111111`, the "
    "changed file set is confined to `src/kodezart/api/`."
)


def _criteria_round(scope_text: str) -> dict[str, object]:
    return {
        "criteria": [
            {"text": scope_text, "criterionClass": "hard_gate"},
            {
                "text": "`Foo` is importable from `app.api`.",
                "criterionClass": "hard_gate",
            },
        ],
        "reasoning": "Generated from codebase analysis.",
    }


WRONG_BASELINE_FINDING = {
    "criterionId": "AC-1",
    "smallestRepair": "criterion_text",
    "refutation": (
        "the criterion measures scope with a bare `git diff` and names no base, "
        "so it is measured against whatever the grader picks — against the "
        "recorded base `kodezart/blocker-a-11111111` it is not the same claim"
    ),
}


async def test_a_scope_criterion_with_no_stated_base_is_regenerated() -> None:
    """The wrong-baseline class is a criterion-text fault, so it is amended.

    What reaches the loop names the resolved base; the bare ``git diff``
    draft does not survive the sweep, and the assertion is over the
    dispatched set rather than over the prompt that asked for it.
    """
    executor = ValidatorScriptExecutor(
        sweeps=[
            {"findings": [WRONG_BASELINE_FINDING, FEASIBLE_B], "contradictions": []},
            {"findings": [FEASIBLE_A, FEASIBLE_B], "contradictions": []},
        ],
        criteria_rounds=[
            _criteria_round(BARE_DIFF_CRITERION),
            _criteria_round(BASED_CRITERION),
        ],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        last_commit_sha="a" * 40,
    )
    await _run(
        _engine(executor, max_rounds=1, quality_gate=gate),
        base_spec=STACKED_BASE,
    )

    assert executor.criteria_calls == 2, "the bare-diff draft consumed a round"
    dispatched = gate.calls[0]["acceptance_criteria"]
    assert isinstance(dispatched, list)
    texts = [criterion.text for criterion in dispatched]
    assert BASED_CRITERION in texts
    assert BARE_DIFF_CRITERION not in texts
    assert "git diff --name-only`" not in texts[0]

    # The amendment is demanded AGAINST the base the run resolved. Asserting
    # the base only in the dispatched text would compare one test constant to
    # another: the drafts are scripted, so their contents prove nothing about
    # the run. What the run supplies is this line.
    regeneration = [p for p in executor.prompts if "<validation_findings>" in p]
    assert len(regeneration) == 1
    assert _base_line(STACKED_BASE.base_ref) in regeneration[0]


@pytest.mark.parametrize(
    ("spec", "other_base"),
    [
        (trunk_base("main"), STACKED_BASE.base_ref),
        (STACKED_BASE, "main"),
    ],
    ids=["trunk-fired", "stacked"],
)
async def test_the_drafter_is_told_which_base_the_lane_is_measured_against(
    spec: BaseSpec,
    other_base: str,
) -> None:
    """The resolved base reaches the drafter as data, not as an assumption.

    Two runs differing only in the base they were fired with, each asserting
    the OTHER lane's base is absent.  A literal in the harness — `main`, or
    anything else fixed — satisfies at most one of the two, and a corrupted
    render satisfies neither: the assertion is a function of the run's own
    input rather than of a constant this test also supplies.
    """
    executor = ValidatorScriptExecutor(
        sweeps=[{"findings": [FEASIBLE_A, FEASIBLE_B], "contradictions": []}],
    )
    await _run(_engine(executor), base_spec=spec)

    drafter_prompts = [
        p for p in executor.prompts if "SCOPE CRITERIA NAME THEIR BASE" in p
    ]
    assert len(drafter_prompts) == 1
    assert _base_line(spec.base_ref) in drafter_prompts[0]
    assert _base_line(other_base) not in drafter_prompts[0]
    assert "Never write `main` or `trunk` as the base" in drafter_prompts[0]


async def test_the_refuter_reads_scope_against_the_same_base_the_drafter_was_given(
) -> None:
    """One base per run, or the two surfaces grade different claims.

    The drafter is told which base to write criteria against and the refuter
    is told which base to test them against.  Two bases in one run is the
    defect this lane exists to close, arriving as a disagreement rather than
    as a wrong value.
    """
    executor = ValidatorScriptExecutor(
        sweeps=[{"findings": [FEASIBLE_A, FEASIBLE_B], "contradictions": []}],
    )
    await _run(_engine(executor), base_spec=STACKED_BASE)

    drafter = next(p for p in executor.prompts if "SCOPE CRITERIA NAME THEIR BASE" in p)
    refuter = next(p for p in executor.prompts if "ADVERSARIAL REFUTER" in p)
    assert _base_line(STACKED_BASE.base_ref) in drafter
    assert f"you hold the repository at base ref `{STACKED_BASE.base_ref}`" in refuter
    assert _base_line("main") not in drafter
    assert "repository at base ref `main`" not in refuter


# ---------------------------------------------------------------------------
# AC-13 / AC-17 — the historical defect patterns, as behaviour, not as prose
# ---------------------------------------------------------------------------

# Each fixture is a ticket abstracted from a run that induced the pattern,
# paired with the criterion that run's drafter actually produced and the
# evidence a refuter reading the repository at base reports about it. The
# ticket DRIVES the run — it is the run's prompt and the summary the ticket
# generator returns, so the drafter is rendered with it and the subject of
# the assertion really is "this fixture ticket generates criteria that
# comply". The claim is settled over the criteria the stage YIELDS: an
# instruction being present in a prompt is not compliance, so no prompt
# assertion here stands alone as evidence of one.

PATTERN_1_TICKET = (
    "Add a `status` arm to the run-state renderer so every state the domain "
    "declares is rendered, including any the ticket author expects to exist."
)
PATTERN_3_TICKET = (
    "Tighten the lint surface: the change must not weaken any existing check."
)
PATTERN_5_TICKET = (
    "Complete the switch over `WorkflowOutcome` so no terminal route is "
    "left undiscriminated."
)

PATTERN_1_BAD = (
    "The renderer handles every `RunState` arm, including `archived` and `paused`."
)
PATTERN_3_BAD = "Running `uv run ruff check src/ tests/` exits 0 with no warnings."
PATTERN_5_BAD = (
    "The `WorkflowOutcome` switch covers `criteria_infeasible`, `pr_opened` "
    "and `rolled_back`."
)

PATTERN_1_FINDING = {
    "criterionId": "AC-1",
    "smallestRepair": "criterion_text",
    "undeclaredSwitchArms": ["archived", "paused"],
    "refutation": (
        "`RunState` declares no `archived` and no `paused` member; an arm the "
        "type does not have cannot be handled by any implementation"
    ),
}
PATTERN_3_FINDING = {
    "criterionId": "AC-1",
    "smallestRepair": "criterion_text",
    "forbiddenClass": "execution_graded",
    "refutation": (
        "the criterion is graded by the exit status of a command, which is "
        "the run's own stochastic execution rather than a property of the tree"
    ),
}
PATTERN_5_FINDING = {
    "criterionId": "AC-1",
    "smallestRepair": "criterion_text",
    "undeclaredSwitchArms": ["rolled_back"],
    "refutation": (
        "`WorkflowOutcome` declares no `rolled_back` member; the arms it does "
        "declare are the eleven terminal routes named in types/domain/outcome.py"
    ),
}

CORRECTED = "Every arm the named type actually declares is handled by the renderer."


@pytest.mark.parametrize(
    ("ticket", "drafted", "finding", "banned_arms"),
    [
        (PATTERN_1_TICKET, PATTERN_1_BAD, PATTERN_1_FINDING, ("archived", "paused")),
        (PATTERN_3_TICKET, PATTERN_3_BAD, PATTERN_3_FINDING, ()),
        (PATTERN_5_TICKET, PATTERN_5_BAD, PATTERN_5_FINDING, ("rolled_back",)),
    ],
    ids=["pattern-1", "pattern-3", "pattern-5"],
)
async def test_no_forbidden_class_or_non_domain_arm_reaches_the_loop(
    ticket: str,
    drafted: str,
    finding: dict[str, object],
    banned_arms: tuple[str, ...],
) -> None:
    """AC-13: the fixture ticket's own run yields criteria that comply.

    The ticket is the run's prompt and the ticket generator's summary, so
    the drafter is rendered with it and the criteria under assertion are
    the ones this ticket produced.  What the stage yields carries no
    forbidden class, no undeclared arm on its verdict, and no mention of
    an arm the refuter established the named type does not declare.
    """
    executor = ValidatorScriptExecutor(
        sweeps=[
            {"findings": [finding, FEASIBLE_B], "contradictions": []},
            {"findings": [FEASIBLE_A, FEASIBLE_B], "contradictions": []},
        ],
        criteria_rounds=[_criteria_round(drafted), _criteria_round(CORRECTED)],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        last_commit_sha="a" * 40,
    )
    events = await _run(
        _engine(
            executor,
            max_rounds=1,
            quality_gate=gate,
            ticket_generator=FakeTicketGenerator(make_ticket_draft(summary=ticket)),
        ),
        prompt=ticket,
    )

    drafter_prompts = [p for p in executor.prompts if "WATSON 1: BEHAVIORAL" in p]
    assert len(drafter_prompts) == 2
    assert ticket in drafter_prompts[0], "the fixture ticket drove generation"
    refutation = finding["refutation"]
    assert isinstance(refutation, str)
    assert refutation in drafter_prompts[1], "the refutation was handed back"

    sweep_event = next(
        e for e in events if isinstance(e, WorkflowCriteriaValidationEvent)
    )
    assert sweep_event.regeneration_targets == ["AC-1"]

    dispatched = gate.calls[0]["acceptance_criteria"]
    assert isinstance(dispatched, list)
    texts = [criterion.text for criterion in dispatched]
    assert drafted not in texts
    assert CORRECTED in texts
    for criterion in dispatched:
        assert criterion.feasibility.forbidden_class is None
        assert criterion.feasibility.undeclared_switch_arms == []
        for arm in banned_arms:
            assert arm not in criterion.text


def test_the_pattern_5_fixture_still_names_an_arm_its_type_lacks() -> None:
    """AC-13's premise is read off the production type, never assumed.

    ``PATTERN_5_BAD`` is a Pattern-5 instance only while ``rolled_back`` is
    absent from ``WorkflowOutcome`` and the arms it pairs that with are
    present.  Appending ``rolled_back`` to the enum would quietly turn the
    fixture into a satisfiable criterion and leave AC-13 asserted over
    nothing, so the enum is consulted rather than trusted.
    """
    declared = {member.value for member in WorkflowOutcome}
    assert "rolled_back" not in declared
    assert {"criteria_infeasible", "pr_opened"} <= declared


async def test_an_ungradeable_class_survives_the_bound_as_a_halt() -> None:
    """A drafter that keeps emitting one halts the run instead of dispatching it."""
    executor = ValidatorScriptExecutor(
        sweeps=[
            {"findings": [PATTERN_3_FINDING, FEASIBLE_B], "contradictions": []},
            {"findings": [PATTERN_3_FINDING, FEASIBLE_B], "contradictions": []},
        ],
        criteria_rounds=[
            _criteria_round(PATTERN_3_BAD),
            _criteria_round(PATTERN_3_BAD),
        ],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        last_commit_sha="a" * 40,
    )
    events = await _run(_engine(executor, max_rounds=1, quality_gate=gate))

    assert gate.calls == [], "an ungradeable criterion never reached the loop"
    complete = next(e for e in events if isinstance(e, WorkflowCompleteEvent))
    assert complete.outcome is WorkflowOutcome.criteria_infeasible


async def test_a_literal_count_class_is_flagged_rather_than_regenerated() -> None:
    """The one banned class that is not a feasibility fault keeps its text."""
    executor = ValidatorScriptExecutor(
        sweeps=[
            {
                "findings": [
                    {
                        "criterionId": "AC-1",
                        "smallestRepair": "none",
                        "forbiddenClass": "literal_count",
                    },
                    FEASIBLE_B,
                ],
                "contradictions": [],
            },
        ],
        criteria_rounds=[_criteria_round("Exactly 3 files under `src/` change.")],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation(),
        last_commit_sha="a" * 40,
    )
    await _run(_engine(executor, max_rounds=1, quality_gate=gate))

    assert executor.criteria_calls == 1, "no regeneration round was consumed"
    dispatched = gate.calls[0]["acceptance_criteria"]
    assert isinstance(dispatched, list)
    flagged = dispatched[0]
    assert flagged.text == "Exactly 3 files under `src/` change."
    assert flagged.criterion_class is CriterionClass.soft_signal


async def test_an_ungraded_criterion_clamps_the_run_and_names_its_resource() -> None:
    """KOD-71 R3, through the graph: no seat, ceiling clamped, resource stated.

    The evaluator answers BOTH ids as passing, so nothing failed anywhere in
    the run.  A gate reading pass/fail alone reports ``accepted`` here; the
    ruling says a run holding a criterion nobody could grade may not claim
    clean acceptance, and the resource whose absence blocked it has to reach
    the human reading the pull request.
    """
    executor = ValidatorScriptExecutor(
        sweeps=[{"findings": [FEASIBLE_A, UNVERIFIABLE_B], "contradictions": []}],
    )
    gate = FakeQualityGate(
        events=[],
        evaluation=make_passing_evaluation_over("AC-1", "AC-2"),
        last_commit_sha="a" * 40,
    )
    pr_creator = FakePRCreator()
    events = await _run(
        _engine(executor, max_rounds=1, quality_gate=gate, pr_creator=pr_creator),
        repo_url="https://github.com/o/r",
    )

    iteration = next(e for e in events if isinstance(e, WorkflowIterationEvent))
    assert iteration.verdict is AcceptVerdict.ship_with_flags

    create = next(c for c in pr_creator.calls if c["method"] == "create_pr")
    body = str(create["body"])
    assert "AC-2" in body
    assert "a PostgreSQL server reachable from the runner" in body
