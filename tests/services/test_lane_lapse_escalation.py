"""The lapse question's own component: its address, its window, and its silence.

Driven directly, without a loop: which occurrence the question is addressed
under, what a refuted judgement does to the act, and what an empty transition
costs are facts about this component, and a loop around it would only make
them harder to read.
"""

import pytest

from kodezart.domain.amendment import NativeWriteRefusalError
from kodezart.domain.comment_markers import compose_comment_marker
from kodezart.services.agent_service import AgentService
from kodezart.services.lane_lapse_escalation import (
    LaneLapseEscalations,
    lapse_question,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import (
    CriterionCrossOff,
    CrossOffState,
    RederivationClass,
)
from kodezart.types.domain.gating import RepoVisibility
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.run_state import LaneBinding, LaneEscalation
from tests.chains.test_native_fire import (
    DIRECT_OWED,
    NESTED_OWED,
    SUBJECT,
    NativeExecutor,
    native_operation,
    tracker,
)
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeChangePersister,
    FakeWorkspaceProvider,
    PassThroughGate,
    make_prompt_provider,
)
from tests.lane_fixture import LaneGit, LaneRepo

HOLDER = "actual-parent-job"
REPO_URL = "https://github.com/owner/repo"
BRANCH = "ralph/fire-subject"
#: The commit a grading was taken at, which the head has since left behind.
GRADED_SHA = "1" * 40
EXERCISED = "src/kodezart/domain/"
REFUTED = {
    "verdict": "refuted",
    "evidence": "The landed question names a criterion this tree does not hold.",
    "cited_refs": ["policy.py"],
}


def observed(key: str) -> CriterionCrossOff:
    """A passing grading resting on an observation, over one prefix."""
    return CriterionCrossOff(
        criterion=key,
        state=CrossOffState.passed,
        rederivation_class=RederivationClass.observed,
        exercised_paths=(EXERCISED,),
        evidence=CriterionEvidence(
            graded_sha=GRADED_SHA,
            test="tests/services/test_lane_lapse_escalation.py::test_one_question",
        ),
    )


def marker(key: str) -> str:
    """The address the question about *key* is written under on this lane."""
    return compose_comment_marker(
        prefixes=native_operation().marker_prefixes,
        purpose="escalation",
        lane=SUBJECT,
        occurrence_key=f"{key}:lapse",
    )


class Raiser:
    """The component over the tracker double, and the head it judges at."""

    def __init__(self, *, findings=None) -> None:
        self.port = tracker()
        self.repo = LaneRepo(branch=BRANCH)
        self.git = LaneGit(self.repo)
        self.head_sha = self.repo.commit()
        self.workspace = FakeWorkspaceProvider(git=self.git)
        self.executor = NativeExecutor([])
        self.executor.findings = list(findings or [])
        self.service = LaneLapseEscalations(
            tracker=self.port,
            operation=native_operation(),
            runner=AgentService(
                git_base_url="https://github.com",
                executor=self.executor,
                workspace=self.workspace,
                persister=FakeChangePersister(),
            ),
            workspace=self.workspace,
            git=self.git,
            prompts=make_prompt_provider(),
            skills=SUPPRESS_ALL_SKILLS,
            gate=PassThroughGate(),
            lease_seconds=900,
        )

    @property
    def lane(self) -> LaneBinding:
        return LaneBinding(
            lane_key=SUBJECT,
            body_digest="the subject text this run entered on",
            loop_branch=BRANCH,
            deliverable_branch="feature/fire-subject",
            base=trunk_base("main"),
            repo_url=REPO_URL,
            repo_path=None,
            run_id=HOLDER,
            visibility=RepoVisibility.PUBLIC,
        )

    async def raise_lapses(self, *cross_offs) -> None:
        await self.service.raise_lapses(
            lane=self.lane, lapsed=cross_offs, head_sha=self.head_sha
        )

    def occurrences(self) -> list[str]:
        prefix = native_operation().marker_prefixes["escalation"]
        return [
            comment.body.partition("\n")[0]
            for comment in self.port.comments
            if comment.body.startswith(f"[{prefix}:")
        ]


def test_the_question_is_addressed_under_the_criterion_identity_and_nothing_else():
    """The occurrence key carries the criterion and the purpose, and no sha.

    Composing the question is arithmetic, so it is asked of the plain
    function with no window around it. The key carries nothing that moves
    between iterations or between runs: a question asked again composes the
    same address, which is what lets the existing idempotent comment write
    rewrite one question rather than add a second.
    """
    first = lapse_question(lane_key=SUBJECT, cross_off=observed(DIRECT_OWED))
    again = lapse_question(lane_key=SUBJECT, cross_off=observed(DIRECT_OWED))

    assert first.escalation_key == f"{DIRECT_OWED}:lapse"
    assert first == again
    assert first.issue_id == SUBJECT
    assert first.raised_by == RunKind.FIRE.value
    assert first.raised_at_sha == GRADED_SHA
    assert first.interim_basis == EXERCISED
    assert HOLDER not in first.model_dump_json()


async def test_the_marker_the_service_addresses_is_the_marker_the_board_holds():
    """One question lands, under the address the criterion's own key composes.

    The window re-reads the surface the step declared it wrote, so a service
    addressing one marker while its writer wrote another could not complete
    the read at all. What the board holds afterwards is asserted here as well,
    because that is the address a reader of this lane goes to.
    """
    raiser = Raiser()

    await raiser.raise_lapses(observed(DIRECT_OWED))

    assert raiser.occurrences() == [marker(DIRECT_OWED)]
    landed = LaneEscalation.model_validate_json(
        raiser.port.comments[-1].body.partition("\n")[2]
    )
    assert landed == lapse_question(lane_key=SUBJECT, cross_off=observed(DIRECT_OWED))
    assert raiser.port.classification_writes == [(SUBJECT, "decision")]
    assert len(raiser.executor.judge_sessions) == 1


async def test_each_grading_that_lapsed_at_one_head_gets_its_own_question():
    """Two gradings lapse at one head, and each is asked about under its own address.

    A lapse is a take-back, so a call that asked about the first cross-off and
    stopped would put the rest back on the board with nothing on the lane
    saying why. The field the loop hands over holds one entry per observed
    grading whose prefixes moved, so the plural is the ordinary shape of this
    call and not a hypothetical one.
    """
    raiser = Raiser()

    await raiser.raise_lapses(observed(DIRECT_OWED), observed(NESTED_OWED))

    assert raiser.occurrences() == [marker(DIRECT_OWED), marker(NESTED_OWED)]
    assert len(raiser.executor.judge_sessions) == 2


async def test_a_refuted_question_refuses_the_act_and_raises_nothing_further():
    """One round, no repair arm, and the questions after it are not asked.

    There is nothing to author differently the second time a question is
    asked, so a judgement that does not uphold what landed ends the act. The
    second lapsed grading of the same call is never written, which is what
    keeps a refusal from being a partial raise nobody reads.
    """
    raiser = Raiser(findings=[REFUTED])

    with pytest.raises(NativeWriteRefusalError, match="not upheld at the head"):
        await raiser.raise_lapses(observed(DIRECT_OWED), observed(NESTED_OWED))

    assert len(raiser.executor.judge_sessions) == 1
    assert raiser.occurrences() == [marker(DIRECT_OWED)]


async def test_no_lapse_opens_no_session_and_writes_nothing():
    """An iteration with no transition costs the lane neither a write nor a session.

    The loop calls this per iteration that produced a lapse, so the empty
    case has to be free: a component that opened a judging session to write
    nothing would put a session on the critical path of every iteration.
    """
    raiser = Raiser()

    await raiser.raise_lapses()

    assert raiser.executor.judge_sessions == []
    assert raiser.port.comments == []
    assert raiser.port.classification_writes == []
    assert raiser.workspace.acquisitions == []
