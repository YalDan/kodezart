"""One cross-off per graded criterion, as arithmetic over the grade.

The evaluation session is the judgement; this module is what that verdict
becomes. It makes no judgement of its own: the state is the result's own
pass where that pass is a reading of the branch — a pass of a check that
already passed at the lane's base is a reading of the base and not of the
branch — the sha is the commit the workspace was graded at, and the Evidence
pointer names the session and the iteration that produced the verdict
rather than repeating the evaluator's prose, which nothing reads back.

The stale-write precondition below compares the addressed issue's own Check
body with the criterion's text. That is not a lookup: the caller arrived
holding the target's key, and a comparison that confirms a target is not one
that finds one. Nothing here locates a criterion by prose or by checkbox.
"""

from collections.abc import Collection, Mapping, Sequence
from types import MappingProxyType
from typing import Final

from kodezart.domain.errors import StaleWriteError
from kodezart.domain.fire_spec import (
    criterion_field_bodies,
    criterion_ref,
    duplicated_row_labels,
)
from kodezart.domain.lapse import GradedState
from kodezart.types.domain.agent import (
    AcceptanceCriteriaOutput,
    BaseCheckOutput,
    CriterionResult,
)
from kodezart.types.domain.criteria import CriterionId, TrackerCriterion
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.criterion_lifecycle import (
    PATH_BOUND_CLASSES,
    CriterionCrossOff,
    CrossOffState,
    ExercisedPath,
    RederivationClass,
    UndemonstratedReason,
)
from kodezart.types.domain.criterion_ref import CriterionRef
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind

#: The state a criterion a fire finished sits in until something takes it back.
#:
#: The fire's own evaluation step is what moved it, so such a criterion is
#: still inside the obligation the fire took on: reading it as gone would
#: make finishing work indistinguishable from the board cancelling it. The
#: barrier that keeps it in the fire's current set and the writer that takes
#: it back are two readings of this one state, so it is stated once.
HELD_CRITERION_STATE = WorkflowStateKind.COMPLETED

#: The states a criterion sub-issue can be ticked from.
#:
#: Unstarted is the ordinary first tick. Completed is a re-grade of a
#: criterion an earlier iteration of this same run already ticked, which
#: restamps its Evidence at the new head. Any other state means the board
#: took the criterion somewhere this verdict does not address.
TICKABLE_STATES = frozenset({WorkflowStateKind.UNSTARTED, HELD_CRITERION_STATE})


def undemonstrated_reasons(
    *,
    results: Sequence[CriterionResult],
    workspace_stood: bool,
    surviving_checks: Collection[CriterionId],
) -> dict[CriterionId, UndemonstratedReason]:
    """Which of this attempt's readings proved nothing, and which one failed.

    The workspace reading is about the whole tree, so when it fails nothing
    read in that tree stands and every criterion carries it — the mutation
    reading included, which is taken in a copy of that same tree. Otherwise
    the readings are one criterion at a time: a check that passed with the
    behaviour it names gone read nothing about that behaviour.
    """
    if not workspace_stood:
        return {
            result.criterion_id: UndemonstratedReason.workspace_not_the_graded_sha
            for result in results
        }
    return {
        result.criterion_id: UndemonstratedReason.check_survived_mutation
        for result in results
        if result.criterion_id in surviving_checks
    }


def evaluation_observation(*, session_id: str, iteration: int) -> str:
    """The pointer an Evidence row carries back to the grading it came from."""
    return f"evaluator session {session_id}, iteration {iteration}"


#: What stands in for a verdict this attempt did not ask for, because the
#: verdict an earlier one reached still stands.
#:
#: Fixed text, for the reason the undemonstrated sentences in
#: ``criteria_grading`` are: this is the harness's arithmetic over what moved
#: since that grading, not a session's reading of anything, so a session's
#: prose would misattribute it.
CARRIED_REASON: Final[str] = (
    "carried: nothing this grading exercised moved since the sha it was "
    "graded at, so the verdict it reached still stands and was not asked again"
)

#: What stands in for a verdict whose grading no longer stands.
LAPSE_REASON: Final[str] = (
    "lapsed: what this grading exercised moved after the sha it was graded "
    "at, so the verdict it reached is owed again rather than failed"
)


def iteration_output(
    *,
    criteria: Sequence[TrackerCriterion],
    standing: Sequence[CriterionCrossOff],
    reading: Mapping[CriterionRef, GradedState],
    graded: AcceptanceCriteriaOutput,
) -> AcceptanceCriteriaOutput:
    """The whole roster's reading: this session's rows, plus the standing ones.

    A session is asked only about what this iteration may grade, so its own
    output answers a subset of the roster. What the gate and the trajectory
    read has to answer the roster entire, or the denominator moves between
    iterations and acceptance is over whatever the session happened to be
    handed. So each withheld criterion gets the row its standing grading
    earns — passing for one that still stands, not passing for one that has
    lapsed — carrying that grading's own class and prefixes so the next
    iteration reads the same declaration back.

    The session's rows are passed through untouched, including a second row
    for one id and a row for an id nobody dispatched: both are the reconciler's
    to report, and filtering them here would hide a hallucinated roster behind
    a complete-looking one. A row for a criterion this session was NOT asked
    about is the one exception — the harness's reading of that criterion is
    the row above, and a session answering an obligation it was not handed
    does not get to contradict it.
    """
    held = {cross_off.criterion: cross_off for cross_off in standing}
    answered: dict[CriterionId, list[CriterionResult]] = {}
    for result in graded.criteria_results:
        answered.setdefault(result.criterion_id, []).append(result)
    rows: list[CriterionResult] = []
    for criterion in criteria:
        state = reading.get(criterion_ref(criterion.id))
        if state is None:
            rows.extend(answered.pop(criterion.id, ()))
            continue
        answered.pop(criterion.id, None)
        prior = held[criterion_ref(criterion.id)]
        rows.append(
            CriterionResult(
                criterion_id=criterion.id,
                criterion=criterion.text,
                passed=state is GradedState.counted,
                reasoning=(
                    CARRIED_REASON if state is GradedState.counted else LAPSE_REASON
                ),
                rederivation_class=prior.rederivation_class,
                exercised_paths=prior.exercised_paths,
            )
        )
    for unknown in answered.values():
        rows.extend(unknown)
    return AcceptanceCriteriaOutput(
        criteria_results=rows, sherlock_flags=list(graded.sherlock_flags)
    )


#: What a pointer says once the grading it names no longer stands.
LAPSE_POINTER: Final[str] = "that grading lapsed"


def lapse_observation(*, observation: str) -> str:
    """The pointer a lapsed grading leaves, beside the sha it was graded at.

    The sha is not moved on and not cleared: a reader of the row must see
    the gap between what was graded and where the branch went, which is
    exactly what a sha with no pointer to a lapse cannot show. The pointer
    keeps the grading it came from, so the reading stays traceable to the
    session that produced the verdict that lapsed.
    """
    return f"{observation} — {LAPSE_POINTER}"


def declared_class(
    *, rederivation_class: RederivationClass, exercised_paths: Sequence[str]
) -> tuple[RederivationClass, tuple[ExercisedPath, ...]]:
    """The class and prefixes a declaration earns, normalised where it earns none.

    A path-bound class is an exemption from re-derivation on every head
    move, and the prefixes are what says when that exemption stops holding.
    A declaration of one without them claims the exemption and gives no way
    to end it, so it earns neither: it reads cheap, with no prefixes, and
    the next head move re-derives it like any other cheap grading.

    Read here rather than refused: the cross-off model raises on that pair,
    and raising would take the whole fire down over one answer a session
    gave. A prefix naming nothing is dropped for the same reason.
    """
    paths = tuple(path for path in exercised_paths if path.strip())
    if rederivation_class in PATH_BOUND_CLASSES and not paths:
        return RederivationClass.cheap, ()
    return rederivation_class, paths


def tick_anchor(criterion: TrackerCriterion) -> str:
    """What a tick asserts about the sub-issue it addresses, as one line."""
    return (
        f"the unstarted or completed criterion {criterion.id} carrying the "
        f"Check {criterion.text!r}, at most one Evidence row and no template "
        f"field written twice"
    )


def require_tickable(*, issue: TrackerIssue, criterion: TrackerCriterion) -> None:
    """Refuse a sub-issue the verdict in hand no longer addresses.

    Read from the sub-issue as it stands rather than from what the
    evaluator remembered, so a criterion reclassified, moved or amended
    between the grading and this write takes no part of it. The refusal is
    the description surface's own stale-write error, because the tick's
    first act is a compare-and-set on that body.

    Every condition is one the write itself depends on, the body's rows
    among them: the Evidence row is set field-scoped, and a body carrying a
    template field twice names no single row for that edit to set, so it is
    the codec's own rule that is asked here rather than one row of it. A
    body with no Evidence row is tickable — the edit appends the row it
    finds absent.

    What this cannot see: a criterion someone moved INTO the finished state
    during the fire reads exactly like one the fire itself finished, because
    the roster carries no state and the Evidence row carries no run. Such a
    criterion is re-graded and, on a fail, taken back. Nothing but the
    evaluation step moves a criterion to that state, so this is a protocol
    violation on the board rather than a case to decide here.
    """
    if (
        "criterion" not in issue.issue_labels
        or issue.state_kind not in TICKABLE_STATES
        or criterion_field_bodies(issue.body, field="Check") != (criterion.text,)
        or duplicated_row_labels(issue.body)
    ):
        raise StaleWriteError(target=criterion.id, expected=tick_anchor(criterion))


def passed_ids(results: Sequence[CriterionResult]) -> frozenset[CriterionId]:
    """The ids this attempt read as passing."""
    return frozenset(result.criterion_id for result in results if result.passed)


def base_answers(output: BaseCheckOutput) -> dict[CriterionId, bool]:
    """One answer per id the base reading settled, and none for any it did not.

    An id answered twice is the reading contradicting itself, so neither
    answer stands and that id has no reading. An id nobody dispatched is
    carried: it matches no result below, so it decides nothing, and dropping
    it here would be a second reconciliation of the dispatched set.
    """
    answers: dict[CriterionId, bool] = {}
    answered_twice: set[CriterionId] = set()
    for result in output.base_check_results:
        if result.criterion_id in answers:
            answered_twice.add(result.criterion_id)
        answers[result.criterion_id] = result.satisfied_at_base
    return {
        criterion_id: answer
        for criterion_id, answer in answers.items()
        if criterion_id not in answered_twice
    }


def demonstrated_criteria(
    *,
    results: Sequence[CriterionResult],
    graded_tree_stood: bool,
    at_base: Mapping[CriterionId, bool],
) -> frozenset[CriterionId]:
    """The dispatched criteria whose reading stands as this branch's.

    A grading read from a tree the sha does not name stands for nothing at
    all, so no id does. Otherwise a fail stands on the graded tree alone — a
    criterion this fire finished and has now broken is taken back whatever
    the base says — and a pass stands only where the base reading found that
    same check failing there. ``at_base.get(id) is False`` and never ``not
    at_base.get(id)``: absent and false are the two answers this function
    exists to tell apart, and absent — no reading at all, or an id the
    reading left out — fails closed.
    """
    if not graded_tree_stood:
        return frozenset()
    return frozenset(
        result.criterion_id
        for result in results
        if not result.passed or at_base.get(result.criterion_id) is False
    )


def cross_off_state(
    *, passed: bool, reason: UndemonstratedReason | None
) -> CrossOffState:
    """What one result is worth, given which reading of its tree failed.

    *reason* decides before the result does: a verdict produced where no
    reading of the tree says anything about this criterion is neither its
    pass nor its fail, and recording it as either would put a claim about
    the branch on the board that nothing on the branch supports.  The
    function names the thing it decides on, because the state now has more
    than one trigger.
    """
    if reason is not None:
        return CrossOffState.undemonstrated
    return CrossOffState.passed if passed else CrossOffState.failed


def cross_offs_for(
    *,
    results: Sequence[CriterionResult],
    graded_sha: str,
    observation: str,
    reasons: Mapping[CriterionId, UndemonstratedReason],
    standing: Sequence[CriterionCrossOff] = (),
    reading: Mapping[CriterionRef, GradedState] = MappingProxyType({}),
) -> tuple[CriterionCrossOff, ...]:
    """The only site in the source that builds a cross-off and its evidence.

    One pair of facts serves the whole attempt: the sha and the session
    pointer are the attempt's, not each criterion's, so a second reading of
    either per criterion would be a second place for the same sha to drift
    from.

    *reasons* is per criterion, because a reading can fail for one
    criterion of an attempt and hold for the next. It is consulted for a
    criterion this attempt graded afresh and for no other: a counted or
    lapsed criterion was not read by this attempt at all, so no reading of
    this attempt can have failed for it.

    *reading* is what an earlier grading is still worth, for the criteria
    this attempt therefore did not grade afresh; *standing* carries those
    gradings. A criterion the reading counts keeps its own cross-off
    unchanged, sha and pointer included — arithmetic may not restate a
    verdict it did not reach. A criterion the reading finds lapsed becomes
    a lapsed cross-off at the sha it was graded at, with its class and its
    prefixes carried, so what the board is told is that the grading is owed
    again rather than that it failed.

    Each arm decides the five facts a cross-off carries and one construction
    below makes the value out of them, so the state, the sha and the class
    reach the board through one expression however they were reached.
    """
    attempt = (graded_sha, observation)
    held = {cross_off.criterion: cross_off for cross_off in standing}
    if not reading.keys() <= held.keys():
        raise ValueError("a standing reading names a criterion nothing is standing for")
    built: list[CriterionCrossOff] = []
    exercised_paths: tuple[ExercisedPath, ...]
    for result in results:
        criterion = criterion_ref(result.criterion_id)
        state = reading.get(criterion)
        if state is GradedState.counted:
            built.append(held[criterion])
            continue
        if state is None:
            withheld = reasons.get(result.criterion_id)
            verdict = cross_off_state(passed=result.passed, reason=withheld)
            recorded, pointer = attempt
            rederivation_class, exercised_paths = declared_class(
                rederivation_class=result.rederivation_class,
                exercised_paths=result.exercised_paths,
            )
        else:
            prior = held[criterion]
            withheld = None
            verdict = CrossOffState.lapsed
            recorded = prior.evidence.graded_sha
            pointer = lapse_observation(observation=prior.evidence.test)
            rederivation_class = prior.rederivation_class
            exercised_paths = prior.exercised_paths
        built.append(
            CriterionCrossOff(
                criterion=criterion,
                state=verdict,
                evidence=CriterionEvidence(graded_sha=recorded, test=pointer),
                rederivation_class=rederivation_class,
                exercised_paths=exercised_paths,
                undemonstrated_reason=withheld,
            )
        )
    return tuple(built)
