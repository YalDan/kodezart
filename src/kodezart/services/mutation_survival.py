"""The same question asked of two trees, so a pass says what it is worth.

A criterion whose named check still passes when the behaviour that criterion
names is gone from the tree read nothing about that behaviour.  Measuring
that is one reading: the evaluation that produced the passes is put again,
unchanged, to a copy of the graded tree with that behaviour taken out of it.

Nothing the removing session SAYS is read.  Its product is the tree, and the
tree is read back here with the same two facts the clean grading is checked
with, the other way round: the copy must still stand at the graded sha and
must hold changes, or no reading was taken and every pass keeps standing.
So a removal that committed, moved the head or changed nothing withholds
nothing, and neither does a mutant grading that produced no structured
output or did not answer for a criterion.  The failure mode is silence.
"""

from collections.abc import Sequence

from pydantic import ValidationError

from kodezart.core.constants import EVAL_PERMISSION_MODE, UNATTENDED_PERMISSION_MODE
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import (
    AgentRunner,
    GitService,
    PromptSetProvider,
    WorkspaceProvider,
)
from kodezart.core.stream_drain import drain
from kodezart.domain.mutation_survival import passing_checks, surviving_checks
from kodezart.domain.prompt_variables import execution_criteria_variables
from kodezart.services.git_observations import read_workspace_head
from kodezart.services.owned_workspace import owned_workspace
from kodezart.types.domain.agent import (
    ACCEPTANCE_CRITERIA_SCHEMA,
    AcceptanceCriteriaOutput,
)
from kodezart.types.domain.criteria import CriterionId, ExecutionCriterion
from kodezart.types.domain.grading import IterationGrade
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.session import SessionType, ToolPreset
from kodezart.types.domain.skills import SkillsSelection
from kodezart.types.domain.subagents import NO_SUBAGENTS


class MutationSurvivalReader:
    """Read which of an attempt's passes survive the behaviour they name going.

    Its own service rather than another arm of the loop: it holds a
    two-session machine over a workspace of its own, and a loop that grew
    one would be answering two questions.  It is held as an optional
    collaborator for the same reason the lane's writer is — an arm that
    takes no such reading constructs none.
    """

    def __init__(
        self,
        *,
        runner: AgentRunner,
        workspace: WorkspaceProvider,
        git: GitService,
        prompts: PromptSetProvider,
        skills: SkillsSelection,
    ) -> None:
        self._runner = runner
        self._workspace = workspace
        self._git = git
        self._prompts = prompts
        self._skills = skills
        self._log: BoundLogger = get_logger(__name__)

    async def survivors(
        self,
        *,
        criteria: Sequence[ExecutionCriterion],
        grade: IterationGrade,
        evaluation_prompt: str,
        graded_sha: str,
        repo_path: str,
        cache_key: str | None,
    ) -> frozenset[CriterionId]:
        """Which of *grade*'s passes stand in a tree that lost their behaviour.

        One mutant tree serves the whole roster.  The only conclusion drawn
        is the withholding one — this check passed although the behaviour
        every passing criterion of the roster names had been removed — and
        removing MORE makes that conclusion stronger, never weaker.  A check
        that fails in the joint tree proves less, which is why a failure
        withholds nothing.
        """
        passing = passing_checks(grade.results)
        if not passing:
            return frozenset()
        withheldable = [criterion for criterion in criteria if criterion.id in passing]
        async with owned_workspace(
            self._workspace,
            ref=graded_sha,
            repo_path=repo_path,
            cache_key=cache_key,
        ) as mutant:
            await self._remove(criteria=withheldable, workspace=mutant)
            if await read_workspace_head(git=self._git, workspace=mutant) != (
                graded_sha,
                True,
            ):
                # Still at the sha AND holding changes, or the tree the
                # second grading would read is not a tree that lost
                # anything: a removal that committed, moved the head or
                # edited nothing took no reading at all.
                await self._log.awarning(
                    "mutation_took_no_reading",
                    site="mutation_removal",
                    graded_sha=graded_sha,
                )
                return frozenset()
            mutated = await self._regrade(
                evaluation_prompt=evaluation_prompt, workspace=mutant
            )
        if mutated is None:
            return frozenset()
        return surviving_checks(clean=grade.results, mutated=mutated.criteria_results)

    async def _remove(
        self, *, criteria: Sequence[ExecutionCriterion], workspace: str
    ) -> None:
        """Take the behaviour the passing criteria name out of *workspace*.

        No output format and no structured answer: what this session claims
        about its own work is worth nothing to the reading, which is taken
        off the tree afterwards.
        """
        prompt = self._prompts.template_for(PromptKey.MUTATION_SURVIVAL).render(
            execution_criteria_variables(criteria),
        )
        await drain(
            self._runner.stream_in_workspace(
                prompt=prompt,
                workspace_path=workspace,
                permission_mode=UNATTENDED_PERMISSION_MODE,
                allowed_tools=ToolPreset.IMPLEMENTATION,
                skills=self._prompts.session_skills(
                    PromptKey.MUTATION_SURVIVAL, self._skills
                ),
                session_type=SessionType.TICKET_FIRE,
                agents=NO_SUBAGENTS,
                session_policy=self._prompts.session_policy(
                    PromptKey.MUTATION_SURVIVAL
                ),
            ),
            site="mutation_removal",
        )

    async def _regrade(
        self, *, evaluation_prompt: str, workspace: str
    ) -> AcceptanceCriteriaOutput | None:
        """Put the clean tree's question to the mutant tree, byte for byte.

        The instrument that produced the pass is the instrument asked again,
        with the same words, about a tree that differs in the behaviour the
        criteria name.  One attempt and no fan-in: a grading that came back
        with nothing, or without an answer for a criterion, is no reading of
        that criterion and leaves its verdict standing.
        """
        result_event, _ = await drain(
            self._runner.stream_in_workspace(
                prompt=evaluation_prompt,
                workspace_path=workspace,
                permission_mode=EVAL_PERMISSION_MODE,
                allowed_tools=ToolPreset.EVALUATION,
                skills=self._prompts.session_skills(PromptKey.EVALUATION, self._skills),
                session_type=SessionType.TICKET_FIRE,
                agents=NO_SUBAGENTS,
                session_policy=self._prompts.session_policy(PromptKey.EVALUATION),
                output_format={
                    "type": "json_schema",
                    "schema": ACCEPTANCE_CRITERIA_SCHEMA,
                },
            ),
            site="mutation_evaluator",
        )
        if result_event is None or result_event.structured_output is None:
            await self._log.awarning(
                "mutation_grading_took_no_reading", site="mutation_evaluator"
            )
            return None
        try:
            return AcceptanceCriteriaOutput.model_validate(
                result_event.structured_output
            )
        except ValidationError:
            # A shape this grading cannot be read from is no reading of any
            # criterion, which is the same answer as no output at all: the
            # clean grading's verdicts stand untouched.
            await self._log.awarning(
                "mutation_grading_took_no_reading", site="mutation_evaluator"
            )
            return None
