"""A tick is barren by identity over the fire's subtree, never by gap size.

Every fixture is a real pair of subtree readings taken through the same
``SubtreeClosure`` the walker reads a fire's gap with, so the four claims
are asserted against rows a board could hold rather than against key sets
typed to suit the predicate.  Three of the four are negatives, and each
one is a shape whose CARDINALITY says the opposite of its identities: the
gap held still, the gap grew, the closure landed under a child.
"""

import pytest

from kodezart.domain.fire_plateau import (
    closed_previous_work,
    fire_plateaued,
    observe_tick,
)
from kodezart.domain.issue_tree import SubtreeClosure
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind
from tests.fakes import make_tracker_issue

REF = ScopeRef(kind=ScopeKind.ISSUE, key="fire")
CRITERION = frozenset({"criterion"})


def criterion(key: str, *, parent: str = "fire", closed: bool = False) -> TrackerIssue:
    return make_tracker_issue(
        key,
        parent_key=parent,
        issue_labels=CRITERION,
        state_kind=(
            WorkflowStateKind.COMPLETED if closed else WorkflowStateKind.UNSTARTED
        ),
        state_name="Done" if closed else "Todo",
    )


def fire() -> TrackerIssue:
    return make_tracker_issue("fire")


def child() -> TrackerIssue:
    return make_tracker_issue("child", parent_key="fire")


def reading(
    rows: tuple[TrackerIssue, ...],
) -> tuple[tuple[TrackerIssue, ...], tuple[TrackerIssue, ...]]:
    """One board reading: the subtree's criteria, and the part still open."""
    closure = SubtreeClosure(facts={row.issue_key: row for row in rows}, ref=REF)
    criteria = tuple(row for row in rows if "criterion" in row.issue_labels)
    return criteria, closure.gap("fire")


def tick(before: tuple[TrackerIssue, ...], after: tuple[TrackerIssue, ...]):
    """The tick between two board readings, as the predicate sees it."""
    _, was_open = reading(before)
    criteria, still_open = reading(after)
    return observe_tick(
        previously_open=tuple(row.issue_key for row in was_open),
        subtree_criteria=criteria,
        open_criteria=still_open,
    )


def nothing_closed() -> tuple[tuple[TrackerIssue, ...], tuple[TrackerIssue, ...]]:
    """A tick that moved the branch and closed none of what was owed."""
    owed = (
        criterion("fire-AC-1"),
        criterion("fire-AC-2"),
        criterion("child-AC-1", parent="child"),
    )
    return (fire(), child(), *owed), (fire(), child(), *owed)


def flat_cardinality_churn() -> tuple[
    tuple[TrackerIssue, ...], tuple[TrackerIssue, ...]
]:
    """Twelve criteria closed while twelve more are surfaced: the gap holds."""
    before = tuple(criterion(f"fire-AC-{index}") for index in range(1, 13))
    after = tuple(
        criterion(f"fire-AC-{index}", closed=True) for index in range(1, 13)
    ) + tuple(criterion(f"fire-AC-{index}") for index in range(13, 25))
    return (fire(), *before), (fire(), *after)


def gap_grown_through_a_lapse() -> tuple[
    tuple[TrackerIssue, ...], tuple[TrackerIssue, ...]
]:
    """One criterion closed while two Done ones lapse back: the gap grows."""
    before = (
        criterion("fire-AC-1"),
        criterion("fire-AC-2", closed=True),
        criterion("fire-AC-3", closed=True),
    )
    after = (
        criterion("fire-AC-1", closed=True),
        criterion("fire-AC-2"),
        criterion("fire-AC-3"),
    )
    return (fire(), *before), (fire(), *after)


def descendant_only_closure() -> tuple[
    tuple[TrackerIssue, ...], tuple[TrackerIssue, ...]
]:
    """The only closure is a criterion under the deliverable child."""
    own = (criterion("fire-AC-1"), criterion("fire-AC-2"))
    before = (fire(), child(), *own, criterion("child-AC-1", parent="child"))
    after = (
        fire(),
        child(),
        *own,
        criterion("child-AC-1", parent="child", closed=True),
    )
    return before, after


def test_a_tick_closing_nothing_the_subtree_owed_plateaus_at_a_bound_of_one():
    before, after = nothing_closed()
    barren = tick(before, after)
    assert closed_previous_work(barren) == frozenset()
    assert fire_plateaued(ticks=(barren,), plateau_bound=1) is True


@pytest.mark.parametrize(
    "fixture,closed,gap_before,gap_after",
    [
        (flat_cardinality_churn, 12, 12, 12),
        (gap_grown_through_a_lapse, 1, 1, 2),
        (descendant_only_closure, 1, 3, 2),
    ],
)
def test_closing_previous_work_is_progress_whatever_the_gap_size_did(
    fixture, closed, gap_before, gap_after
):
    before, after = fixture()
    assert len(reading(before)[1]) == gap_before
    assert len(reading(after)[1]) == gap_after
    moved = tick(before, after)
    assert len(closed_previous_work(moved)) == closed
    assert fire_plateaued(ticks=(moved,), plateau_bound=1) is False


def test_a_criterion_that_left_the_subtree_is_not_a_closure():
    owed = (criterion("fire-AC-1"), criterion("fire-AC-2"))
    moved_away = tick((fire(), *owed), (fire(), owed[0]))
    assert closed_previous_work(moved_away) == frozenset()
    assert fire_plateaued(ticks=(moved_away,), plateau_bound=1) is True


def test_a_criterion_created_already_closed_is_not_previous_work():
    owed = (criterion("fire-AC-1"),)
    surfaced = tick(
        (fire(), *owed), (fire(), *owed, criterion("fire-AC-2", closed=True))
    )
    assert surfaced.currently_closed == frozenset({"fire-AC-2"})
    assert closed_previous_work(surfaced) == frozenset()
    assert fire_plateaued(ticks=(surfaced,), plateau_bound=1) is True


def test_the_bound_counts_ticks_and_one_closure_clears_the_whole_window():
    barren = tick(*nothing_closed())
    moved = tick(*gap_grown_through_a_lapse())
    assert fire_plateaued(ticks=(), plateau_bound=1) is False
    assert fire_plateaued(ticks=(barren,), plateau_bound=2) is False
    assert fire_plateaued(ticks=(barren, barren), plateau_bound=2) is True
    assert fire_plateaued(ticks=(barren, moved), plateau_bound=2) is False
    assert fire_plateaued(ticks=(moved, barren), plateau_bound=1) is True


def test_an_unreadable_tick_or_bound_refuses_instead_of_answering():
    repeated = criterion("fire-AC-1")
    with pytest.raises(ValueError, match="repeats in the subtree reading"):
        observe_tick(
            previously_open=(),
            subtree_criteria=(repeated, repeated),
            open_criteria=(),
        )
    with pytest.raises(ValueError, match="repeats in the previous reading"):
        observe_tick(
            previously_open=("fire-AC-1", "fire-AC-1"),
            subtree_criteria=(repeated,),
            open_criteria=(repeated,),
        )
    with pytest.raises(ValueError, match="nonempty"):
        observe_tick(previously_open=(" ",), subtree_criteria=(), open_criteria=())
    with pytest.raises(ValueError, match="non-criterion"):
        observe_tick(previously_open=(), subtree_criteria=(fire(),), open_criteria=())
    with pytest.raises(ValueError, match="absent from the subtree"):
        observe_tick(previously_open=(), subtree_criteria=(), open_criteria=(repeated,))
    with pytest.raises(ValueError, match="at least one tick"):
        fire_plateaued(ticks=(), plateau_bound=0)
