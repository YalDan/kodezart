"""The criteria oracle is byte-stable across every surface that reads it.

KOD-53/AC-18 — the two-iteration integration run over all four surfaces.
KOD-11's reproduction compares four sources: the ``workflow_criteria``
event, the persisted ``.kodezart/criteria.json``, and each iteration's
evaluation report.  This runs a real two-iteration ralph loop under the
workflow engine with an evaluator that MUTATES the text it echoes — the
observed failure mode — and asserts all four still agree byte for byte,
with identity carried by the ``AC-n`` id.
"""

from collections.abc import AsyncGenerator

from kodezart.chains.ralph_loop import RalphLoop
from kodezart.chains.ralph_workflow import RalphWorkflowEngine
from kodezart.domain.criteria import mint_criteria
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.accept import AcceptVerdict
from kodezart.types.domain.agent import (
    AgentEvent,
    AssistantTextEvent,
    ResultEvent,
    WorkflowCriteriaEvent,
    WorkflowIterationEvent,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.criteria import (
    CriteriaArtifact,
    CriterionClass,
    DraftedCriterion,
)
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.skills import SkillsSelection
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeChangePersister,
    FakeGitService,
    FakeRepoCache,
    FakeTicketGenerator,
    FakeWorkspaceProvider,
    PassThroughGate,
    as_validated,
    make_prompt_provider,
)

# Two criteria chosen for the exact shapes the incident report names: a
# space before a quote in an identifier-equals-quoted-string pattern, and
# escaped backslashes around a quoted class-style string.
CRITERION_ONE = 'The response sets header ="application/json", with the space.'
CRITERION_TWO = 'The template emits class=\\"kz-row\\" on the wrapper element.'

# What a mutating evaluator echoes back: whitespace normalised on the
# first, backslashes collapsed on the second.
ECHO_ONE = 'The response sets header="application/json",with the space.'
ECHO_TWO = 'The template emits class="kz-row" on the wrapper element.'


class MutatingEchoExecutor:
    """A loop executor whose evaluator echoes mutated criterion text.

    Iteration 1 fails both criteria; iteration 2 passes both.  Every echo
    is deliberately wrong, and every echo is keyed to the right id.
    """

    def __init__(self) -> None:
        self.eval_calls = 0
        self.eval_prompts: list[str] = []
        self.feedback_prompts: list[str] = []

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
        props: dict[str, object] = {}
        if output_format is not None:
            schema = output_format.get("schema")
            if isinstance(schema, dict):
                raw = schema.get("properties", {})
                if isinstance(raw, dict):
                    props = raw

        if "slug" in props:
            yield _result({"slug": "oracle"})
            return
        if "findings" in props:
            yield _result(
                {
                    "findings": [
                        {
                            "criterionId": "AC-1",
                            "verdict": "feasible",
                            "smallestRepair": "none",
                        },
                        {
                            "criterionId": "AC-2",
                            "verdict": "feasible",
                            "smallestRepair": "none",
                        },
                    ],
                    "contradictions": [],
                }
            )
            return
        if "criteria" in props and "criteriaResults" not in props:
            yield _result(
                {
                    "criteria": [
                        {"text": CRITERION_ONE, "criterionClass": "hard_gate"},
                        {"text": CRITERION_TWO, "criterionClass": "soft_signal"},
                    ],
                    "reasoning": "Generated from codebase analysis.",
                }
            )
            return
        if "criteriaResults" in props:
            self.eval_calls += 1
            self.eval_prompts.append(prompt)
            passed = self.eval_calls >= 2
            yield _result(
                {
                    "criteriaResults": [
                        {
                            "criterionId": "AC-1",
                            "criterion": ECHO_ONE,
                            "passed": passed,
                            "reasoning": "header check",
                        },
                        {
                            "criterionId": "AC-2",
                            "criterion": ECHO_TWO,
                            "passed": passed,
                            "reasoning": "class check",
                        },
                    ],
                }
            )
            return
        if "title" in props and "description" in props:
            yield _result({"title": "feat: oracle", "description": "d"})
            return

        # Unstructured execute call — record the prompt so the re-injected
        # feedback text can be inspected.
        self.feedback_prompts.append(prompt)
        yield AssistantTextEvent(text="worked", model="m")
        yield ResultEvent(
            subtype="result",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="oracle",
            commit_sha="c" * 40,
        )


def _result(structured: dict[str, object]) -> ResultEvent:
    return ResultEvent(
        subtype="result",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="oracle",
        structured_output=structured,
    )


async def test_the_oracle_is_byte_identical_across_all_four_surfaces() -> None:
    executor = MutatingEchoExecutor()
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    prompts = make_prompt_provider()
    persister = FakeArtifactPersister()
    loop = RalphLoop(
        service=service,
        max_iterations=3,
        plateau_window=5,
        git=FakeGitService(),
        cache=FakeRepoCache(),
        prompts=prompts,
        skills=SUPPRESS_ALL_SKILLS,
    )
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=prompts,
        service=service,
        quality_gate=loop,
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
        artifact_persister=persister,
    )

    events = [
        event
        async for event in engine.run(
            prompt="do the thing",
            repo_path="/tmp/fake",
            repo_url=None,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key="oracle-run",
        )
    ]

    iterations = [e for e in events if isinstance(e, WorkflowIterationEvent)]
    assert len(iterations) == 2, "the run must reach a second iteration"
    assert iterations[0].verdict is AcceptVerdict.rejected
    assert iterations[1].verdict is AcceptVerdict.accepted

    # (a) the workflow_criteria event
    criteria_event = next(e for e in events if isinstance(e, WorkflowCriteriaEvent))
    source = [(c.id, c.text) for c in criteria_event.criteria]
    assert source == [("AC-1", CRITERION_ONE), ("AC-2", CRITERION_TWO)]

    # (b) the persisted .kodezart/criteria.json
    artifact = CriteriaArtifact.model_validate_json(
        persister.artifacts[0]["criteria.json"],
    )
    assert [(c.id, c.text) for c in artifact.criteria] == source

    # (c) and (d) every iteration's evaluation report
    for iteration in iterations:
        reported = [
            (r.criterion_id, r.criterion) for r in iteration.evaluation.criteria_results
        ]
        assert reported == source
        assert len(iteration.evaluation.criteria_results) == 2

    # Byte-for-byte, not merely equal after normalisation.
    for _, text in source:
        assert text.encode() in (CRITERION_ONE.encode(), CRITERION_TWO.encode())
    assert artifact.criteria[1].text.encode() == CRITERION_TWO.encode()
    assert iterations[0].evaluation.criteria_results[1].criterion != ECHO_TWO


async def test_the_second_iteration_is_asked_about_the_harness_text() -> None:
    """KOD-53/AC-20 — re-injection renders the harness's own text, by id."""
    executor = MutatingEchoExecutor()
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    prompts = make_prompt_provider()
    loop = RalphLoop(
        service=service,
        max_iterations=3,
        plateau_window=5,
        git=FakeGitService(),
        cache=FakeRepoCache(),
        prompts=prompts,
        skills=SUPPRESS_ALL_SKILLS,
    )
    engine = RalphWorkflowEngine(
        gate=PassThroughGate(),
        skills=SUPPRESS_ALL_SKILLS,
        prompts=prompts,
        service=service,
        quality_gate=loop,
        ticket_generator=FakeTicketGenerator(),
        merger=FakeBranchMerger(),
        git_base_url="https://github.com",
        git_remote="origin",
        git=FakeGitService(remote_branch_shas={"main": "b" * 40}),
        cache=FakeRepoCache(),
    )

    _ = [
        event
        async for event in engine.run(
            prompt="do the thing",
            repo_path="/tmp/fake",
            repo_url=None,
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            cache_key="oracle-feedback",
        )
    ]

    feedback = [p for p in executor.feedback_prompts if "AC-1" in p]
    assert feedback, "the second iteration re-injects the failed criteria"
    body = feedback[0]
    assert CRITERION_ONE in body
    assert CRITERION_TWO in body
    assert ECHO_ONE not in body
    assert ECHO_TWO not in body


async def test_both_iterations_dispatch_the_full_id_set() -> None:
    """The evaluator prompt names every id, every iteration."""
    executor = MutatingEchoExecutor()
    service = AgentService(
        executor=executor,
        workspace=FakeWorkspaceProvider(),
        persister=FakeChangePersister(),
    )
    prompts = make_prompt_provider()
    loop = RalphLoop(
        service=service,
        max_iterations=3,
        plateau_window=5,
        git=FakeGitService(),
        cache=FakeRepoCache(),
        prompts=prompts,
        skills=SUPPRESS_ALL_SKILLS,
    )

    _ = [
        event
        async for event in loop.run(
            prompt="implement",
            repo_path="/tmp/fake",
            repo_url=None,
            feature_branch="kodezart/oracle-12345678",
            ralph_branch="kodezart/oracle-12345678-ralph-abcdef01",
            base_spec=trunk_base("main"),
            permission_mode="bypassPermissions",
            allowed_tools=["Bash"],
            acceptance_criteria=as_validated(
                mint_criteria(
                    [
                        DraftedCriterion(
                            text=CRITERION_ONE,
                            criterion_class=CriterionClass.hard_gate,
                        ),
                        DraftedCriterion(
                            text=CRITERION_TWO,
                            criterion_class=CriterionClass.soft_signal,
                        ),
                    ]
                )
            ),
            cache_key="oracle-dispatch",
            repo_visibility=RepoVisibility.UNKNOWN,
        )
    ]

    assert executor.eval_calls == 2
    for prompt in executor.eval_prompts:
        assert "AC-1" in prompt
        assert "AC-2" in prompt
        assert CRITERION_ONE in prompt
        assert CRITERION_TWO in prompt
