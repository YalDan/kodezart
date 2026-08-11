"""Prompt registry, renderer, and set-content suite.

Replaces the pre-relocation prelude smoke tests: the same guarantees are now
asserted against the claude-opus SET content resolved through the port.
"""

import inspect
import json
import re
from pathlib import Path

import pytest
from pydantic import Field

from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.core.config import AppConfig
from kodezart.core.errors import PromptRenderError, PromptResolutionError
from kodezart.core.prompt_rendering import (
    PromptTemplate,
    binding_names,
    free_binding_names,
    render_template,
)
from kodezart.core.protocols import PromptProvider
from kodezart.domain.criteria import mint_criteria
from kodezart.domain.criteria_prompt import render_validation_findings
from kodezart.domain.prompt_variables import changeset_variables
from kodezart.domain.ticket import format_ticket_as_task
from kodezart.types.domain.agent import FileChange, TicketDraftOutput
from kodezart.types.domain.consolidation import ChangesetDigest
from kodezart.types.domain.criteria import (
    ConjunctionVerdict,
    CriteriaValidation,
    CriterionClass,
    CriterionFailure,
    CriterionFeasibility,
    CriterionVerdict,
    DraftedCriterion,
)
from kodezart.types.domain.prompts import (
    PromptKey,
    PromptSetFragments,
    PromptSetMetadata,
)
from kodezart.types.domain.ticket_review import TicketReviewMode
from tests.fakes import as_validated
from tests.prompt_census import configured_investigation_cap

DEFAULT_SET = "claude-opus"
#: The configured fan-out cap, read off the field declaration rather than a
#: constructed config: the suite must not depend on the ambient environment
#: to know what the application ships.
CONFIGURED_INVESTIGATION_CAP: int = configured_investigation_cap()
GOLDENS = Path(__file__).parent / "goldens" / "claude_opus_empty_skills"
REPO_ROOT = Path(__file__).resolve().parents[2]

TICKET = TicketDraftOutput(
    title="Golden ticket",
    summary="Golden summary",
    context="Golden context",
    references=[],
    required_changes=[
        FileChange(
            file_path="src/example.py",
            change_type="modify",
            description="golden description",
            rationale="golden rationale",
        ),
    ],
    out_of_scope=[],
    open_questions=[],
)
TASK = "Golden task description"
TASK_MD = format_ticket_as_task(TICKET)
MINTED_CRITERIA = list(
    mint_criteria(
        [
            DraftedCriterion(
                text="First criterion",
                criterion_class=CriterionClass.hard_gate,
            ),
            DraftedCriterion(
                text="Second criterion",
                criterion_class=CriterionClass.soft_signal,
            ),
        ]
    )
)
# Every criteria-consuming template downstream of the sweep is handed the
# VALIDATED shape, so the goldens render what the run renders.
CRITERIA = as_validated(MINTED_CRITERIA)


def make_criteria(*texts: str) -> list:
    """Mint AC-n identities for inline template fixtures, then validate them."""
    return as_validated(
        mint_criteria(
            [
                DraftedCriterion(
                    text=text,
                    criterion_class=CriterionClass.hard_gate,
                )
                for text in texts
            ]
        )
    )


VALIDATION = CriteriaValidation(
    verdicts=[
        CriterionFeasibility(
            criterion_id="AC-1",
            verdict=CriterionVerdict.infeasible,
            refutation="golden refutation",
        ),
        CriterionFeasibility(
            criterion_id="AC-2",
            verdict=CriterionVerdict.feasible,
        ),
    ],
    conjunction=ConjunctionVerdict(satisfiable=True),
)
DIGEST = ChangesetDigest(
    file_paths=["src/foo.py", "tests/test_foo.py"],
    commit_subjects=["feat: add foo", "test: cover foo"],
    commit_count=2,
)
EMPTY_DIGEST = ChangesetDigest(file_paths=[], commit_subjects=[], commit_count=0)
NO_FILES_DIGEST = ChangesetDigest(
    file_paths=[],
    commit_subjects=["chore: empty commit"],
    commit_count=1,
)
FAILURES = [
    CriterionFailure(
        criterion_id="AC-1",
        text="First criterion",
        reasoning="not done",
    ),
    CriterionFailure(
        criterion_id="AC-2",
        text="Second criterion",
        reasoning="missing",
    ),
]

# golden name -> (key, per-call variables). The skills fragment is bound EMPTY
# so these goldens survive KOD-46 untouched.
GOLDEN_CASES: dict[str, tuple[PromptKey, dict[str, object]]] = {
    "branch_name": (PromptKey.BRANCH_NAME, {"task": TASK}),
    "commit_message": (PromptKey.COMMIT_MESSAGE, {}),
    "acceptance_criteria": (
        PromptKey.ACCEPTANCE_CRITERIA,
        {
            "task_description": TASK_MD,
            "validation_findings": None,
            "base_ref": "main",
        },
    ),
    "acceptance_criteria__regeneration_round": (
        PromptKey.ACCEPTANCE_CRITERIA,
        {
            "task_description": TASK_MD,
            "validation_findings": render_validation_findings(
                MINTED_CRITERIA,
                VALIDATION,
            ),
            "base_ref": "kodezart/blocker-a-12345678",
        },
    ),
    "criteria_validation": (
        PromptKey.CRITERIA_VALIDATION,
        {
            "task_description": TASK_MD,
            "acceptance_criteria": CRITERIA,
            "base_ref": "main",
        },
    ),
    "implementation": (PromptKey.IMPLEMENTATION, {"task_md": TASK_MD}),
    "evaluation": (
        PromptKey.EVALUATION,
        {"criteria": CRITERIA, **changeset_variables(DIGEST)},
    ),
    "post_merge_review": (
        PromptKey.POST_MERGE_REVIEW,
        {"criteria": CRITERIA, **changeset_variables(DIGEST)},
    ),
    "evaluation__empty_changeset": (
        PromptKey.EVALUATION,
        {"criteria": CRITERIA, **changeset_variables(EMPTY_DIGEST)},
    ),
    "evaluation__no_file_paths": (
        PromptKey.EVALUATION,
        {"criteria": CRITERIA, **changeset_variables(NO_FILES_DIGEST)},
    ),
    "iteration_feedback": (
        PromptKey.ITERATION_FEEDBACK,
        {"prior_prompt": "golden prior prompt", "pending_failures": FAILURES},
    ),
    "fix": (
        PromptKey.FIX,
        {
            "task_md": TASK_MD,
            "review_feedback": "golden review feedback",
            "ci_summary": "golden ci summary",
        },
    ),
    "fix__no_optional_sections": (
        PromptKey.FIX,
        {"task_md": TASK_MD, "review_feedback": None, "ci_summary": None},
    ),
    "ticket_create": (PromptKey.TICKET_CREATE, {"task": TASK}),
    "ticket_review": (
        PromptKey.TICKET_REVIEW,
        {"task": TASK, "draft_md": TASK_MD},
    ),
    "ticket_revision": (
        PromptKey.TICKET_REVISION,
        {
            "task": TASK,
            "previous_draft_md": TASK_MD,
            "reviewer_feedback": "golden reviewer feedback",
            "reviewer_suggestions": ["first suggestion", "second suggestion"],
        },
    ),
    "ticket_revision__no_suggestions": (
        PromptKey.TICKET_REVISION,
        {
            "task": TASK,
            "previous_draft_md": TASK_MD,
            "reviewer_feedback": "golden reviewer feedback",
            "reviewer_suggestions": [],
            "reviewer_suggestions_absent": True,
        },
    ),
    "pr_description": (
        PromptKey.PR_DESCRIPTION,
        {
            "task_md": TASK_MD,
            "acceptance_criteria": CRITERIA,
            "total_iterations": 3,
        },
    ),
}


def load_registry(
    *,
    sets_root: Path | None = None,
    default_set: str = DEFAULT_SET,
    set_overrides: dict[str, str] | None = None,
    template_overrides: dict[str, str] | None = None,
    bindings: dict[str, object] | None = None,
    investigation_cap: int | None = None,
    ticket_review_mode: TicketReviewMode = TicketReviewMode.REVIEWED,
    fallback_model: str | None = None,
) -> InRepoPromptRegistry:
    """Load a registry addressing the set BY NAME (never via environment).

    ``ticket_review_mode`` defaults to the reviewed arm because that is
    what every suite calling this helper is about; the mode's own effect on
    resolution is asserted where a case passes the other value explicitly.
    """
    return InRepoPromptRegistry.load(
        sets_root=sets_root if sets_root is not None else default_sets_root(),
        default_set=default_set,
        set_overrides=set_overrides or {},
        template_overrides=template_overrides or {},
        bindings=bindings or {},
        investigation_cap=(
            investigation_cap
            if investigation_cap is not None
            else CONFIGURED_INVESTIGATION_CAP
        ),
        ticket_review_mode=ticket_review_mode,
        fallback_model=fallback_model,
    )


def write_set(
    root: Path,
    name: str,
    members: dict[str, str],
    *,
    skills: dict[str, list[str]] | None = None,
    extra_toml: str = "",
) -> Path:
    """Author a fixture set as pure data — no code change, no Python module."""
    set_dir = root / name
    set_dir.mkdir(parents=True)
    declared = skills if skills is not None else {k: [] for k in members}
    lines = [
        f'name = "{name}"',
        'engines = ["fixture-engine"]',
        "",
        "[fragments]",
        'skills_reference_header = "SKILLS:"',
        "",
        "[skills]",
    ]
    lines.extend(f"{key} = {json.dumps(names)}" for key, names in declared.items())
    if extra_toml:
        lines.append(extra_toml)
    (set_dir / "set.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for stem, body in members.items():
        (set_dir / f"{stem}.md").write_text(body + "\n", encoding="utf-8")
    return set_dir


def complete_members(marker: str) -> dict[str, str]:
    """A complete member map whose bodies are trivially identifiable."""
    return {key.value: f"{marker}:{key.value}" for key in PromptKey}


# ---------------------------------------------------------------------------
# KOD-63/AC-2 + AC-3 — byte-identity goldens, addressed by set name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("golden_name", sorted(GOLDEN_CASES))
def test_claude_opus_render_is_byte_identical_to_baseline(golden_name: str) -> None:
    """Rendered output with the skills fragment bound EMPTY matches 92597c0."""
    key, variables = GOLDEN_CASES[golden_name]
    registry = load_registry()
    rendered = registry.template_for(key).render({**variables, "skills_reference": ""})
    expected = (GOLDENS / f"{golden_name}.txt").read_text(encoding="utf-8")
    assert rendered == expected


# The two pass keys, the content-audit key, the remediation ticket and the
# knowledge-map prelude are net-new content with no 92597c0 baseline to be
# byte-identical to; every RELOCATED key is covered by the goldens.
RELOCATED_KEYS = frozenset(PromptKey) - {
    PromptKey.FIRE_PREP_PASS,
    PromptKey.GROOMING_PASS,
    PromptKey.CONTENT_AUDIT,
    PromptKey.REMEDIATION_TICKET,
    PromptKey.KNOWLEDGE_MAP,
}


def test_golden_suite_covers_every_relocated_function_key() -> None:
    """No relocated key escapes the byte-identity guarantee."""
    covered = {key for key, _ in GOLDEN_CASES.values()}
    assert covered == RELOCATED_KEYS


def test_golden_test_does_not_read_the_prompt_set_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The goldens pin the set by name, so flipping the default cannot move them."""
    monkeypatch.setenv("KODEZART_PROMPT_SET", "not-a-real-set")
    registry = load_registry()
    rendered = registry.template_for(PromptKey.COMMIT_MESSAGE).render(
        {"skills_reference": ""},
    )
    assert rendered == (GOLDENS / "commit_message.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# KOD-63/AC-4, AC-5, D-5 — composition and precedence
# ---------------------------------------------------------------------------


def test_per_step_set_override_serves_only_that_key(tmp_path: Path) -> None:
    """A two-set composition: the override wins for its key, default for the rest."""
    write_set(tmp_path, "base", complete_members("base"))
    write_set(tmp_path, "alt", {PromptKey.FIX.value: "alt:fix"})
    registry = load_registry(
        sets_root=tmp_path,
        default_set="base",
        set_overrides={PromptKey.FIX.value: "alt"},
    )
    assert registry.template_for(PromptKey.FIX).body == "alt:fix"
    assert registry.template_for(PromptKey.EVALUATION).body == "base:evaluation"
    table = registry.resolution_table()
    assert table[PromptKey.FIX] == "alt"
    assert table[PromptKey.EVALUATION] == "base"


def test_explicit_template_override_beats_both_set_layers(tmp_path: Path) -> None:
    """Layer 1 (path) > layer 2 (set override) > layer 3 (default set)."""
    write_set(tmp_path, "base", complete_members("base"))
    write_set(tmp_path, "alt", {PromptKey.FIX.value: "alt:fix"})
    explicit = tmp_path / "explicit_fix.md"
    explicit.write_text("explicit:fix\n", encoding="utf-8")

    registry = load_registry(
        sets_root=tmp_path,
        default_set="base",
        set_overrides={PromptKey.FIX.value: "alt"},
        template_overrides={PromptKey.FIX.value: str(explicit)},
    )
    assert registry.template_for(PromptKey.FIX).body == "explicit:fix"
    assert registry.resolution_table()[PromptKey.FIX] == f"template:{explicit}"


def test_default_chain_applies_only_to_keys_without_an_override(
    tmp_path: Path,
) -> None:
    """Configured overrides never fall back to the default set's content."""
    write_set(tmp_path, "base", complete_members("base"))
    write_set(tmp_path, "alt", {PromptKey.FIX.value: "alt:fix"})
    registry = load_registry(
        sets_root=tmp_path,
        default_set="base",
        set_overrides={PromptKey.FIX.value: "alt"},
    )
    bodies = {key: registry.template_for(key).body for key in PromptKey}
    assert bodies[PromptKey.FIX] == "alt:fix"
    assert all(
        bodies[key] == f"base:{key.value}"
        for key in PromptKey
        if key is not PromptKey.FIX
    )


# ---------------------------------------------------------------------------
# KOD-63/AC-5c — the single rendering path
# ---------------------------------------------------------------------------


def test_renderer_substitutes_names_including_dotted_paths() -> None:
    """Name substitution resolves both bare and dotted paths."""
    rendered = render_template(
        "{{greeting}} {{who.name}}!",
        {"greeting": "hello", "who": {"name": "world"}},
    )
    assert rendered == "hello world!"


def test_renderer_iterates_and_exposes_one_based_index() -> None:
    """{{#each}} exposes {{this}} and {{@index1}}."""
    rendered = render_template(
        "{{#each items}}{{@index1}}. {{this}}\n{{/each}}",
        {"items": ["a", "b"]},
    )
    assert rendered == "1. a\n2. b\n"


def test_renderer_iterates_over_model_fields() -> None:
    """Item fields resolve inside the block scope."""
    rendered = render_template(
        "{{#each rows}}- {{text}}: {{reasoning}}{{/each}}",
        {"rows": FAILURES},
    )
    assert rendered == "- First criterion: not done- Second criterion: missing"


def test_renderer_presence_conditional_renders_when_bound() -> None:
    """{{#if}} renders its body when the name is bound to a non-None value."""
    assert render_template("a{{#if x}}-{{x}}{{/if}}", {"x": "b"}) == "a-b"


def test_renderer_presence_conditional_omits_when_absent() -> None:
    """An absent (or None) name omits the section rather than raising."""
    assert render_template("a{{#if x}}-{{x}}{{/if}}", {}) == "a"
    assert render_template("a{{#if x}}-{{x}}{{/if}}", {"x": None}) == "a"


def test_renderer_collects_every_missing_unconditional_name_in_one_error() -> None:
    """Three unbound unconditional names produce ONE error naming all three."""
    with pytest.raises(PromptRenderError) as excinfo:
        render_template("{{alpha}} {{beta}} {{gamma}}", {})
    assert set(excinfo.value.missing) == {"alpha", "beta", "gamma"}
    message = str(excinfo.value)
    assert "alpha" in message
    assert "beta" in message
    assert "gamma" in message


def test_renderer_does_not_report_names_inside_a_false_conditional() -> None:
    """A name referenced only inside a false {{#if}} is never reported missing."""
    with pytest.raises(PromptRenderError) as excinfo:
        render_template("{{alpha}}{{#if flag}}{{hidden}}{{/if}}", {})
    assert "hidden" not in excinfo.value.missing
    assert excinfo.value.missing == ("alpha",)


def test_renderer_rejects_unbalanced_blocks() -> None:
    """A malformed template fails loudly instead of rendering half a prompt."""
    with pytest.raises(PromptRenderError):
        render_template("{{#if x}}unclosed", {"x": 1})


def test_free_names_exclude_references_an_each_frame_supplies() -> None:
    """A loop-local is a member of the iterated item, not a binding."""
    body = "{{#each repos}}{{this.url}}{{@index1}}{{/each}}"
    assert free_binding_names(body) == frozenset({"repos"})
    assert binding_names(body) == frozenset({"repos", "this.url"})


def test_free_names_keep_outer_references_made_inside_a_loop() -> None:
    """Only the item-rooted names are loop-local; the rest still bind."""
    body = "{{#each principals}}{{this.role}} in {{workspace}}{{/each}}"
    assert free_binding_names(body) == frozenset({"principals", "workspace"})


def test_free_names_keep_an_item_reference_made_outside_any_loop() -> None:
    """With no enclosing frame there is nothing to supply it — it must bind."""
    assert free_binding_names("{{this.url}}") == frozenset({"this.url"})


def test_free_names_handle_a_nested_loop_over_an_item_member() -> None:
    """The inner sequence is reached through the item, so only the outer binds."""
    body = "{{#each repos}}{{#each this.checks}}{{this}}{{/each}}{{/each}}"
    assert free_binding_names(body) == frozenset({"repos"})


def test_pyproject_gains_no_templating_dependency() -> None:
    """The renderer is custom — no new dependency was added for it."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for banned in ("jinja", "mako", "chevron", "pystache", "handlebars"):
        assert banned not in pyproject.lower()


def test_no_shipped_template_ends_in_a_blank_line() -> None:
    """`_read_member` drops one trailing newline; a second one is content.

    An editor that removes a trailing section leaves the blank line that
    separated it, and the surviving newline reaches the model as prompt
    text.  Nothing else in the suite reads the files as bytes.
    """
    root = default_sets_root()
    members = sorted(root.glob("*/*.md"))
    assert members
    framing = {str(path.relative_to(root)): path.read_bytes()[-2:] for path in members}
    assert {
        name: tail for name, tail in framing.items() if not tail.endswith(b"\n")
    } == {}
    assert {name: tail for name, tail in framing.items() if tail == b"\n\n"} == {}


# ---------------------------------------------------------------------------
# KOD-63/AC-6, AC-7a, AC-7b, AC-9b — typed boot failures
# ---------------------------------------------------------------------------


def test_unknown_set_is_a_typed_boot_error(tmp_path: Path) -> None:
    """An unknown default set raises, listing the sets that do exist."""
    write_set(tmp_path, "base", complete_members("base"))
    with pytest.raises(PromptResolutionError) as excinfo:
        load_registry(sets_root=tmp_path, default_set="missing-set")
    assert excinfo.value.available_sets == ("base",)


def test_unknown_set_in_an_override_is_a_typed_boot_error(tmp_path: Path) -> None:
    """A set override naming an unknown set fails; it never falls back."""
    write_set(tmp_path, "base", complete_members("base"))
    with pytest.raises(PromptResolutionError) as excinfo:
        load_registry(
            sets_root=tmp_path,
            default_set="base",
            set_overrides={PromptKey.FIX.value: "nope"},
        )
    assert PromptKey.FIX.value in excinfo.value.failing_keys


def test_key_missing_from_the_named_set_is_a_typed_boot_error(tmp_path: Path) -> None:
    """An override pointing at a set that lacks the key fails loudly."""
    write_set(tmp_path, "base", complete_members("base"))
    write_set(tmp_path, "alt", {PromptKey.EVALUATION.value: "alt:evaluation"})
    with pytest.raises(PromptResolutionError) as excinfo:
        load_registry(
            sets_root=tmp_path,
            default_set="base",
            set_overrides={PromptKey.FIX.value: "alt"},
        )
    assert PromptKey.FIX.value in excinfo.value.failing_keys


def test_unreadable_template_override_is_a_typed_boot_error(tmp_path: Path) -> None:
    """A template override path that is not a readable file fails loudly."""
    write_set(tmp_path, "base", complete_members("base"))
    with pytest.raises(PromptResolutionError) as excinfo:
        load_registry(
            sets_root=tmp_path,
            default_set="base",
            template_overrides={PromptKey.FIX.value: str(tmp_path / "absent.md")},
        )
    assert PromptKey.FIX.value in excinfo.value.failing_keys


def test_unknown_function_key_in_an_override_is_a_typed_boot_error(
    tmp_path: Path,
) -> None:
    """Override maps are validated against the function-key enum."""
    write_set(tmp_path, "base", complete_members("base"))
    with pytest.raises(PromptResolutionError) as excinfo:
        load_registry(
            sets_root=tmp_path,
            default_set="base",
            set_overrides={"not_a_key": "base"},
        )
    assert "not_a_key" in excinfo.value.failing_keys


def test_incomplete_default_set_fails_and_lists_every_unresolvable_key(
    tmp_path: Path,
) -> None:
    """Boot validation reports ALL unresolvable keys, not the first one."""
    members = complete_members("base")
    del members[PromptKey.FIX.value]
    del members[PromptKey.EVALUATION.value]
    write_set(tmp_path, "base", members)
    with pytest.raises(PromptResolutionError) as excinfo:
        load_registry(sets_root=tmp_path, default_set="base")
    assert PromptKey.FIX.value in excinfo.value.failing_keys
    assert PromptKey.EVALUATION.value in excinfo.value.failing_keys


def test_partial_additional_set_is_legal(tmp_path: Path) -> None:
    """A partial set loads fine as long as the DEFAULT set is complete."""
    write_set(tmp_path, "base", complete_members("base"))
    write_set(tmp_path, "partial", {PromptKey.FIX.value: "partial:fix"})
    registry = load_registry(sets_root=tmp_path, default_set="base")
    assert registry.template_for(PromptKey.FIX).body == "base:fix"


def test_new_function_key_forces_the_default_set_to_supply_it() -> None:
    """The claude-opus member files are exactly the function-key enum."""
    members = {path.stem for path in (default_sets_root() / DEFAULT_SET).glob("*.md")}
    assert members == {key.value for key in PromptKey}


def test_default_set_missing_a_skills_entry_is_a_boot_error(tmp_path: Path) -> None:
    """The default set must declare a [skills] loadout for every key."""
    skills = {key.value: [] for key in PromptKey}
    del skills[PromptKey.FIX.value]
    write_set(tmp_path, "base", complete_members("base"), skills=skills)
    with pytest.raises(PromptResolutionError) as excinfo:
        load_registry(sets_root=tmp_path, default_set="base")
    assert PromptKey.FIX.value in excinfo.value.failing_keys


# ---------------------------------------------------------------------------
# KOD-63/AC-9a, D-6, R-5 — sets are data
# ---------------------------------------------------------------------------


def test_adding_a_set_requires_zero_code_changes(tmp_path: Path) -> None:
    """A fixture directory with set.toml plus .md members resolves as authored."""
    write_set(tmp_path, "base", complete_members("base"))
    write_set(tmp_path, "authored", complete_members("authored"))
    registry = load_registry(sets_root=tmp_path, default_set="authored")
    assert registry.template_for(PromptKey.FIX).body == "authored:fix"


def test_no_python_module_lives_under_the_sets_tree() -> None:
    """Set members are DATA — never a Python callable."""
    assert list(default_sets_root().rglob("*.py")) == []


def test_physical_layout_is_set_toml_plus_one_md_per_member() -> None:
    """R-5: sets/<set-name>/set.toml plus one <function-key>.md per member."""
    set_dir = default_sets_root() / DEFAULT_SET
    assert (set_dir / "set.toml").is_file()
    names = sorted(p.name for p in set_dir.iterdir())
    assert names == sorted(
        ["set.toml", *[f"{key.value}.md" for key in PromptKey]],
    )


def test_set_name_is_not_a_code_enum() -> None:
    """A set is named by an open configuration string, never a code enum."""
    config_source = (REPO_ROOT / "src" / "kodezart" / "core" / "config.py").read_text(
        encoding="utf-8"
    )
    assert "prompt_set: str" in config_source
    registry_source = (
        REPO_ROOT / "src" / "kodezart" / "adapters" / "in_repo_prompt_registry.py"
    ).read_text(encoding="utf-8")
    assert "StrEnum" not in registry_source
    assert DEFAULT_SET not in registry_source


# ---------------------------------------------------------------------------
# KOD-63/D-2, D-8, D-9, R-4, R-11 — design properties
# ---------------------------------------------------------------------------


def test_post_merge_review_is_a_distinct_key_with_identical_content() -> None:
    """Distinct keys, same claude-opus content — a set can split them later."""
    registry = load_registry()
    evaluation = registry.template_for(PromptKey.EVALUATION)
    post_merge = registry.template_for(PromptKey.POST_MERGE_REVIEW)
    assert evaluation.key is not post_merge.key
    assert evaluation.body == post_merge.body


def test_a_fixture_set_splits_evaluation_from_post_merge_review(
    tmp_path: Path,
) -> None:
    """Supplying only the post-merge key splits the roles with no code change."""
    write_set(tmp_path, "base", complete_members("base"))
    write_set(tmp_path, "split", {PromptKey.POST_MERGE_REVIEW.value: "split:post"})
    registry = load_registry(
        sets_root=tmp_path,
        default_set="base",
        set_overrides={PromptKey.POST_MERGE_REVIEW.value: "split"},
    )
    assert registry.template_for(PromptKey.POST_MERGE_REVIEW).body == "split:post"
    assert registry.template_for(PromptKey.EVALUATION).body == "base:evaluation"


def test_provider_returns_an_unrendered_template() -> None:
    """The port's return type is a template; substitution happens elsewhere."""
    registry = load_registry()
    template = registry.template_for(PromptKey.BRANCH_NAME)
    assert isinstance(template, PromptTemplate)
    assert "{{task}}" in template.body


def test_registry_performs_no_placeholder_substitution() -> None:
    """No second substitution implementation exists in the registry adapter."""
    source = (
        REPO_ROOT / "src" / "kodezart" / "adapters" / "in_repo_prompt_registry.py"
    ).read_text(encoding="utf-8")
    assert "{{" not in source
    assert ".replace(" not in source
    assert "re.sub" not in source


def test_exactly_one_rendering_entry_point_exists() -> None:
    """KOD-54/AC-1: one renderer, N sets, any composition."""
    src = REPO_ROOT / "src" / "kodezart"
    tag_pattern = re.compile(r"\\\{\\\{|\{\{\(\.\*\?\)\}\}")
    hits = [
        path.relative_to(src).as_posix()
        for path in src.rglob("*.py")
        if tag_pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert hits == ["core/prompt_rendering.py"]


def test_prompt_provider_exposes_no_augment_specific_method() -> None:
    """R-4: iteration feedback is an ordinary keyed template."""
    methods = {
        name
        for name, _ in inspect.getmembers(PromptProvider, inspect.isfunction)
        if not name.startswith("_")
    }
    assert methods == {"template_for", "resolution_table", "declared_skills"}
    assert not any("augment" in name for name in methods)


def test_set_metadata_rejects_an_unknown_section(tmp_path: Path) -> None:
    """D-8: an unknown section is a typed boot error."""
    write_set(
        tmp_path,
        "base",
        complete_members("base"),
        extra_toml='\n[unknown_section]\nfoo = "bar"\n',
    )
    with pytest.raises(ValueError, match="unknown_section"):
        load_registry(sets_root=tmp_path, default_set="base")


def test_set_metadata_is_additively_extensible(tmp_path: Path) -> None:
    """D-8: a new section is one more field, not a reshape of the type."""

    class ExtendedMetadata(PromptSetMetadata):
        """KOD-68 shape: metadata plus one additional optional section."""

        agents: dict[str, list[str]] = Field(default_factory=dict)

    set_dir = write_set(tmp_path, "base", complete_members("base"))
    raw = (set_dir / "set.toml").read_text(encoding="utf-8")
    raw += '\n[agents]\nfix = ["explorer"]\n'
    import tomllib

    extended = ExtendedMetadata.model_validate(tomllib.loads(raw))
    assert extended.agents == {"fix": ["explorer"]}
    assert extended.name == "base"
    assert set(extended.skills) == {key.value for key in PromptKey}


def test_claude_opus_declares_a_skills_loadout_for_every_key() -> None:
    """R-11: [skills] is declared for every key, empty allowed."""
    registry = load_registry()
    for key in PromptKey:
        assert isinstance(registry.declared_skills(key), tuple)


def test_utility_keys_declare_an_empty_skills_loadout() -> None:
    """R-11: the utility keys carry an explicit empty list."""
    registry = load_registry()
    utility = (
        PromptKey.BRANCH_NAME,
        PromptKey.TICKET_REVISION,
        PromptKey.COMMIT_MESSAGE,
        PromptKey.PR_DESCRIPTION,
    )
    for key in utility:
        assert registry.declared_skills(key) == ()


# ---------------------------------------------------------------------------
# KOD-63/D-7 — the model knob is a separate axis
# ---------------------------------------------------------------------------


def test_prompt_resolution_never_reads_the_model_knob() -> None:
    """D-7: KODEZART_MODEL is not an input to prompt resolution."""
    registry_source = (
        REPO_ROOT / "src" / "kodezart" / "adapters" / "in_repo_prompt_registry.py"
    ).read_text(encoding="utf-8")
    identifiers = [
        name
        for name in re.findall(r"\bmodel\w*", registry_source)
        if name != "model_validate"
    ]
    assert identifiers == []
    signature = inspect.signature(InRepoPromptRegistry.load)
    assert "model" not in signature.parameters


def test_set_selection_is_independent_of_model_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing the model knob does not change the resolution table."""
    monkeypatch.setenv("KODEZART_MODEL", "some-other-engine")
    before = load_registry().resolution_table()
    monkeypatch.delenv("KODEZART_MODEL")
    after = load_registry().resolution_table()
    assert before == after


# ---------------------------------------------------------------------------
# KOD-63/AC-1 — no consumer imports the prompt package
# ---------------------------------------------------------------------------


def test_no_module_imports_the_prompt_package() -> None:
    """Static guard: the prompt package holds no Python and nothing imports it."""
    needle = "kodezart." + "prompts"
    offenders: list[str] = []
    for root in (REPO_ROOT / "src", REPO_ROOT / "tests"):
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if path == Path(__file__):
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped.startswith(("import ", "from ")):
                    continue
                if needle in stripped:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {stripped}")
    assert offenders == []


# ---------------------------------------------------------------------------
# Set-content suite (migrated from the pre-relocation prelude smoke tests)
# ---------------------------------------------------------------------------


def test_evaluation_set_content_contains_watson_dispatch() -> None:
    """The evaluation member still carries the Sherlock/Watson dispatch."""
    registry = load_registry()
    output = registry.template_for(PromptKey.EVALUATION).render(
        {"criteria": make_criteria("Tests pass"), **changeset_variables(EMPTY_DIGEST)},
    )
    assert "WATSON 1" in output
    assert "graceful degradation" in output
    assert "Tests pass" in output


def test_acceptance_criteria_set_content_contains_watson_dispatch() -> None:
    """The acceptance-criteria member still carries the Watson dispatch."""
    registry = load_registry()
    output = registry.template_for(PromptKey.ACCEPTANCE_CRITERIA).render(
        {"task_description": "Implement feature X", "base_ref": "main"},
    )
    assert "WATSON 1" in output
    assert "graceful degradation" in output
    assert "Implement feature X" in output


def test_iteration_feedback_set_content_contains_watson_dispatch() -> None:
    """The iteration-feedback member still carries the Watson dispatch."""
    registry = load_registry()
    output = registry.template_for(PromptKey.ITERATION_FEEDBACK).render(
        {"prior_prompt": "base task prompt", "pending_failures": FAILURES},
    )
    assert "WATSON 1" in output
    assert "graceful degradation" in output
    assert "First criterion" in output
    assert "base task prompt" in output


def test_evaluation_inlines_the_changeset_digest_as_data() -> None:
    """Commit subjects and file paths appear verbatim; no shell commands."""
    registry = load_registry()
    rendered = registry.template_for(PromptKey.EVALUATION).render(
        {"criteria": make_criteria("Tests pass"), **changeset_variables(DIGEST)},
    )
    assert "src/foo.py" in rendered
    assert "feat: add foo" in rendered
    assert "git diff" not in rendered
    assert "git log" not in rendered


def test_evaluation_handles_an_empty_changeset() -> None:
    """Empty digest renders the deterministic escape clause."""
    registry = load_registry()
    rendered = registry.template_for(PromptKey.EVALUATION).render(
        {"criteria": make_criteria("Tests pass"), **changeset_variables(EMPTY_DIGEST)},
    )
    assert (
        "No commits between the base and head refs; the previous "
        "verdict's failures persist unchanged."
    ) in rendered


# ---------------------------------------------------------------------------
# AppConfig surface
# ---------------------------------------------------------------------------


def test_override_mappings_parse_from_json_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both override mappings are JSON-valued env entries."""
    monkeypatch.setenv("KODEZART_PROMPT_SET", "alt")
    monkeypatch.setenv("KODEZART_PROMPT_SET_OVERRIDES", '{"fix": "alt"}')
    monkeypatch.setenv(
        "KODEZART_PROMPT_TEMPLATE_OVERRIDES",
        '{"evaluation": "/tmp/e.md"}',
    )
    config = AppConfig.from_env()
    assert config.prompt_set == "alt"
    assert config.prompt_set_overrides == {"fix": "alt"}
    assert config.prompt_template_overrides == {"evaluation": "/tmp/e.md"}


def test_prompt_set_fragments_reject_unknown_keys() -> None:
    """Fragment metadata is closed like the rest of the set metadata."""
    with pytest.raises(ValueError, match="extra_fragment"):
        PromptSetFragments.model_validate(
            {"skills_reference_header": "x", "extra_fragment": "y"},
        )


# ---------------------------------------------------------------------------
# KOD-90-AC-6 (registry half) — a slot the mode owes and the set cannot fill
# ---------------------------------------------------------------------------

CRITIQUE_SLOT = "{{#if ticket_create_critique}}{{ticket_create_critique}}{{/if}}"


def test_a_slotted_create_member_without_a_critique_fragment_fails_create_only(
    tmp_path: Path,
) -> None:
    """The mode's only review cannot be silently absent: boot names the key."""
    members = complete_members("base")
    members[PromptKey.TICKET_CREATE.value] += CRITIQUE_SLOT
    write_set(tmp_path, "base", members)

    with pytest.raises(PromptResolutionError) as excinfo:
        load_registry(
            sets_root=tmp_path,
            default_set="base",
            ticket_review_mode=TicketReviewMode.CREATE_ONLY,
        )

    assert PromptKey.TICKET_CREATE.value in excinfo.value.failing_keys


def test_the_same_set_resolves_under_the_reviewed_mode(tmp_path: Path) -> None:
    """Non-vacuity: the refusal is the MODE's, not the slot's."""
    members = complete_members("base")
    members[PromptKey.TICKET_CREATE.value] += CRITIQUE_SLOT
    write_set(tmp_path, "base", members)

    registry = load_registry(
        sets_root=tmp_path,
        default_set="base",
        ticket_review_mode=TicketReviewMode.REVIEWED,
    )

    assert (
        registry.template_for(PromptKey.TICKET_CREATE).render({})
        == "base:ticket_create"
    )


def test_a_declared_critique_fills_the_slot_under_create_only(tmp_path: Path) -> None:
    """The fragment reaches the render from set metadata, not from the caller."""
    members = complete_members("base")
    members[PromptKey.TICKET_CREATE.value] += CRITIQUE_SLOT
    set_dir = write_set(tmp_path, "base", members)
    metadata = set_dir / "set.toml"
    metadata.write_text(
        metadata.read_text(encoding="utf-8").replace(
            "[fragments]\n",
            '[fragments]\nticket_create_critique = "critique the draft"\n',
            1,
        ),
        encoding="utf-8",
    )

    registry = load_registry(
        sets_root=tmp_path,
        default_set="base",
        ticket_review_mode=TicketReviewMode.CREATE_ONLY,
    )

    assert "critique the draft" in registry.template_for(
        PromptKey.TICKET_CREATE
    ).render({})
