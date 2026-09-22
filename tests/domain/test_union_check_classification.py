"""Historical check arithmetic, with the union as its actual consumer."""

import pytest

from kodezart.domain.check_chain import (
    classify_check_failures,
    counted_checks,
    rostered_forge_checks,
)
from kodezart.types.domain.operation import CheckStep


def chain():
    return (
        CheckStep(name="gate", command="gate"),
        CheckStep(name="middle", command="middle", depends_on="gate"),
        CheckStep(name="last", command="last", depends_on="middle"),
    )


def test_gate_and_dependents_are_one_root():
    result = classify_check_failures(chain(), ["last", "gate", "middle"])
    assert result.roots == ("gate",)
    assert result.cascades == ("middle", "last")


def test_two_independent_failures_are_two_roots():
    steps = [
        CheckStep(name="gate", command="gate"),
        CheckStep(name="other", command="other"),
    ]
    result = classify_check_failures(steps, ["other", "gate"])
    assert result.roots == ("gate", "other")
    assert result.cascades == ()


def test_dependent_failing_alone_is_root():
    result = classify_check_failures(chain(), ["last"])
    assert result.roots == ("last",)
    assert result.cascades == ()


def test_transitive_failed_ancestor_makes_cascade():
    result = classify_check_failures(chain(), ["last", "gate"])
    assert result.roots == ("gate",)
    assert result.cascades == ("last",)


def test_input_failure_order_does_not_change_classification():
    assert classify_check_failures(
        chain(), ["last", "gate", "middle"]
    ) == classify_check_failures(chain(), ["gate", "middle", "last"])


def test_unknown_failed_names_remain_sorted_roots():
    result = classify_check_failures(
        chain(), ["unknown-z", "last", "gate", "unknown-a"]
    )
    assert result.roots == ("gate", "unknown-a", "unknown-z")
    assert result.cascades == ("last",)


def rostered_chain():
    """A chain mixing steps that name a forge check and steps that do not."""
    return (
        CheckStep(name="gate", command="gate", forge_check="unit"),
        CheckStep(name="middle", command="middle", depends_on="gate"),
        CheckStep(
            name="last", command="last", depends_on="middle", forge_check="integration"
        ),
    )


def test_a_chain_rosters_only_the_forge_checks_its_steps_name():
    assert rostered_forge_checks(rostered_chain()) == frozenset({"unit", "integration"})
    assert rostered_forge_checks(chain()) == frozenset()


@pytest.mark.parametrize(
    ("reported", "failed", "rostered", "excluded", "failures"),
    [
        pytest.param(
            {"unit", "lint"}, {"lint"}, set(), set(), {"lint"}, id="no-roster"
        ),
        pytest.param({"unit"}, {"unit"}, {"unit"}, set(), {"unit"}, id="roster-equal"),
        pytest.param(
            {"unit", "lint"}, set(), {"unit"}, {"lint"}, set(), id="roster-subset"
        ),
        pytest.param(
            {"unit", "lint"}, {"lint"}, {"unit"}, {"lint"}, set(), id="excluded-failure"
        ),
        pytest.param(
            {"unit"},
            {"unit"},
            {"unit", "absent"},
            set(),
            {"unit"},
            id="roster-unreported",
        ),
    ],
)
def test_the_counted_checks_are_the_reported_ones_a_roster_keeps(
    reported, failed, rostered, excluded, failures
):
    """An empty roster counts everything; a named roster counts its own.

    A rostered name nobody reported leaves the counted set the intersection:
    the arm's own roster clause is what refuses that observation, and this
    arithmetic does not answer it twice.
    """
    counted = counted_checks(
        reported=frozenset(reported),
        failed=frozenset(failed),
        rostered=frozenset(rostered),
    )
    assert counted.excluded == frozenset(excluded)
    assert counted.failures == frozenset(failures)


def test_a_failure_outside_the_reported_roster_is_refused():
    with pytest.raises(ValueError, match="reported roster"):
        counted_checks(
            reported=frozenset({"unit"}),
            failed=frozenset({"lint"}),
            rostered=frozenset(),
        )
