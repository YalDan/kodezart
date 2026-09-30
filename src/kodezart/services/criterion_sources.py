"""Read a criterion's own full source from an unambiguous native child family."""

from dataclasses import dataclass

from kodezart.core.protocols import TrackerCriteriaReader
from kodezart.domain.errors import CriterionResolutionError
from kodezart.types.domain.tracker import TrackerIssue


@dataclass(frozen=True, slots=True)
class NativeCriterionResolver:
    """The one site that turns a criterion identity into its sub-issue.

    Membership, identity and full source are one observation against a fresh,
    complete family read. Callers apply their own state policy; no address,
    body anchor or criterion set is persisted here, and nothing is cached
    between calls — a native edit between two resolutions is visible.
    """

    tracker: TrackerCriteriaReader

    async def resolve_criterion(
        self, *, issue_key: str, criterion_key: str
    ) -> TrackerIssue:
        """Resolve one native key against a fresh, complete criterion-family read."""

        def refuse(reason: str) -> CriterionResolutionError:
            return CriterionResolutionError(
                issue_key=issue_key, criterion_key=criterion_key, reason=reason
            )

        try:
            rows = tuple(await self.tracker.read_criteria(issue_key=issue_key))
        except Exception as exc:
            raise refuse(f"the current criterion family is unreadable: {exc}") from exc
        selected = [row for row in rows if row.issue_key == criterion_key]
        if len(selected) != 1:
            raise refuse(f"the key resolves to {len(selected)} current sub-issues")
        keys = [row.issue_key for row in rows]
        if len(set(keys)) != len(keys) or any(
            row.parent_key != issue_key or "criterion" not in row.issue_labels
            for row in rows
        ):
            raise refuse("the current criterion family has ambiguous membership")
        return selected[0]
