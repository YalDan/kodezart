"""KOD-92 — per-role session policy, read from the set.

Every assertion here is about DATA: which role a key belongs to, what that
role declares, and what a set that declares nothing produces.  The runtime
half — what actually reaches the executor at each dispatch — is asserted in
the chain modules the criteria name by path.
"""

from pathlib import Path

import pytest

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.core.errors import PromptResolutionError
from kodezart.types.domain.prompts import (
    PromptKey,
    PromptSetMetadata,
    SessionRole,
)
from kodezart.types.domain.subagents import SessionEffort
from tests.prompts.test_claude_opus_goldens import V5_SET
from tests.prompts.test_prompt_wiring import (
    DEFAULT_SET,
    complete_members,
    load_registry,
    write_set,
)

V5_SET_DIR = default_sets_root() / V5_SET

#: The ladder, in the enum's own declaration order — low through max.  A
#: test that hard-codes the sequence states the ordering twice.
LADDER: tuple[SessionEffort, ...] = tuple(SessionEffort)

#: The set's own declared role → effort, as the fire-time ruling FR-2 fixed
#: it: the harness default named, one level below it for judgment, and the
#: floor for the roles that emit a name or a message.
EXPECTED_EFFORT: dict[SessionRole, SessionEffort] = {
    SessionRole.GENERATIVE: SessionEffort.XHIGH,
    SessionRole.IMPLEMENTATION: SessionEffort.XHIGH,
    SessionRole.EVALUATIVE: SessionEffort.HIGH,
    SessionRole.UTILITY: SessionEffort.LOW,
}


def v5_metadata() -> PromptSetMetadata:
    """The new set's metadata, parsed the way the registry parses it."""
    import tomllib

    raw = (V5_SET_DIR / "set.toml").read_text(encoding="utf-8")
    return PromptSetMetadata.model_validate(tomllib.loads(raw))


def rank(effort: SessionEffort) -> int:
    """Where *effort* sits on the ladder."""
    return LADDER.index(effort)


# ---------------------------------------------------------------------------
# AC-1 — every key resolves to exactly one role
# ---------------------------------------------------------------------------


def test_every_registered_key_belongs_to_exactly_one_role() -> None:
    """A key in two rosters, or in none, is a policy question with no answer."""
    metadata = v5_metadata()
    assignments = {
        key.value: [
            role
            for role, policy in metadata.session_roles.items()
            if key.value in policy.keys
        ]
        for key in PromptKey
    }
    assert {key: roles for key, roles in assignments.items() if len(roles) != 1} == {}


def test_the_role_roster_covers_the_registered_census_and_nothing_else() -> None:
    """No role names a key the enum does not have."""
    metadata = v5_metadata()
    rostered = {
        key for policy in metadata.session_roles.values() for key in policy.keys
    }
    assert rostered == {key.value for key in PromptKey}


@pytest.mark.parametrize("role", sorted(EXPECTED_EFFORT, key=lambda r: r.value))
def test_each_role_declares_the_effort_the_ruling_fixed(role: SessionRole) -> None:
    """The metadata is the source; this pins what it currently says."""
    assert v5_metadata().session_roles[role].effort is EXPECTED_EFFORT[role]


@pytest.mark.parametrize("key", list(PromptKey))
def test_the_registry_serves_each_key_the_effort_of_its_role(key: PromptKey) -> None:
    """Resolution goes through the role, so the mapping is not restated."""
    metadata = v5_metadata()
    registry = load_registry(default_set=V5_SET)
    role = metadata.role_of(key.value)

    assert role is not None
    assert registry.session_policy(key).effort is metadata.session_roles[role].effort


def test_judgment_sits_strictly_below_authoring_on_the_ladder() -> None:
    """The substance of the policy: grading is cheaper work than authoring."""
    assert rank(EXPECTED_EFFORT[SessionRole.EVALUATIVE]) < rank(
        EXPECTED_EFFORT[SessionRole.GENERATIVE],
    )
    assert rank(EXPECTED_EFFORT[SessionRole.UTILITY]) < rank(
        EXPECTED_EFFORT[SessionRole.EVALUATIVE],
    )


def test_a_key_no_role_claims_is_a_typed_boot_error(tmp_path: Path) -> None:
    """Unassigned is loud, never defaulted to some role's policy."""
    members = complete_members("fixture")
    roster = [name for name in members if name != PromptKey.FIX.value]
    write_set(
        tmp_path,
        "fixture",
        members,
        skills={},
        extra_toml=(
            "[session_roles.generative]\n"
            'effort = "xhigh"\n'
            "skills = []\n"
            f"keys = {roster!r}\n"
        ),
    )

    with pytest.raises(PromptResolutionError) as excinfo:
        load_registry(sets_root=tmp_path, default_set="fixture")
    assert PromptKey.FIX.value in excinfo.value.failing_keys


def test_a_set_declaring_both_mechanisms_is_refused(tmp_path: Path) -> None:
    """One loadout, one source: two tables can disagree and one cannot."""
    with pytest.raises(ValueError, match="one source"):
        PromptSetMetadata.model_validate(
            {
                "name": "two-sources",
                "engines": [],
                "skills": {"fix": []},
                "session_roles": {
                    "generative": {"effort": "high", "skills": [], "keys": ["fix"]},
                },
                "fragments": {"skills_reference_header": "header"},
            },
        )


def test_a_utility_roster_disagreeing_with_the_utility_role_is_refused() -> None:
    """The roster is declared once; the model refuses a second answer."""
    with pytest.raises(ValueError, match="utility roster"):
        PromptSetMetadata.model_validate(
            {
                "name": "disagreeing",
                "engines": [],
                "utility_keys": ["branch_name"],
                "session_roles": {
                    "utility": {
                        "effort": "low",
                        "skills": [],
                        "keys": ["commit_message"],
                    },
                },
                "fragments": {"skills_reference_header": "header"},
            },
        )


# ---------------------------------------------------------------------------
# AC-5 — the loadout a key resolves to is its role's
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", list(PromptKey))
def test_each_key_resolves_to_its_roles_declared_loadout(key: PromptKey) -> None:
    """A skill declared for another role never appears on this key."""
    metadata = v5_metadata()
    role = metadata.role_of(key.value)
    registry = load_registry(default_set=V5_SET)

    assert role is not None
    assert list(registry.declared_skills(key)) == metadata.session_roles[role].skills


def test_the_declared_loadouts_reach_the_rendered_prompt() -> None:
    """The loadout is not merely declared: the render names it."""
    registry = load_registry(default_set=V5_SET)
    evaluation = registry.template_for(PromptKey.EVALUATION)
    branch_name = registry.template_for(PromptKey.BRANCH_NAME)

    reference = str(evaluation.bindings["skills_reference"])
    for skill in registry.declared_skills(PromptKey.EVALUATION):
        assert skill in reference
    assert str(branch_name.bindings["skills_reference"]) == ""


# ---------------------------------------------------------------------------
# AC-8 (as amended) — the legacy set is untouched by the mechanism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", list(PromptKey))
def test_a_legacy_dispatch_declares_no_effort_and_no_definitions(
    key: PromptKey,
) -> None:
    """The policy machinery is opt-in per set: selecting the legacy set is a no-op."""
    registry = load_registry(default_set=DEFAULT_SET)

    assert registry.session_policy(key).effort is None
    assert registry.session_policy(key).system_prompt_append is None
    assert registry.definitions() == ()


def test_the_legacy_sets_skills_are_unchanged_by_the_role_mechanism() -> None:
    """The amendment's third clause: the landed per-key loadouts stay as authored."""
    registry = load_registry(default_set=DEFAULT_SET)
    metadata = PromptSetMetadata.model_validate(
        __import__("tomllib").loads(
            (default_sets_root() / DEFAULT_SET / "set.toml").read_text(
                encoding="utf-8",
            ),
        ),
    )

    assert metadata.session_roles == {}
    for key in PromptKey:
        assert list(registry.declared_skills(key)) == metadata.skills[key.value]


# ---------------------------------------------------------------------------
# AC-4 — no template of the new set names a skill or when to invoke one
# ---------------------------------------------------------------------------


def test_no_new_set_template_enumerates_skills_or_their_conditions() -> None:
    """Metadata over prose: a skill's own description is its trigger."""
    declared = {
        skill
        for policy in v5_metadata().session_roles.values()
        for skill in policy.skills
    }
    assert declared, "non-vacuity: the set declares at least one skill somewhere"

    offenders = [
        path.stem
        for path in sorted(V5_SET_DIR.glob("*.md"))
        for skill in declared
        if skill in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# AC-6 — the configured fallback engine reaches every dispatch's policy
# ---------------------------------------------------------------------------


def test_the_configured_fallback_engine_reaches_every_keys_policy() -> None:
    """Configured once, carried on the same object the effort rides."""
    configured = load_registry(default_set=V5_SET, fallback_model="fallback-engine")
    unconfigured = load_registry(default_set=V5_SET)

    for key in PromptKey:
        assert configured.session_policy(key).fallback_model == "fallback-engine"
        assert unconfigured.session_policy(key).fallback_model is None


# ---------------------------------------------------------------------------
# AC-7 — no policy literal in the code that dispatches
# ---------------------------------------------------------------------------

SWEPT_TREES = ("chains", "adapters")

#: The one excluded surface, per the fire-time ruling FR-6: the port's
#: translation of the effort enum onto the SDK's accepted literal type.
#: Pinned by module AND by exhaustiveness — a table that must carry all
#: five levels cannot express a choice between them.
TRANSLATION_MODULE = "_agents_mapping.py"


def swept_sources() -> list[tuple[str, str]]:
    """Every module under the swept trees, as (name, text)."""
    root = default_sets_root().parents[1]
    return [
        (path.name, path.read_text(encoding="utf-8"))
        for tree in SWEPT_TREES
        for path in sorted((root / tree).rglob("*.py"))
    ]


def policy_values() -> set[str]:
    """Every effort level, skill name and engine name the sets decide."""
    metadata = v5_metadata()
    skills = {
        skill for policy in metadata.session_roles.values() for skill in policy.skills
    }
    return {effort.value for effort in SessionEffort} | skills | set(metadata.engines)


def test_the_swept_trees_and_the_value_set_are_both_non_empty() -> None:
    """Non-vacuity: an empty sweep or an empty needle set proves nothing."""
    assert len(swept_sources()) > 1
    assert len(policy_values()) > len(tuple(SessionEffort))


def test_no_policy_value_appears_as_a_literal_in_the_dispatching_code() -> None:
    """Effort, skills and engines come from the set — code reads, never decides."""
    offenders = [
        f"{name}: {line.strip()}"
        for name, source in swept_sources()
        if name != TRANSLATION_MODULE
        for line in source.splitlines()
        for value in policy_values()
        if f'"{value}"' in line
    ]
    assert offenders == []


def test_the_excluded_translation_is_exhaustive_over_the_enum() -> None:
    """The exclusion cannot widen: fewer than all five levels fails here."""
    source = next(text for name, text in swept_sources() if name == TRANSLATION_MODULE)
    for effort in SessionEffort:
        assert f'"{effort.value}"' in source

    skills_and_engines = policy_values() - {e.value for e in SessionEffort}
    assert [value for value in skills_and_engines if f'"{value}"' in source] == []


# ---------------------------------------------------------------------------
# KOD-161 — the deployment's per-key engine table reaches the policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("set_name", [V5_SET, DEFAULT_SET])
def test_a_pinned_keys_policy_carries_its_engine_and_no_other_keys_does(
    set_name: str,
) -> None:
    """Registry-level injection, so both sets serve the table identically —
    the legacy set needs no roles for a deployment to pin its keys."""
    registry = load_registry(
        default_set=set_name,
        session_models={"implementation": "engine-a", "fix": "engine-b"},
    )

    assert registry.session_policy(PromptKey.IMPLEMENTATION).model == "engine-a"
    assert registry.session_policy(PromptKey.FIX).model == "engine-b"
    for key in PromptKey:
        if key not in (PromptKey.IMPLEMENTATION, PromptKey.FIX):
            assert registry.session_policy(key).model is None


def test_an_empty_table_is_byte_identical_to_before_it_existed() -> None:
    """The shipped default: no key pinned, every policy's model is None
    and map_model falls through to the construction model as always."""
    registry = load_registry(default_set=V5_SET)

    for key in PromptKey:
        assert registry.session_policy(key).model is None
