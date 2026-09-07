"""Historical check arithmetic, with the union as its actual consumer."""

from kodezart.domain.check_chain import classify_check_failures
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
