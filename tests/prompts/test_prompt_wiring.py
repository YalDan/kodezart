"""Smoke tests that Sherlock/Watson preludes are wired into prompt functions.

Catches the exact regression where a prelude constant is defined but never
referenced by the public function (dead code).
"""

from kodezart.prompts.acceptance_criteria import build_prompt as criteria_prompt
from kodezart.prompts.evaluation import build_prompt as evaluation_prompt
from kodezart.prompts.iteration_feedback import augment_prompt
from kodezart.types.domain.agent import CriterionResult


def test_evaluation_prompt_contains_watson_dispatch() -> None:
    """evaluation.build_prompt output includes Watson dispatch instructions."""
    output = evaluation_prompt(["Tests pass"])
    assert "WATSON 1" in output
    assert "graceful degradation" in output
    assert "Tests pass" in output


def test_acceptance_criteria_prompt_contains_watson_dispatch() -> None:
    """acceptance_criteria.build_prompt output includes Watson dispatch."""
    output = criteria_prompt("Implement feature X")
    assert "WATSON 1" in output
    assert "graceful degradation" in output
    assert "Implement feature X" in output


def test_iteration_feedback_contains_watson_dispatch() -> None:
    """iteration_feedback.augment_prompt output includes Watson dispatch."""
    failure = CriterionResult(
        criterion="Tests pass",
        passed=False,
        reasoning="Tests fail.",
    )
    output = augment_prompt("base task prompt", [failure])
    assert "WATSON 1" in output
    assert "graceful degradation" in output
    assert "Tests pass" in output
    assert "base task prompt" in output
