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
from kodezart.domain.prompt_variables import (
    execution_criteria_variables,
    scope_variables,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.run_records import RunIdentity, RunOutcome, RunRecord
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.ticket_review import TicketReviewMode
from tests.fakes import fixture_run_identity, pass_render_variables
from tests.prompts.sets import (
    ALL_CASES,
    EXAMPLE_OPERATION,
    V5_SET,
    render_v5_case,
    v5_registry,
)
from tests.prompts.style_detectors import (
    artifact_tag_names,
    data_boundary_sentences,
    unbalanced_artifact_tags,
)
from tests.prompts.test_prompt_wiring import CRITERIA, load_registry

#: Which named tag each injected artifact must arrive inside. Keyed by the
#: shared fixture case, so the expectation is stated per rendering rather
#: than per key — the regeneration round injects one the first round does
#: not, and that difference is the point of listing them separately.
ORGANIZE_INPUT_TAGS = (
    "mandate_rubric",
    "issue_key",
    "organize_context",
    "issue_body",
    "linked_issue_bodies",
    "linked_issue",
    "criterion_issue_bodies",
    "criterion_issue",
    "base_ref",
    "defect_classes",
)

#: The two admission roles and the criteria author carry one artifact the
#: body author does not: the operation's declared environments, rendered
#: between the base ref and the defect classes.
ORGANIZE_ADMISSION_TAGS = (
    *ORGANIZE_INPUT_TAGS[:-1],
    "declared_environments",
    ORGANIZE_INPUT_TAGS[-1],
)

ARTIFACT_TAGS: dict[str, tuple[str, ...]] = {
    "audit_overclaim": ("criterion_key", "graded_sha", "head_sha", "check"),
    "audit_mandate": (
        "defect_class",
        "refutation_evidence",
        "head_sha",
        "audited_surfaces",
    ),
    "audit_claim": ("criterion_key", "head_sha", "check"),
    "audit_detection_removal": ("criterion_key", "graded_sha", "head_sha", "check"),
    "organize_assess": ORGANIZE_ADMISSION_TAGS,
    "organize_verify": ORGANIZE_ADMISSION_TAGS,
    "organize_author": (*ORGANIZE_INPUT_TAGS, "refusal_evidence"),
    "organize_criteria_author": (*ORGANIZE_ADMISSION_TAGS, "refusal_evidence"),
    # A rubric is the standard a judging role is handed; it carries no
    # injected artifact of its own and therefore no tag.
    "organize_spec_rubric": (),
    # The session's prompt lists the marker and the member keys as plain
    # lines; the members are addresses, not injected artifacts.
    "organize_session": (),
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
    # The gate's two per-tick values are a name and a timestamp, rendered as
    # plain lines; neither is an injected artifact.
    "pass_gate": (),
    # The scope questions render the boundary and the parent as plain lines.
    "scope_scan": (),
    "scope_done": (),
    "fix": ("ticket", "review_feedback", "ci_summary"),
    "fix__no_optional_sections": ("ticket",),
    "grooming_pass": (),
    "supervisor_pass": (),
    "implementation": ("ticket",),
    "iteration_feedback": ("failed_criteria",),
    "knowledge_map": (),
    "fire_record": (),
    # The criteria roster is the only thing the removal member renders, and
    # it renders as plain lines rather than inside a named tag.
    "mutation_survival": (),
    # The base reading renders the same roster the same way, beside the sha
    # it names in prose.
    "base_check": (),
    "fire_time_ruling": ("issue_key", "pinned_answers", "task_md"),
    "native_writer_contract": ("pinned_rulings",),
    "amendment_judge": ("claim", "current_criteria", "pinned_rulings", "base_sha"),
    "amendment_author": (
        "claim",
        "independent_judgment",
        "exact_prior_artifact",
        "write_back_finding",
        "preserve_subject",
    ),
    "write_back_verify": ("base_ref", "written_artifact"),
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
    rendered = render_v5_case(golden_name)
    assert artifact_tag_names(rendered) == ARTIFACT_TAGS[golden_name]
    assert unbalanced_artifact_tags(rendered) == ()


@pytest.mark.parametrize("golden_name", sorted(ARTIFACT_TAGS))
def test_exactly_one_boundary_sentence_per_artifact_carrying_render(
    golden_name: str,
) -> None:
    """One sentence where there are artifacts, none where there are not."""
    rendered = render_v5_case(golden_name)
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
    rendered = render_v5_case(golden_name)
    opening = rendered.index("<ticket>")
    closing = rendered.index("</ticket>")
    assert "Golden ticket" in rendered[opening:closing]


def test_the_empty_changeset_escape_clause_renders() -> None:
    """Behaviour preserved: an empty digest says so rather than saying nothing."""
    empty = render_v5_case("evaluation__empty_changeset")
    assert EMPTY_CHANGESET_CLAUSE in empty

    populated = render_v5_case("evaluation")
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
    assert "orchestration_block" not in render_v5_case("acceptance_criteria")
    assert "orchestration_block" not in render_v5_case("ticket_create")


def test_the_rendered_evaluator_is_under_the_size_bound() -> None:
    """AC-7, informational: an upper bound, never an equality."""
    assert len(render_v5_case("evaluation").split()) < EVALUATION_WORD_BOUND


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
    assert reviewed == render_v5_case("ticket_create")
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
# KOD-1305 — fire-prep prepares each fire as a unit with edges (item 6), and
# KOD-1285 — a tick stages a bounded number of fires
# ---------------------------------------------------------------------------

#: Each sentence item 6 put in, once; the wrapper carries no base line.
FIRE_PREP_GRAPH_TEXTS = (
    "a base, a pinned target or a dependency is never a wrapper field, because"
    " the delivery rule below derives the base from blocking edges.",
    "list the open pull requests and the unit issue each is attached to,",
    "the base is what its unit's blocking edges now give",
    "Read the real code at the base the delivery rule gives the unit — the"
    " heads of the units it is blocked by, merged or not, their union, or the"
    " trunk — and treat something as missing only when it is absent there.",
    "exists on the branches of the units it is blocked by, merged or not,",
    "its dependencies are blocking edges to the unit issues that carry them,"
    " set with the fire and never written in its body",
    "Wrap it in the issue as scope, ticket type, the consulted section",
    "blocked by that request's unit",
    "each with the units it is blocked by;",
)

#: The recency base rule and the wrapper base fields, gone.
FIRE_PREP_RETIRED_TEXTS = (
    "base-branch mode",
    "the head of the latest open request when one exists, else the trunk",
    "find the head of the latest one",
    "with the base pinned to that request's branch",
    "each with its resolved base branch",
)

#: KOD-1285: one tick's work is bounded, so it ends inside its budget.
FIRE_PREP_TICK_CAP = (
    "One tick takes up at most eight problem groups, the oldest triage items"
    " first, and stops; the rest wait for the next tick, so a tick ends inside"
    " its budget with its record row written instead of being cancelled"
    " mid-draft. The row names every triage item and every response-set item"
    " left untaken, so the next tick's gate counts them as work and its sweeps"
    " read them again whether or not they moved."
)

#: The cap's neighbours agree with it: the finish promise, the one-act rule
#: and the record row all speak of the items the tick took up (KOD-1285).
FIRE_PREP_CAP_NEIGHBOURS = (
    "You finish the prep of every item you take up: nothing is left for the"
    " grooming pass except a genuine principal decision, and what the tick's"
    " cap leaves untaken is named in the record row for the next tick.",
    "By the end of a run each triaged item the tick took up is a fire-ready issue,",
    "First, once every disposition and reply of the items you took up is"
    " complete, write this run's record row, naming what the cap left untaken",
)

#: Texts a retired rule could sneak back through, inside a conditional no
#: render of the example config reaches: guarded on the files themselves.
FIRE_PREP_FILE_ABSENT_TEXTS = (
    *FIRE_PREP_RETIRED_TEXTS,
    "keep staging until every",
    "nothing is left for the grooming pass or for a future run",
)


def _rendered_pass(key: PromptKey) -> str:
    return (
        v5_registry()
        .template_for(key)
        .render({"skills_reference": "", **pass_render_variables(key)})
    )


def _pass_source(key: PromptKey) -> str:
    """The member file itself, every conditional branch included."""
    return (default_sets_root() / V5_SET / f"{key.value}.md").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    "text", (*FIRE_PREP_GRAPH_TEXTS, FIRE_PREP_TICK_CAP, *FIRE_PREP_CAP_NEIGHBOURS)
)
def test_fire_prep_grounds_each_fire_on_its_blockers_branches(text: str) -> None:
    """Each sentence renders exactly once in the fire-prep prompt."""
    assert _rendered_pass(PromptKey.FIRE_PREP_PASS).count(text) == 1


@pytest.mark.parametrize("text", FIRE_PREP_FILE_ABSENT_TEXTS)
def test_fire_prep_names_no_base_branch_wrapper_field(text: str) -> None:
    """The base follows from the edges; no wrapper field and no recency rule,
    in the file itself, so no conditional branch can carry one."""
    assert text.casefold() not in _rendered_pass(PromptKey.FIRE_PREP_PASS).casefold()
    assert text.casefold() not in _pass_source(PromptKey.FIRE_PREP_PASS).casefold()


# ---------------------------------------------------------------------------
# KOD-1305 — grooming keeps units and edges true (item 7), stops its noise
# (item 8), and KOD-1283 — never ends a process by pattern
# ---------------------------------------------------------------------------

GROOMING_GRAPH_TEXTS = (
    "a dependency, base-branch or prerequisite line there becomes, once"
    " verified, the blocking edge between units it stands for, and is removed"
    " with a fields-only edit plus a comment, touching nothing else; a line"
    " relaying a principal's hold goes only when that principal's later ruling"
    " is recorded, and hold text a principal wrote is never edited: ask that"
    " principal once whether it becomes an edge.",
    "build each unit's request head that moved since the window started, for a"
    " verdict of its own, then the composition of the graph's ends — the open"
    " units no other open unit is blocked by — merged in your clone, and report"
    " the composition's verdict beside the per-unit verdicts, never folded into"
    " one of them;",
    "each request is attached to the one unit issue whose issues it carries,"
    " and a missing attachment is yours to add; act on the supervisor pass's"
    " findings addressed to you:",
    "a request whose head holds another open unit's work gets an edge only when"
    " that dependency is real.",
    "An edge between issues of two different units is set between their unit"
    " issues, because the unit is what merges.",
    "Compose in your clone and push nothing you composed; delete the composition"
    " branches earlier passes of this kind pushed.",
    "Never end a process by pattern: end only a process id this pass started.",
    "One status update per initiative whose health changed or under which"
    " something moved in the window, and none for the others, whose trace is"
    " this pass's record row; it opens with the land queue — the requests that"
    " can merge now: based on the trunk, ready for review, every blocker merged"
    " and no hold open — derived this pass.",
    "or whose health changed, every pass:",
    "a pass with a healthy build, no findings and no initiative that moved posts"
    " no update and sends no notification.",
    "record the query, its empty result and what you did not cover in this"
    " pass's record row, and in the status update of an initiative only when"
    " that initiative gets one this pass, never as a comment,",
)

GROOMING_RETIRED_TEXTS = (
    "push them",
    "Push what you composed",
    "the most recently updated on a tie",
    "every pass, even when nothing changed",
    "or carries a target date, every pass",
    "is never a reason to withhold the composition",
    "still posts the initiative updates",
    "in the initiative status update, never as a comment",
    "pkill",
)


@pytest.mark.parametrize("text", GROOMING_GRAPH_TEXTS)
def test_grooming_keeps_units_and_edges_true_and_composes_without_pushing(
    text: str,
) -> None:
    """Each sentence renders exactly once in the grooming prompt."""
    assert _rendered_pass(PromptKey.GROOMING_PASS).count(text) == 1


@pytest.mark.parametrize("text", GROOMING_RETIRED_TEXTS)
def test_grooming_pushes_no_composition_and_reports_only_on_change(
    text: str,
) -> None:
    """The recency tie-break, the pushed compositions and the per-pass status
    update on unchanged initiatives are gone, from the file itself."""
    assert text.casefold() not in _rendered_pass(PromptKey.GROOMING_PASS).casefold()
    assert text.casefold() not in _pass_source(PromptKey.GROOMING_PASS).casefold()


# ---------------------------------------------------------------------------
# KOD-290 — the Record clause prescribes the runner's own title
# ---------------------------------------------------------------------------

PASS_KEYS = (
    PromptKey.FIRE_PREP_PASS,
    PromptKey.GROOMING_PASS,
    PromptKey.SUPERVISOR_PASS,
)


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


# ---------------------------------------------------------------------------
# KOD-1304 — a scope run's grader works at each unit's pull-request head
# ---------------------------------------------------------------------------

#: The clauses of the scope grader's instruction, each its own case, so a
#: rewrite that drops any one of them reds on its own. None of them is in a
#: scope run's rendered evaluation before KOD-1304: the draft rule already
#: says "pushed head", so that phrase alone proves nothing.
SCOPE_GRADING_CLAUSES: tuple[str, ...] = (
    "fetch the pull request's pushed head into the repository's checkout",
    "check it out detached, one worktree per unit, and grade there",
    "never on the trunk or on this run's own branch",
    "a criterion whose unit has no pull request with a pushed head fails "
    "for that reason",
    "never commit or push, and write nothing on the tracker",
)


def _scope_evaluation() -> str:
    """The evaluation a scope run renders: criteria and scope, no changeset."""
    return (
        v5_registry()
        .template_for(PromptKey.EVALUATION)
        .render(
            {
                **execution_criteria_variables(CRITERIA),
                **scope_variables(ScopeRef(kind=ScopeKind.PROJECT, key="project")),
                "skills_reference": "",
            }
        )
    )


@pytest.mark.parametrize("clause", SCOPE_GRADING_CLAUSES)
def test_a_scope_run_grades_each_unit_at_its_pull_request_head(clause: str) -> None:
    """Rendered for a scope run, and absent from every per-issue evaluation."""
    assert clause in _scope_evaluation()
    for case in ("evaluation", "evaluation__empty_changeset"):
        assert clause not in render_v5_case(case), case


def test_a_scope_run_is_shown_no_loop_branch_changeset() -> None:
    """The loop branch holds none of the units' work, so nothing of it is shown.

    A per-issue evaluation still carries the changeset block and, when the
    branch is empty, the clause that says so.
    """
    scoped = _scope_evaluation()
    assert "<changeset>" not in scoped
    assert "No commits between" not in scoped
    assert "Evaluate whether the changeset below" not in scoped
    assert "<changeset>" in render_v5_case("evaluation")
    assert "No commits between" in render_v5_case("evaluation__empty_changeset")
