"""Capture native subjects and read current obligations at fire barriers."""

from collections.abc import Sequence

from langchain_core.runnables import RunnableConfig

from kodezart.core.errors import TrackerAccessDeniedError, TrackerUnavailableError
from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import FireCriteriaReader, FireCriteriaSource, TrackerPort
from kodezart.domain.criterion_cross_off import HELD_CRITERION_STATE
from kodezart.domain.errors import (
    FireSpecEntryError,
    InvalidFireCriterionError,
    TransientAPIError,
)
from kodezart.domain.fire_spec import criterion_check
from kodezart.domain.workflow_state import recorded_native_roster
from kodezart.services.scope_membership import read_scope_members
from kodezart.types.domain.criteria import (
    CriterionId,
    ExecutionCriterion,
    TrackerCriterion,
    TrackerCriterionSet,
)
from kodezart.types.domain.fire_spec import TrackerSpec
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind
from kodezart.types.domain.workflow import WorkflowState

#: The state a criterion the fire still owes sits in at head.
#:
#: The tracker owns the vocabulary and ``Todo`` is its unstarted kind. A
#: criterion in any other kind is finished, abandoned, or already being
#: worked somewhere else, and re-validating it here would put this fire on
#: the hook for an obligation the board does not hold it to.
OWED_CRITERION_STATE = WorkflowStateKind.UNSTARTED


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

    The spec read IS the source: ``read_fire_spec`` establishes the
    subject's admission and lists its criterion sub-issues, and nothing a
    caller carries alongside it can stand in for that read.

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
        identity and has exactly one construction site, at the spec read.

        Only the Check is carried.  A criterion's recorded Evidence is
        what a previous run claimed, never part of what this one is
        graded against.
        """
        spec = await self.read_spec(issue_key=issue_key)
        current = await self.read_current(spec=spec)
        return {criterion.id: criterion.text for criterion in current.criteria}

    async def _read_owed_criteria(
        self, spec: TrackerSpec, held: frozenset[str]
    ) -> dict[str, str]:
        """Refresh current criterion Checks without recapturing the subject."""
        issue_key = spec.subject
        subtree = await read_scope_members(
            tracker=self._tracker,
            scope=ScopeRef(kind=ScopeKind.ISSUE, key=issue_key),
        )
        criteria: dict[str, TrackerIssue] = {
            key: issue
            for key, issue in subtree.items()
            if "criterion" in issue.issue_labels
        }
        for named in spec.criteria:
            if named not in criteria:
                raise InvalidFireCriterionError(
                    issue_key=issue_key,
                    criterion_key=named,
                    reason="the spec names a criterion the subtree does not hold",
                )
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
        return owed

    async def read_spec(self, *, issue_key: str) -> TrackerSpec:
        """Capture the admitted subject; an outage is never cached authority."""
        try:
            return await self._tracker.read_fire_spec(issue_key=issue_key)
        except (
            ConnectionError,
            TimeoutError,
            TransientAPIError,
            TrackerUnavailableError,
            TrackerAccessDeniedError,
        ) as exc:
            raise FireSpecEntryError(
                issue_key=issue_key,
                reason="the tracker subject spec could not be read",
            ) from exc

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
            owed = await self._read_owed_criteria(spec, keys)
        except (
            ConnectionError,
            TimeoutError,
            TransientAPIError,
            TrackerUnavailableError,
            TrackerAccessDeniedError,
        ) as exc:
            raise FireSpecEntryError(
                issue_key=spec.subject,
                reason="current tracker criteria could not be read",
            ) from exc
        if not owed:
            raise FireSpecEntryError(
                issue_key=spec.subject,
                reason="the subtree has no Todo criteria to execute",
            )
        return TrackerCriterionSet(
            criteria=[
                TrackerCriterion(id=CriterionId(key), text=check)
                for key, check in owed.items()
            ],
        )


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
    if spec is None:
        spec = await source.read_spec(issue_key=issue_key)
    if not isinstance(spec, TrackerSpec) or spec.subject != issue_key:
        raise ValueError("The native fire spec must match its addressed subject")
    return {
        "fire_spec": spec,
        "criterion_set": await source.read_current(
            spec=spec,
            # A first entry carries no roster and reads the Todo set; a
            # remediation re-entry or a replayed checkpoint carries the one
            # this run was already judged against.
            held=recorded_native_roster(state["criterion_set"]),
        ),
    }


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
