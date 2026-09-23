"""Capture native subjects and read current obligations at fire barriers."""

from collections.abc import Mapping, Sequence

from langchain_core.runnables import RunnableConfig

from kodezart.core.errors import (
    TrackerAccessDeniedError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import FireCriteriaReader, FireCriteriaSource, TrackerPort
from kodezart.domain.criterion_cross_off import HELD_CRITERION_STATE
from kodezart.domain.errors import (
    CriterionReadError,
    FireSpecEntryError,
    InvalidFireCriterionError,
    TransientAPIError,
)
from kodezart.domain.fire_spec import criterion_check, tracker_spec_from_issues
from kodezart.domain.lane_entry import require_unamended_subject
from kodezart.domain.workflow_state import recorded_native_roster
from kodezart.services.scope_membership import read_scope_members
from kodezart.types.domain.criteria import (
    CriterionId,
    ExecutionCriterion,
    TrackerCriterion,
    TrackerCriterionSet,
)
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.lane_entry import DeliverOnlyLane
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind, is_open
from kodezart.types.domain.workflow import WorkflowState

#: The state a criterion the fire still owes sits in at head.
#:
#: The tracker owns the vocabulary and ``Todo`` is its unstarted kind. A
#: criterion in any other kind is finished, abandoned, or already being
#: worked somewhere else, and re-validating it here would put this fire on
#: the hook for an obligation the board does not hold it to.
OWED_CRITERION_STATE = WorkflowStateKind.UNSTARTED

#: What a tracker read failing to arrive looks like, named once.
#:
#: Every reading in this module turns the same transport classes into the
#: same typed refusal, so an outage can never be mistaken for an answer
#: about what a subtree holds. Naming the tuple once keeps the three
#: readings from drifting into disagreeing about which failures those are.
_TRANSPORT_FAILURES = (
    ConnectionError,
    TimeoutError,
    TransientAPIError,
    TrackerUnavailableError,
    TrackerAccessDeniedError,
)


def _criterion_set(checks: Mapping[str, str]) -> TrackerCriterionSet:
    """The one shaping of a reading's result, so the readings compare equal.

    A barrier compares the set it holds with the set a later reading
    answers, by identity and Check text in order; two construction sites
    could order or spell them differently and turn a lane that is exactly
    on track into a refusal.
    """
    return TrackerCriterionSet(
        criteria=[
            TrackerCriterion(id=CriterionId(key), text=check)
            for key, check in checks.items()
        ],
    )


def held_roster(criteria: Sequence[ExecutionCriterion]) -> TrackerCriterionSet | None:
    """The tracker roster a caller already holds, as a set or as nothing.

    A caller carries its roster as execution criteria, which on the authored
    arm are of another kind entirely; a caller holding none of this arm's
    criteria holds no roster, and that is the entry-shaped read rather than
    an empty one.
    """
    held = [
        criterion for criterion in criteria if isinstance(criterion, TrackerCriterion)
    ]
    return TrackerCriterionSet(criteria=held) if held else None


class TrackerCriteria:
    """A fire's criteria, read from the tracker at each execution barrier.

    The subtree IS the source, and the spec read is what admits a subject
    to it and guards it against shrinking: the entry establishes that
    admission through the port and reads the subject's subtree once, the
    spec it captures is composed from that one reading, and a barrier
    refuses a fire whose subtree has since lost a criterion that spec
    names.  So nothing a caller carries alongside it can stand in for that
    read, and no second, narrower reading of what the subject holds exists
    for a fire to be refused by.

    What the fire OWES is then its subtree's own Todo criterion
    sub-issues.  A deliverable child's criterion sits inside the exit
    condition and outside the subject's direct family, so a barrier
    reading only the direct family would be narrower than the obligation
    it defends — the shape the subtree rule closed on the plan-time
    refusal, closed here on the fire's own entry.

    Plus, at every barrier after the first, the criteria the fire ITSELF
    finished: the roster it entered with, named by the caller as *held*.
    A fire that crosses off its own work would otherwise read a smaller
    set at its next barrier than the one it was judged against, and its
    own delivery would refuse it.  A criterion finished BEFORE the fire
    entered is in no roster, so it stays outside both readings.
    """

    def __init__(self, *, tracker: TrackerPort) -> None:
        self._tracker = tracker
        self._log: BoundLogger = get_logger(__name__)

    async def read_owed_criteria(self, *, issue_key: str) -> dict[str, str]:
        """The Check every criterion this fire owes carries at head.

        Keyed by the criterion sub-issue's own key: identity and
        provenance are the same value here, and no second identity is
        minted for the tracker-native arm.  The key is carried as the
        tracker reports it — ``CriterionRef`` is the captured spec's
        identity and has exactly one construction site, in the formatter
        the entry composes that spec with.

        Only the Check is carried.  A criterion's recorded Evidence is
        what a previous run claimed, never part of what this one is
        graded against.
        """
        _, current = await self.read_entry(issue_key=issue_key)
        return {criterion.id: criterion.text for criterion in current.criteria}

    async def _read_subtree(self, issue_key: str) -> dict[str, TrackerIssue]:
        """Every criterion sub-issue under *issue_key*, keyed and at head.

        The one subtree reading. The extent every reading of this fire's
        criteria is taken over: what the entry captures is this roster
        entire, what the fire owes is a selection from it by state, and what
        a lane delivers on is all of it. Two definitions of the extent could
        answer two different rosters for one subject, and the barrier that
        compares their selections would refuse a lane nothing is wrong with.
        """
        subtree = await read_scope_members(
            tracker=self._tracker,
            scope=ScopeRef(kind=ScopeKind.ISSUE, key=issue_key),
        )
        return {
            key: issue
            for key, issue in subtree.items()
            if "criterion" in issue.issue_labels
        }

    async def _read_subtree_criteria(
        self, spec: TrackerSpec
    ) -> dict[str, TrackerIssue]:
        """The barrier's reading: the same extent, against the captured spec.

        The entry composes its spec out of one reading of this same extent,
        so there is no interval inside the entry for a criterion to leave in.
        The interval is between the entry and a barrier, and this is where it
        is caught: a criterion the spec names that the subtree no longer holds
        refuses here rather than shrinking the roster quietly.
        """
        criteria = await self._read_subtree(spec.subject)
        for named in spec.criteria:
            if named not in criteria:
                raise InvalidFireCriterionError(
                    issue_key=spec.subject,
                    criterion_key=named,
                    reason="the spec names a criterion the subtree does not hold",
                )
        return criteria

    async def _capture(
        self, issue_key: str
    ) -> tuple[TrackerSpec, dict[str, TrackerIssue]]:
        """Admit the subject, read its subtree once, compose the spec from it.

        The subject is captured through the port, which answers its text and
        its version from the one hydration that admits it; membership is then
        measured over that subject's CANONICAL key, so a fire addressed by an
        alias walks the subtree the tracker reports rather than the one the
        caller asked for.

        Only the two reads sit inside the conversion below. The formatter's
        own refusals — an empty subtree, a criterion with no legible Check —
        and the admission refusals are answers, not outages, and reach the
        caller with their own types.
        """
        try:
            subject = await self._tracker.read_fire_subject(issue_key=issue_key)
            criteria = await self._read_subtree(subject.issue_key)
        except _TRANSPORT_FAILURES as exc:
            raise FireSpecEntryError(
                issue_key=issue_key,
                reason="the tracker subject spec could not be read",
            ) from exc
        except TrackerProtocolError as exc:
            # A response the backend's own shape cannot be read out of is an
            # incomplete read, not an answer about what the subtree holds:
            # the same refusal the port's criterion reads raise for it.
            raise CriterionReadError(
                issue_key=issue_key, reason="the tracker read failed or was incomplete"
            ) from exc
        spec = tracker_spec_from_issues(
            subject=subject, criteria=[criteria[key] for key in sorted(criteria)]
        )
        return spec, criteria

    async def read_entry(
        self, *, issue_key: str, delivering: bool = False
    ) -> tuple[TrackerSpec, TrackerCriterionSet]:
        """Enter a fire: the captured spec and the roster it starts on, once.

        Both values come out of the same reading. The emptiness that refuses
        a fire before its loop and the roster the loop is dispatched with are
        then the same question asked once, and a subject whose criteria all
        sit on its deliverables cannot be admitted by one and refused by the
        other.
        """
        spec, criteria = await self._capture(issue_key)
        if delivering:
            return spec, self._finished(spec, criteria)
        return spec, await self._owed(spec, criteria, frozenset())

    async def _owed(
        self,
        spec: TrackerSpec,
        criteria: Mapping[str, TrackerIssue],
        held: frozenset[str],
    ) -> TrackerCriterionSet:
        """The obligations a reading already taken leaves this fire holding.

        A subtree with nothing unstarted is refused rather than answered
        with an empty roster: there would be no obligation for the work to
        be the discharge of.
        """
        issue_key = spec.subject
        owed = {
            key: criterion_check(criterion=issue, issue_key=issue_key)
            for key, issue in sorted(criteria.items())
            if issue.state_kind is OWED_CRITERION_STATE
            or (issue.state_kind is HELD_CRITERION_STATE and key in held)
        }
        await self._log.ainfo(
            "fire_criteria_read",
            subject=issue_key,
            read_at_version=spec.read_at_version,
            named=len(spec.criteria),
            held=sorted(held),
            owed=sorted(owed),
        )
        if not owed:
            raise FireSpecEntryError(
                issue_key=issue_key,
                reason="the subtree has no Todo criteria to execute",
            )
        return _criterion_set(owed)

    async def read_current(
        self, *, spec: TrackerSpec, held: TrackerCriterionSet | None = None
    ) -> TrackerCriterionSet:
        """Refresh obligations without replacing the captured subject text.

        *held* is the roster the caller entered with; its finished criteria
        stay in this reading, and the comparison a barrier then makes is by
        identity and Check text rather than by state.
        """
        keys = (
            frozenset()
            if held is None
            else frozenset(criterion.id for criterion in held.criteria)
        )
        try:
            criteria = await self._read_subtree_criteria(spec)
        except _TRANSPORT_FAILURES as exc:
            raise FireSpecEntryError(
                issue_key=spec.subject,
                reason="current tracker criteria could not be read",
            ) from exc
        return await self._owed(spec, criteria, keys)

    def _finished(
        self, spec: TrackerSpec, criteria: Mapping[str, TrackerIssue]
    ) -> TrackerCriterionSet:
        """The counting roster a reading already taken leaves a lane standing on.

        A lane that owes nothing has no unstarted criterion for the owed
        reading to answer with, so the roster it stands on is read off the
        subtree rather than selected by this lane's own work, and the fact
        that makes it deliverable is that every criterion that counts is
        finished — which is what this lane's own cross-offs recorded.

        Which criteria count is decided by state alone, the three closed
        kinds apart (KOD-794): a criterion the board Canceled, or closed as
        a Duplicate of another, is no obligation of anybody's, so it neither
        joins the roster nor refuses the lane. Every OPEN kind does refuse,
        naming the criteria it holds for: a criterion reopened between a
        lane being chosen and its entry is exactly that refusal, and it
        lands before any session opens.

        A counting roster that comes out empty is refused — a subtree
        holding no criterion at all, and equally one whose criteria were
        every one of them abandoned — because there is no obligation for a
        delivery to be the discharge of. The readiness read parts from this
        reading on purpose: there a member whose every criterion was
        abandoned owes nothing and reads closed, while here it has nothing
        to deliver and is refused.
        """
        unfinished = sorted(
            key for key, issue in criteria.items() if is_open(issue.state_kind)
        )
        if unfinished:
            raise FireSpecEntryError(
                issue_key=spec.subject,
                # Named as the state this reading holds them to, never as the
                # board's word for it: the label a team spells that state with
                # is configuration, and a message spelling it here would be a
                # second place it lives.
                reason=(
                    "a criterion of the subtree is not finished: "
                    f"{', '.join(unfinished)}"
                ),
            )
        roster = {
            key: criterion_check(criterion=issue, issue_key=spec.subject)
            for key, issue in sorted(criteria.items())
            if issue.state_kind is HELD_CRITERION_STATE
        }
        if not roster:
            raise FireSpecEntryError(
                issue_key=spec.subject,
                reason="the subtree has no criteria to deliver",
            )
        return _criterion_set(roster)


async def current_native_criteria(
    *,
    spec: TrackerSpec,
    reader: FireCriteriaReader | None,
    held: TrackerCriterionSet | None,
) -> TrackerCriterionSet:
    """Require live authority at the consuming node, including on replay.

    *held* has no default here on purpose: a barrier that left it out would
    read the entry-shaped Todo-only set and refuse a fire whose own work is
    the reason a criterion left Todo, so every call site states its roster.
    """
    if reader is None:
        raise FireSpecEntryError(
            issue_key=spec.subject,
            reason="the current tracker criteria reader is not configured",
        )
    return await reader.read_current(spec=spec, held=held)


async def revalidate_criteria(
    state: WorkflowState,
    config: RunnableConfig,
    *,
    source: FireCriteriaSource,
) -> dict[str, object]:
    """Capture the subject once and carry current Checks into shared state."""
    _ = config
    issue_key = state["issue_key"]
    if issue_key is None:
        raise ValueError("A tracker-native fire carries its subject as issue_key")
    spec = state["fire_spec"]
    entry_set: TrackerCriterionSet | None = None
    if spec is None:
        # A fresh entry: one reading answers both the spec this fire is
        # graded against and the roster its loop starts on. A lane entered
        # to deliver owes nothing, so the owed reading would refuse it for
        # having no unstarted criterion; its roster is its whole finished
        # subtree, out of that same reading.
        spec, entry_set = await source.read_entry(
            issue_key=issue_key,
            delivering=isinstance(state["lane_entry"], DeliverOnlyLane),
        )
    if not isinstance(spec, TrackerSpec) or spec.subject != issue_key:
        raise ValueError("The native fire spec must match its addressed subject")
    require_unamended_subject(issue_key=issue_key, entry=state["lane_entry"], spec=spec)
    if entry_set is not None:
        return {"fire_spec": spec, "criterion_set": entry_set}
    # A later pass — a remediation round the review sent back — carries the
    # spec and the roster this run was already judged against, and
    # revalidates that roster like any other barrier does.
    criterion_set = await source.read_current(
        spec=spec, held=recorded_native_roster(state["criterion_set"])
    )
    return {"fire_spec": spec, "criterion_set": criterion_set}


async def require_current_native_snapshot(
    state: WorkflowState, *, reader: FireCriteriaReader | None
) -> None:
    """Permit a recorded judgment's effect only while its obligations hold."""
    spec = state["fire_spec"]
    if not isinstance(spec, TrackerSpec):
        return
    recorded = recorded_native_roster(state["criterion_set"])
    current = await current_native_criteria(spec=spec, reader=reader, held=recorded)
    if recorded is None or current != recorded:
        raise FireSpecEntryError(
            issue_key=spec.subject,
            reason="current tracker criteria differ from the evaluated snapshot",
        )
