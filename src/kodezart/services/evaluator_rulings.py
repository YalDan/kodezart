"""The evaluator's rulings on a scope run, written to the board by the run itself."""

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import EvaluatorRulingTracker, ScopeStatusWriter
from kodezart.domain.derived_writes import derived_writes
from kodezart.domain.evaluator_rulings import rulings_to_record
from kodezart.types.domain.agent import AcceptanceCriteriaOutput
from kodezart.types.domain.operation import LifecycleStage
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_terminal import STATUS_UPDATE_SCOPE_KINDS
from kodezart.types.domain.tracker import WorkflowStateKind, is_non_counting

#: The kinds a criterion stands in before anyone has claimed it.
_UNCLAIMED_KINDS = frozenset(
    {
        WorkflowStateKind.TRIAGE,
        WorkflowStateKind.BACKLOG,
        WorkflowStateKind.UNSTARTED,
    }
)


def _claimed(kind: WorkflowStateKind) -> bool:
    """Whether a criterion in *kind* stands where the implementer's claim put it.

    A selection, not a reading of finishedness: the criterion still counts
    and has left the kinds it stands in before a claim. Moving one already
    in progress is a read and no write.
    """
    return kind not in _UNCLAIMED_KINDS and not is_non_counting(kind)


class EvaluatorRulingWriter:
    """Puts one evaluation of a scope run on the board, with no session.

    It holds no run state: the scope, the iteration and both evaluations
    arrive per call, so one object serves every run a deployment compiles.
    """

    def __init__(
        self,
        *,
        tracker: EvaluatorRulingTracker,
        status: ScopeStatusWriter,
    ) -> None:
        self._tracker = tracker
        self._status = status
        self._log: BoundLogger = get_logger(__name__)

    @derived_writes("post_comment", "set_workflow_state", "post_status_update")
    async def record(
        self,
        *,
        scope: ScopeRef,
        iteration: int,
        previous: AcceptanceCriteriaOutput | None,
        current: AcceptanceCriteriaOutput,
    ) -> None:
        """Comment each changed ruling and flag, move failed claims back, sum up.

        A failed write is logged and passed over: the verdict already stands
        in the loop's state, and the next evaluation's comparison posts again
        whatever changed. Nothing here raises for the board.

        Derived: every body is the evaluation's own structured output, the
        move is the one its FAIL settles, and the summary is its arithmetic.
        """
        comments, unplaced = rulings_to_record(previous, current, iteration=iteration)
        posted = 0
        for key, body in comments:
            try:
                await self._tracker.post_comment(issue_key=key, body=body)
                posted += 1
            except Exception:
                await self._log.aexception(
                    "ruling_record_failed", scope=scope.key, criterion=key
                )
        moved = 0
        failed_under: dict[str, list[str]] = {}
        for result in current.criteria_results:
            if result.passed:
                continue
            key = str(result.criterion_id)
            parent = "an unread parent"
            try:
                issue = await self._tracker.read_issue(issue_key=key)
                parent = issue.parent_key or "no parent"
                if _claimed(issue.state_kind):
                    placed = await self._tracker.set_workflow_state(
                        issue_key=key, stage=LifecycleStage.IN_PROGRESS
                    )
                    if placed.state_name != issue.state_name:
                        moved += 1
            except Exception:
                await self._log.aexception(
                    "ruling_record_failed", scope=scope.key, criterion=key
                )
            failed_under.setdefault(parent, []).append(key)
        total = len(current.criteria_results)
        failed = sum(len(keys) for keys in failed_under.values())
        lines = [
            f"Iteration {iteration}: {total - failed}/{total} criteria passed, "
            f"{failed} failed, {len(current.sherlock_flags)} flags",
            "",
            *(
                f"- Failed under {parent}: {', '.join(keys)}"
                for parent, keys in sorted(failed_under.items())
            ),
            *(f"- Flag: {concern}" for concern in unplaced),
        ]
        summary = "\n".join(lines).strip()
        summary_posted = False
        try:
            if scope.kind in STATUS_UPDATE_SCOPE_KINDS:
                await self._status.post_status_update(ref=scope, body=summary)
                summary_posted = True
            elif scope.kind is ScopeKind.ISSUE:
                await self._tracker.post_comment(issue_key=scope.key, body=summary)
                summary_posted = True
        except Exception:
            await self._log.aexception(
                "ruling_record_failed", scope=scope.key, criterion=None
            )
        await self._log.ainfo(
            "rulings_recorded",
            scope=scope.key,
            iteration=iteration,
            comments=posted,
            state_moves=moved,
            flags=len(current.sherlock_flags),
            summary_posted=summary_posted,
        )
