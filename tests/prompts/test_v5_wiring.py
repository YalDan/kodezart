"""KOD-88-AC-4 and AC-7 — the interchange convention, over the new set.

XML in, JSON out: every artifact a template injects arrives inside a named
tag, and every template that injects one states the boundary rule exactly
once.  The rule is injection hygiene rather than politeness — a ticket body
is attacker-controlled text, and the sentence is what tells the session
which half of the prompt is data.

Asserted on RENDERED output, not on the template body: a tag that survives
authoring but not rendering protects nothing.
"""

import tomllib
from datetime import timedelta

import pytest

from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.core.prompt_rendering import free_binding_names
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_records import RunIdentity, RunOutcome, RunRecord
from kodezart.types.domain.ticket_review import TicketReviewMode
from tests.fakes import fixture_run_identity, pass_render_variables
from tests.prompts.style_detectors import (
    artifact_tag_names,
    data_boundary_sentences,
    unbalanced_artifact_tags,
)
from tests.prompts.test_claude_opus_goldens import ALL_CASES, EXAMPLE_OPERATION, V5_SET
from tests.prompts.test_prompt_wiring import load_registry
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


# ---------------------------------------------------------------------------
# KOD-90-AC-6 — the create-only critique: composed by the MODE, into one member
# ---------------------------------------------------------------------------

CRITIQUE_FRAGMENT_NAME = "ticket_create_critique"


def declared_critique() -> str:
    """The fragment as the SET declares it — read here, never restated."""
    raw = (default_sets_root() / V5_SET / "set.toml").read_text(encoding="utf-8")
    fragments = tomllib.loads(raw)["fragments"]
    assert isinstance(fragments, dict)
    return str(fragments[CRITIQUE_FRAGMENT_NAME])


def registry_under(mode: TicketReviewMode) -> InRepoPromptRegistry:
    """The new set resolved under *mode*, with the goldens' own bindings."""
    return load_registry(
        default_set=V5_SET,
        bindings=dict(operation_bindings(load_operation_config(EXAMPLE_OPERATION))),
        ticket_review_mode=mode,
    )


def render_under(golden_name: str, registry: InRepoPromptRegistry) -> str:
    """One shared fixture case, rendered against an already-resolved set."""
    key, variables = ALL_CASES[golden_name]
    return registry.template_for(key).render({**variables, "skills_reference": ""})


def test_the_critique_is_composed_under_create_only_and_only_then() -> None:
    """Present under one mode, absent under the other, and nothing else moves."""
    critique = declared_critique()
    create_only = render_under(
        "ticket_create", registry_under(TicketReviewMode.CREATE_ONLY)
    )
    reviewed = render_under("ticket_create", registry_under(TicketReviewMode.REVIEWED))

    assert critique in create_only
    assert critique not in reviewed
    # The reviewed render is the frozen one: "absent" means unchanged, not
    # merely missing the string.
    assert reviewed == render_case("ticket_create")
    assert create_only.replace(f"{critique}\n\n", "", 1) == reviewed


def test_the_critique_reaches_exactly_one_member_of_the_set() -> None:
    """One consumer: a critique composed into the reviewer would review twice."""
    critique = declared_critique()
    registry = registry_under(TicketReviewMode.CREATE_ONLY)
    carriers = sorted(
        name for name in ALL_CASES if critique in render_under(name, registry)
    )

    assert carriers == ["ticket_create"]


def test_the_critique_hands_the_critic_the_task_the_content_and_the_draft() -> None:
    """What the critic receives is enumerated, and the enumeration is closed."""
    rendered = " ".join(
        render_under(
            "ticket_create", registry_under(TicketReviewMode.CREATE_ONLY)
        ).split()
    )

    assert (
        "dispatch a draft-critic agent with the task, the tracker content, "
        "and your draft — nothing else" in rendered
    )
    assert "not your reasoning about the draft and not a summary of it" in rendered
    assert (
        "This critique is the only review this ticket receives; it is not optional."
        in rendered
    )


# ---------------------------------------------------------------------------
# KOD-290 — the Record clause prescribes the runner's own title
# ---------------------------------------------------------------------------

PASS_KEYS = (PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS)


@pytest.mark.parametrize("key", PASS_KEYS)
def test_the_record_clause_names_the_title_the_runner_will_verify_by(
    key: PromptKey,
) -> None:
    """One declaration, two readers: the session's row and the runner's.

    The rendered clause carries the run's title and the runner looks the
    run up by ``RunRecord.title`` — both off the same identity, and the
    comparison here is against that method rather than against a copy of
    the format, so a change to the spelling that reached only one of them
    reds.  Measured at ``00416e1``: the clause prescribed no title, and a
    per-run verification then saw no session's row at all.
    """
    identity = fixture_run_identity(key)
    rendered = (
        v5_registry()
        .template_for(key)
        .render(
            {"skills_reference": "", **pass_render_variables(key)},
        )
    )
    record = RunRecord(
        kind=identity.kind,
        name=identity.name,
        outcome=RunOutcome.COMPLETED,
        duration_seconds=1.0,
        started_at=identity.started_at,
        recorded_at=identity.started_at,
    )

    assert f"titled EXACTLY\n\n{record.title()}\n" in rendered


@pytest.mark.parametrize("key", PASS_KEYS)
def test_no_other_runs_title_reaches_the_clause(key: PromptKey) -> None:
    """The paired negative: the clause is about THIS run and no other.

    A title differing only in the instant is a different run's row, and a
    clause carrying it would send the session to write where the runner
    will not look.
    """
    identity = fixture_run_identity(key)
    neighbour = RunIdentity(
        kind=identity.kind,
        name=identity.name,
        started_at=identity.started_at + timedelta(minutes=1),
    )
    rendered = (
        v5_registry()
        .template_for(key)
        .render(
            {"skills_reference": "", **pass_render_variables(key)},
        )
    )

    assert neighbour.title() not in rendered
