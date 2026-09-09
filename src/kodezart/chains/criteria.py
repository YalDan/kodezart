"""The tracker-native criteria stage a fire runs before its loop."""

from langchain_core.runnables import RunnableConfig

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import InvalidFireCriterionError
from kodezart.domain.fire_spec import criterion_check
from kodezart.services.scope_membership import read_scope_members
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


class TrackerCriteria:
    """A fire's criteria, read from the tracker at head, before the loop.

    The spec read IS the source: ``read_fire_spec`` establishes the
    subject's admission and lists its criterion sub-issues, and nothing a
    caller carries alongside it can stand in for that read.

    What the fire OWES is then its subtree's own Todo criterion
    sub-issues.  A deliverable child's criterion sits inside the exit
    condition and outside the subject's direct family, so a barrier
    reading only the direct family would be narrower than the obligation
    it defends — the shape the 2026-09-09 subtree ruling closed on the
    plan-time refusal, closed here on the fire's own entry.
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
        spec = await self._tracker.read_fire_spec(issue_key=issue_key)
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
        }
        await self._log.ainfo(
            "fire_criteria_read",
            subject=issue_key,
            read_at_version=spec.read_at_version,
            named=len(spec.criteria),
            owed=sorted(owed),
        )
        return owed

    async def revalidate_criteria(
        self,
        state: WorkflowState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        """Re-read what this fire owes, at head, before the loop node runs.

        A barrier node: what it establishes is that every criterion the
        fire is about still states a Check the run can be graded against.
        It writes no run state — the loop's own criterion identity is the
        sub-issue key under the 2026-09-08 ruling, and the models that
        carry identity into the loop are not this lane's surface — so a
        criterion that has lost its Check stops the fire HERE rather than
        after a loop has spent its budget on it.
        """
        _ = config
        issue_key = state["issue_key"]
        if issue_key is None:
            msg = "A tracker-native fire carries its subject as issue_key"
            raise ValueError(msg)
        await self.read_owed_criteria(issue_key=issue_key)
        return {}
