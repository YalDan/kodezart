"""KOD-88-AC-4 and AC-7 — the interchange convention, over the new set.

XML in, JSON out: every artifact a template injects arrives inside a named
tag, and every template that injects one states the boundary rule exactly
once.  The rule is injection hygiene rather than politeness — a ticket body
is attacker-controlled text, and the sentence is what tells the session
which half of the prompt is data.

Asserted on RENDERED output, not on the template body: a tag that survives
authoring but not rendering protects nothing.
"""

import pytest

from kodezart.core.prompt_rendering import free_binding_names
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.style_detectors import (
    artifact_tag_names,
    data_boundary_sentences,
    unbalanced_artifact_tags,
)
from tests.prompts.test_claude_opus_goldens import ALL_CASES
from tests.prompts.test_v5_goldens import render_case, v5_registry

#: Which named tag each injected artifact must arrive inside. Keyed by the
#: shared fixture case, so the expectation is stated per rendering rather
#: than per key — the regeneration round injects one the first round does
#: not, and that difference is the point of listing them separately.
ARTIFACT_TAGS: dict[str, tuple[str, ...]] = {
    "acceptance_criteria": ("ticket",),
    "acceptance_criteria__regeneration_round": ("validation_findings", "ticket"),
    "branch_name": ("task",),
    "commit_message": (),
    "content_audit": ("content",),
    "criteria_validation": ("ticket", "acceptance_criteria"),
    "evaluation": ("acceptance_criteria", "changeset"),
    "evaluation__empty_changeset": ("acceptance_criteria", "changeset"),
    "evaluation__no_file_paths": ("acceptance_criteria", "changeset"),
    "fire_prep_pass": (),
    "fix": ("ticket", "review_feedback", "ci_summary"),
    "fix__no_optional_sections": ("ticket",),
    "grooming_pass": (),
    "implementation": ("ticket",),
    "iteration_feedback": ("failed_criteria",),
    "knowledge_map": (),
    "post_merge_review": ("acceptance_criteria", "changeset"),
    "pr_description": ("ticket", "acceptance_criteria"),
    "remediation_ticket": ("ticket", "done_work", "failure_evidence"),
    "ticket_create": ("task",),
    "ticket_review": ("task", "ticket"),
    "ticket_revision": ("task", "ticket", "review_feedback"),
    "ticket_revision__no_suggestions": ("task", "ticket", "review_feedback"),
}

EMPTY_CHANGESET_CLAUSE = (
    "No commits between the base and head refs; "
    "the previous verdict's failures persist unchanged."
)

#: The informational bound of AC-7, against a legacy evaluator of ~1,540.
EVALUATION_WORD_BOUND = 400


def test_the_tag_expectation_covers_every_case() -> None:
    """Non-vacuity: no case escapes the convention by being unlisted."""
    assert set(ARTIFACT_TAGS) == set(ALL_CASES)


@pytest.mark.parametrize("golden_name", sorted(ARTIFACT_TAGS))
def test_every_injected_artifact_arrives_inside_its_declared_tag(
    golden_name: str,
) -> None:
    """The tags the rendered prompt opens are exactly the declared ones."""
    rendered = render_case(golden_name)
    assert artifact_tag_names(rendered) == ARTIFACT_TAGS[golden_name]
    assert unbalanced_artifact_tags(rendered) == ()


@pytest.mark.parametrize("golden_name", sorted(ARTIFACT_TAGS))
def test_exactly_one_boundary_sentence_per_artifact_carrying_render(
    golden_name: str,
) -> None:
    """One sentence where there are artifacts, none where there are not."""
    rendered = render_case(golden_name)
    expected = 1 if ARTIFACT_TAGS[golden_name] else 0
    assert len(data_boundary_sentences(rendered)) == expected


@pytest.mark.parametrize(
    "golden_name",
    ["acceptance_criteria", "ticket_review", "fix", "pr_description"],
)
def test_the_injected_artifact_is_inside_the_tag_and_not_beside_it(
    golden_name: str,
) -> None:
    """A tag before the artifact and a tag after it are not the same thing."""
    rendered = render_case(golden_name)
    opening = rendered.index("<ticket>")
    closing = rendered.index("</ticket>")
    assert "Golden ticket" in rendered[opening:closing]


def test_the_empty_changeset_escape_clause_renders() -> None:
    """Behaviour preserved: an empty digest says so rather than saying nothing."""
    empty = render_case("evaluation__empty_changeset")
    assert EMPTY_CHANGESET_CLAUSE in empty

    populated = render_case("evaluation")
    assert EMPTY_CHANGESET_CLAUSE not in populated
    assert "Commits: 2" in populated


def test_the_orchestration_slot_is_declared_and_unfilled() -> None:
    """Declared, so a later deliverable binds it; unfilled, so it renders nothing."""
    slotted = (PromptKey.ACCEPTANCE_CRITERIA, PromptKey.TICKET_CREATE)
    registry = v5_registry()
    for key in slotted:
        assert "orchestration_block" in free_binding_names(
            registry.template_for(key).body
        )
    assert "orchestration_block" not in render_case("acceptance_criteria")
    assert "orchestration_block" not in render_case("ticket_create")


def test_the_rendered_evaluator_is_under_the_size_bound() -> None:
    """AC-7, informational: an upper bound, never an equality."""
    assert len(render_case("evaluation").split()) < EVALUATION_WORD_BOUND
