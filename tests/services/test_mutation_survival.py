"""The same question asked of two trees, driven through the real reader."""

from pathlib import Path

import pytest

from kodezart.domain.criteria_grading import grade_iteration
from kodezart.domain.mutation_survival import passing_checks
from kodezart.services.mutation_survival import MutationSurvivalReader
from kodezart.types.domain.agent import AcceptanceCriteriaOutput, ResultEvent
from kodezart.types.domain.criteria import CriterionId, TrackerCriterion
from kodezart.types.domain.grading import IterationGrade
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeGitService, make_prompt_provider
from tests.mutation_fixture import (
    MUTATION_REMOVAL_LINE,
    FixtureWorkspaces,
    fixture_evaluation,
    remove_the_behaviour,
)

#: The roster the reader is driven over, and the check each criterion names.
CHECKS = {"AC-wired": "wired", "AC-tautology": "tautology"}
#: The sha every tree in this fixture stands at.
GRADED_SHA = "a" * 40
#: The clone the copies are cut from: never a tree a reading is taken in.
REPO = "/tmp/fixture-repo"
#: The evaluation the clean grading came from, put again byte for byte.
EVALUATION_PROMPT = "Grade the roster against this tree."


def criteria() -> tuple[TrackerCriterion, ...]:
    return tuple(
        TrackerCriterion(id=CriterionId(key), text=f"the check {name} names")
        for key, name in CHECKS.items()
    )


class FixtureExecutor:
    """Answers a removal by editing the tree and a grading by reading it.

    What each session leaves behind is the whole script: the removal's
    product is the tree it edited, and the grading's answer is the outcome of
    really running each check in the tree it was streamed in.
    """

    def __init__(
        self,
        *,
        removes=None,
        grades: bool = True,
        structured: object | None = None,
    ) -> None:
        self._removes = removes
        self._grades = grades
        self._structured = structured
        self.removals: list[tuple[str, str]] = []
        self.gradings: list[tuple[str, str]] = []

    async def stream(self, **kwargs):
        prompt, cwd = kwargs["prompt"], kwargs["cwd"]
        if MUTATION_REMOVAL_LINE in prompt:
            self.removals.append((prompt, cwd))
            if self._removes is not None:
                self._removes(Path(cwd))
            output = None
        else:
            self.gradings.append((prompt, cwd))
            output = (
                fixture_evaluation(Path(cwd), CHECKS)
                if self._grades
                else self._structured
            )
        yield ResultEvent(
            subtype="result",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="fixture-session",
            structured_output=output,
        )

    async def stream_in_workspace(self, *, prompt, workspace_path, **rest):
        async for event in self.stream(prompt=prompt, cwd=workspace_path, **rest):
            yield event


class FixtureGit(FakeGitService):
    """Head and dirtiness per tree, both read off the trees themselves."""

    def __init__(self, workspaces: FixtureWorkspaces) -> None:
        super().__init__()
        self._workspaces = workspaces
        #: Trees standing at a commit other than the sha they were cut at.
        self.heads: dict[str, str] = {}

    async def current_sha(self, cwd: str) -> str:
        self.calls.append(("current_sha", cwd))
        return self.heads.get(cwd, GRADED_SHA)

    async def has_changes(self, cwd: str) -> bool:
        self.calls.append(("has_changes", cwd))
        return self._workspaces.differs(cwd)


def clean_grade(tree: Path) -> IterationGrade:
    """The grade the clean tree really produced, over the real checks."""
    return grade_iteration(
        criteria(),
        AcceptanceCriteriaOutput.model_validate(fixture_evaluation(tree, CHECKS)),
    )


def reader(executor, git) -> MutationSurvivalReader:
    return MutationSurvivalReader(
        runner=executor,
        workspace=git._workspaces,
        git=git,
        prompts=make_prompt_provider(),
        skills=SUPPRESS_ALL_SKILLS,
    )


async def graded(tmp_path: Path) -> tuple[FixtureWorkspaces, Path, IterationGrade]:
    """The clean grading, taken in a tree this fixture really cut and read."""
    workspaces = FixtureWorkspaces(root=tmp_path)
    tree = Path(await workspaces.acquire(ref=GRADED_SHA, repo_path=REPO))
    return workspaces, tree, clean_grade(tree)


async def survivors(
    workspaces: FixtureWorkspaces, grade: IterationGrade, executor
) -> frozenset[CriterionId]:
    git = FixtureGit(workspaces)
    return await survivors_with(workspaces, grade, executor, git)


async def survivors_with(
    workspaces: FixtureWorkspaces, grade: IterationGrade, executor, git
) -> frozenset[CriterionId]:
    return await reader(executor, git).survivors(
        criteria=criteria(),
        grade=grade,
        evaluation_prompt=EVALUATION_PROMPT,
        graded_sha=GRADED_SHA,
        repo_path=REPO,
        cache_key=None,
    )


async def test_no_session_opens_when_the_attempt_passed_nothing(tmp_path):
    """There is no pass to withhold, so nothing is asked and nothing is cut."""
    workspaces, _, _ = await graded(tmp_path)
    failed = grade_iteration(
        criteria(),
        AcceptanceCriteriaOutput.model_validate(
            {
                "criteriaResults": [
                    {
                        "criterionId": key,
                        "criterion": "an evaluator echo",
                        "passed": False,
                        "reasoning": "Observed the selected check.",
                    }
                    for key in CHECKS
                ]
            }
        ),
    )
    executor = FixtureExecutor()

    assert await survivors(workspaces, failed, executor) == frozenset()
    assert (executor.removals, executor.gradings) == ([], [])
    assert len(workspaces.acquired) == 1


async def test_the_mutant_tree_is_asked_the_clean_trees_question_unchanged(tmp_path):
    """The instrument that produced the pass is the instrument asked again.

    Byte for byte: a paraphrase would be a second question, and a difference
    in the answers would then be a difference in the questions.
    """
    workspaces, tree, grade = await graded(tmp_path)
    executor = FixtureExecutor(removes=remove_the_behaviour)

    await survivors(workspaces, grade, executor)

    assert [prompt for prompt, _ in executor.gradings] == [EVALUATION_PROMPT]
    mutant = executor.gradings[0][1]
    assert mutant != str(tree)
    assert mutant != REPO


async def test_the_removal_session_is_told_which_criteria_to_take_apart(tmp_path):
    """Every passing criterion's id reaches the prompt the session is handed.

    The template says a removal is wanted; which behaviour to remove is the
    roster bound into it, and that binding is where "remove the behaviour that
    criterion names" becomes production code. Bound to nothing, the session is
    asked to take nothing apart, and a tree that lost nothing withholds
    nothing — so the failure this pins is silent everywhere else.
    """
    workspaces, _, grade = await graded(tmp_path)
    executor = FixtureExecutor(removes=remove_the_behaviour)

    await survivors(workspaces, grade, executor)

    passing = passing_checks(grade.results)
    assert passing == frozenset(CriterionId(key) for key in CHECKS)
    prompt = executor.removals[0][0]
    assert {key for key in passing if key in prompt} == passing


async def test_the_mutant_workspace_is_acquired_at_the_graded_sha_and_released(
    tmp_path,
):
    """The sha, not a branch: a copy following a branch is a copy of whatever
    that branch became, and the reading would then be of another tree."""
    workspaces, _, grade = await graded(tmp_path)
    executor = FixtureExecutor(removes=remove_the_behaviour)

    await survivors(workspaces, grade, executor)

    assert [ref for _, ref in workspaces.acquired] == [GRADED_SHA, GRADED_SHA]
    assert workspaces.released == [workspaces.acquired[-1][0]]


async def test_a_reading_taken_in_the_mutant_tree_withholds_the_surviving_check(
    tmp_path,
):
    """The whole measurement, over source that is really run in both trees."""
    workspaces, _, grade = await graded(tmp_path)
    executor = FixtureExecutor(removes=remove_the_behaviour)

    assert await survivors(workspaces, grade, executor) == {CriterionId("AC-tautology")}


async def test_a_removal_that_changed_nothing_takes_no_reading(tmp_path):
    """No tree differed, so nothing was read and no second grading runs."""
    workspaces, _, grade = await graded(tmp_path)
    executor = FixtureExecutor()

    assert await survivors(workspaces, grade, executor) == frozenset()
    assert executor.removals
    assert executor.gradings == []


@pytest.mark.parametrize(
    "edits",
    [
        pytest.param(remove_the_behaviour, id="moved-the-head"),
        pytest.param(None, id="committed"),
    ],
)
async def test_a_removal_that_left_the_graded_sha_takes_no_reading(tmp_path, edits):
    """A copy at another commit is a copy of another tree.

    Both arms of the same fact: a session that moved the head left a tree the
    graded sha does not name, and one that committed its removal left a clean
    tree at a commit of its own. Neither is the tree the reading is about.
    """
    workspaces, _, grade = await graded(tmp_path)
    executor = FixtureExecutor(removes=edits)
    git = FixtureGit(workspaces)

    def move_the_head(path: str) -> None:
        git.heads[path] = "0" * 40

    workspaces.on_acquired = move_the_head

    assert await survivors_with(workspaces, grade, executor, git) == frozenset()
    assert executor.gradings == []


async def test_no_second_grading_runs_when_no_reading_was_taken(tmp_path):
    """Stated as its own property: the gate is before the second session.

    A gate after it would spend an evaluation on a tree it had already
    decided said nothing.
    """
    workspaces, _, grade = await graded(tmp_path)
    executor = FixtureExecutor()

    await survivors(workspaces, grade, executor)

    assert len(executor.removals) == 1
    assert executor.gradings == []


@pytest.mark.parametrize(
    "structured",
    [
        pytest.param(None, id="no-structured-output"),
        pytest.param({"criteriaResults": []}, id="a-shape-it-cannot-be-read-from"),
    ],
)
async def test_a_mutant_grading_with_no_structured_output_takes_no_reading(
    tmp_path, structured
):
    """A grading that came back with nothing is no reading of any criterion."""
    workspaces, _, grade = await graded(tmp_path)
    executor = FixtureExecutor(
        removes=remove_the_behaviour, grades=False, structured=structured
    )

    assert await survivors(workspaces, grade, executor) == frozenset()
