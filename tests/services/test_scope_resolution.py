"""The shared resolver preserves the tracker's scope and structural edges."""

from collections.abc import Sequence

import pytest

from kodezart.core.errors import TrackerProtocolError
from kodezart.services.scope_resolution import resolve_scope
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import IssueRelationKind, TrackerIssue
from tests.fakes import FakeTrackerPort, make_tracker_issue


class ScopeFixtureTracker(FakeTrackerPort):
    """An explicitly resolved port answer, independent of adapter arithmetic."""

    def __init__(self, *, ref: ScopeRef, issues: Sequence[TrackerIssue]) -> None:
        super().__init__(issues=issues)
        self.ref = ref
        self.resolved_issues = list(issues)

    async def scope_issues(self, *, ref: ScopeRef) -> Sequence[TrackerIssue]:
        assert ref == self.ref
        return self.resolved_issues


@pytest.mark.parametrize("kind", tuple(ScopeKind))
async def test_each_entry_kind_preserves_membership_and_structural_edges(
    kind: ScopeKind,
) -> None:
    ref = ScopeRef(kind=kind, key="ENG-1" if kind is ScopeKind.ISSUE else "scope")
    root = make_tracker_issue("ENG-1")
    child = make_tracker_issue(
        "ENG-2",
        parent_key="ENG-1",
        blocked_by=("ENG-3",),
        body="Parent: ENG-98; blockedBy: ENG-99; branch: feature/ENG-100",
    )
    tracker = ScopeFixtureTracker(ref=ref, issues=(root, child))

    resolved = await resolve_scope(ref=ref, tracker=tracker)

    assert resolved.ref == ref
    assert resolved.issues == (root, child)
    assert resolved.issues[1].parent_key == "ENG-1"
    assert tuple(
        (edge.kind, edge.issue_key) for edge in resolved.issues[1].relations
    ) == ((IssueRelationKind.BLOCKED_BY, "ENG-3"),)

    tracker.resolved_issues.clear()
    assert resolved.issues == (root, child)


async def test_scope_read_failure_does_not_turn_into_an_empty_scope() -> None:
    error = TrackerProtocolError(
        "scope read failed",
        tool="fixture-scope-read",
        detail="requested membership is unavailable",
    )

    class FailingTracker(FakeTrackerPort):
        async def scope_issues(self, *, ref: ScopeRef) -> Sequence[TrackerIssue]:
            raise error

    with pytest.raises(TrackerProtocolError) as caught:
        await resolve_scope(
            ref=ScopeRef(kind=ScopeKind.PROJECT, key="scope"),
            tracker=FailingTracker(),
        )

    assert caught.value is error
