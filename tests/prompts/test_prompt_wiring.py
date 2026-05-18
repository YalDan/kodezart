"""Smoke tests that Sherlock/Watson preludes are wired into prompt functions.

Catches the exact regression where a prelude constant is defined but never
referenced by the public function (dead code).
"""

from kodezart.prompts.acceptance_criteria import build_prompt as criteria_prompt
from kodezart.prompts.evaluation import build_prompt as evaluation_prompt
from kodezart.prompts.iteration_feedback import augment_prompt
from kodezart.types.domain.agent import CriterionResult
from kodezart.types.domain.consolidation import ChangesetDigest

_EMPTY_DIGEST = ChangesetDigest(
    file_paths=[],
    commit_subjects=[],
    commit_count=0,
)


def test_evaluation_prompt_contains_watson_dispatch() -> None:
    """evaluation.build_prompt output includes Watson dispatch instructions."""
    output = evaluation_prompt(criteria=["Tests pass"], changeset=_EMPTY_DIGEST)
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


def test_evaluation_prompt_inlines_changeset_digest() -> None:
    """Commit subjects and file paths from the digest appear verbatim."""
    digest = ChangesetDigest(
        file_paths=["src/foo.py", "tests/test_foo.py"],
        commit_subjects=["feat: add foo", "test: cover foo"],
        commit_count=2,
    )
    rendered = evaluation_prompt(criteria=["Tests pass"], changeset=digest)
    assert "src/foo.py" in rendered
    assert "tests/test_foo.py" in rendered
    assert "feat: add foo" in rendered
    assert "test: cover foo" in rendered
    # Data, not commands.
    assert "git diff" not in rendered
    assert "git log" not in rendered
    assert "--name-only" not in rendered
    assert "--format=%s" not in rendered


def test_evaluation_prompt_handles_empty_changeset() -> None:
    """Empty digest renders the deterministic escape clause."""
    rendered = evaluation_prompt(criteria=["Tests pass"], changeset=_EMPTY_DIGEST)
    assert (
        "No commits between the base and head refs; the previous "
        "verdict's failures persist unchanged."
    ) in rendered


def test_evaluation_prompt_rejects_positional_args() -> None:
    """build_prompt is keyword-only — its signature has no positional params."""
    import inspect

    sig = inspect.signature(evaluation_prompt)
    for param in sig.parameters.values():
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"Parameter {param.name!r} is {param.kind.name}, "
            "expected KEYWORD_ONLY"
        )
    assert set(sig.parameters) == {"criteria", "changeset"}
