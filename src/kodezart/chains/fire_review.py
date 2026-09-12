"""Judge the consolidated changes against the validated criteria."""

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from kodezart.chains.criteria import current_native_criteria
from kodezart.chains.fire_consolidation import resolve_workflow_cwd
from kodezart.core.constants import EVAL_PERMISSION_MODE
from kodezart.core.errors import soft_failure
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    AgentRunner,
    FireCriteriaReader,
    GitService,
    PromptSetProvider,
    RepoCache,
)
from kodezart.core.redispatch import (
    until_permutation,
)
from kodezart.core.stream_drain import drain
from kodezart.domain.accept_gate import (
    gate_cleared,
    sherlock_items,
)
from kodezart.domain.criteria_grading import grade_iteration
from kodezart.domain.fan_in import fan_in_report, require_permutation
from kodezart.domain.prompt_variables import (
    changeset_variables,
    execution_criteria_variables,
)
from kodezart.domain.workflow_state import (
    current_fire_spec,
    validated_criteria,
)
from kodezart.types.domain.agent import (
    ACCEPTANCE_CRITERIA_SCHEMA,
    AcceptanceCriteriaOutput,
    WorkflowReviewEvent,
)
from kodezart.types.domain.criteria import (
    FanInReport,
)
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.grading import IterationGrade
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType, ToolPreset
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS
from kodezart.types.domain.workflow import (
    ExecutionContext,
    WorkflowState,
)


class FireReview:
    """Judge the consolidated changes against the validated criteria."""

    def __init__(
        self,
        *,
        service: AgentRunner,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
        git: GitService,
        cache: RepoCache,
        fan_in_max_attempts: int,
        criteria_reader: FireCriteriaReader | None = None,
    ) -> None:
        self._service = service
        self._criteria_reader = criteria_reader
        self._prompts = prompts
        self._skills = skills
        self._git = git
        self._cache = cache
        self._fan_in_max_attempts = fan_in_max_attempts
        self._log: BoundLogger = get_logger("kodezart.chains.ralph_workflow")

    async def review_against_ticket(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Evaluate merged code against ticket acceptance criteria."""
        # Fail-fast on programming-error preconditions BEFORE touching the
        # LangGraph runtime (so callers — and tests — see the precondition
        # error, not a misleading "outside of a runnable context" error).
        review_base_sha = state["review_base_sha"]
        review_head_sha = state["review_head_sha"]
        if review_base_sha is None or review_head_sha is None:
            msg = (
                "review_against_ticket requires review_base_sha and "
                "review_head_sha to be set by the consolidation node"
            )
            raise RuntimeError(msg)

        spec = current_fire_spec(state)
        criterion_set = state["criterion_set"]
        ctx = ExecutionContext.from_configurable(config)
        writer = get_stream_writer()
        cwd = await resolve_workflow_cwd(ctx, self._cache)
        changeset = await self._git.diff_summary(
            cwd=cwd,
            base_ref=review_base_sha,
            head_ref=review_head_sha,
        )

        async def review() -> IterationGrade:
            nonlocal criterion_set
            if isinstance(spec, TrackerSpec):
                criterion_set = await current_native_criteria(
                    spec=spec,
                    reader=self._criteria_reader,
                )
            criteria = validated_criteria({**state, "criterion_set": criterion_set})
            prompt = self._prompts.template_for(PromptKey.POST_MERGE_REVIEW).render(
                {
                    **execution_criteria_variables(criteria),
                    **changeset_variables(changeset),
                },
            )
            result_event, rate_limit_rejected = await drain(
                self._service.stream(
                    prompt=prompt,
                    repo_path=ctx.repo_path,
                    repo_url=ctx.repo_url,
                    branch=state["feature_branch"],
                    permission_mode=EVAL_PERMISSION_MODE,
                    allowed_tools=ToolPreset.EVALUATION,
                    skills=self._prompts.session_skills(
                        PromptKey.POST_MERGE_REVIEW, self._skills
                    ),
                    session_type=SessionType.TICKET_FIRE,
                    run_identity=ctx.run_identity,
                    agents=NO_SUBAGENTS,
                    session_policy=self._prompts.session_policy(
                        PromptKey.POST_MERGE_REVIEW,
                    ),
                    output_format={
                        "type": "json_schema",
                        "schema": ACCEPTANCE_CRITERIA_SCHEMA,
                    },
                    cache_key=ctx.cache_key,
                ),
                site="post_merge_review",
            )

            if result_event is None or result_event.structured_output is None:
                msg = "Agent did not produce structured output for review"
                raise soft_failure(
                    msg,
                    raise_site="post_merge_review",
                    result_event=result_event,
                    rate_limit_rejected=rate_limit_rejected,
                )

            output = AcceptanceCriteriaOutput.model_validate(
                result_event.structured_output,
            )
            return grade_iteration(criteria, output)

        grade, unresolved, attempts = await until_permutation(
            dispatch=review,
            check=require_permutation,
            max_attempts=self._fan_in_max_attempts,
            site="post_merge_review",
            log=self._log,
        )
        fan_in: FanInReport | None = None
        if unresolved is not None:
            # Same guard, same exhaustion arm as the loop's evaluator —
            # a separate call site, so it is wired and asserted separately
            # rather than assumed to inherit anything.
            fan_in = fan_in_report(grade, attempts=attempts)
            await self._log.awarning(
                "fan_in_exhausted",
                site="post_merge_review",
                # No loop here, which is a STATE of this event and not a
                # field to leave out: one event name reaching a reader in
                # two shapes is two events wearing one name.
                iteration=None,
                attempts=attempts,
                dispatched_count=grade.dispatched_count,
                missing_ids=grade.missing_ids,
                unknown_ids=grade.unknown_ids,
                duplicate_ids=grade.duplicate_ids,
            )
        passed = gate_cleared(grade.verdict)

        feedback: str | None = None
        if not passed:
            feedback = "\n".join(
                f"- {f.criterion_id} {f.text}: {f.reasoning}" for f in grade.failures
            )

        writer(
            WorkflowReviewEvent(
                passed=passed,
                evaluation=AcceptanceCriteriaOutput(criteria_results=grade.results),
                fix_rounds_used=state["remediation_rounds_used"],
                fan_in=fan_in,
            )
        )

        # The reviewer's own concerns ride to the pull request the prompt
        # promises them to.  They are appended, not substituted: the loop's
        # flagged items describe the work, these describe the review of it.
        return {
            "criterion_set": criterion_set,
            "review_passed": passed,
            "review_feedback": feedback,
            "flagged_items": [
                *state["flagged_items"],
                *sherlock_items(grade.sherlock_flags),
            ],
        }
