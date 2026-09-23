"""The session passes' team roster names the per-issue teams only (KOD-846).

The two per-issue session passes are the only templates that enumerate the
team roster, so binding the roster over the teams no scope walks tells exactly
those sessions about exactly those boards. With no scope row the binding is
what it always was.
"""

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.composition.passes import prompt_pass_schedule
from kodezart.config.app import AppConfig
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.core.prompt_rendering import binding_names
from kodezart.types.domain.prompts import PromptKey
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_prompt_passes import standing_scope_operation

#: The bindings the per-team narrowing changes.
ROSTER_BINDINGS = frozenset({"teams", "recorded_routing"})

#: The sentence a claude-opus session template adds to each sweep that reaches
#: past the roster when a scope walks some team.
SCOPE_WALK_BOUND = (
    "an issue on a team a scope walks is not this pass's to triage, rewrite, "
    "groom or answer"
)


def test_the_team_roster_binds_only_the_per_issue_teams():
    operation = standing_scope_operation()
    assert operation.scope_walked_teams() == ("primary",)

    bindings = operation_bindings(operation)

    teams = bindings["teams"]
    assert isinstance(teams, list)
    assert [(team["name"], team["key"]) for team in teams] == [
        ("example-agent-team", "EXG")
    ]
    assert bindings["teams_absent"] is None
    # Several repositories are still declared, so an unbound per-issue team
    # would still render as recorded: the repository roster is not narrowed.
    assert bindings["recorded_routing_absent"] is True


def test_with_no_scope_row_the_roster_binds_exactly_as_before():
    operation = standing_scope_operation(scopes=False)
    several_repos = len(operation.repos) > 1

    bindings = operation_bindings(operation)

    assert bindings["teams"] == [
        {
            "name": entry.name,
            "key": entry.key,
            "repository": entry.repository,
            "repository_absent": (
                True if entry.repository is None and not several_repos else None
            ),
            "repository_recorded": (
                True if entry.repository is None and several_repos else None
            ),
            "scope": ", ".join(entry.scope) if entry.scope else None,
            "scope_absent": None if entry.scope else True,
        }
        for entry in operation.teams.values()
    ]


def test_a_walked_team_binds_the_sweep_bound():
    assert operation_bindings(standing_scope_operation())["scope_walks"] is True


def test_with_no_scope_row_the_sweep_bound_is_absent_and_renders_nothing():
    operation = standing_scope_operation(scopes=False)
    bindings = operation_bindings(operation)
    assert bindings["scope_walks_absent"] is True
    prompts = load_registry(default_set="claude-opus", bindings=bindings)

    for key in (PromptKey.FIRE_PREP_PASS, PromptKey.GROOMING_PASS):
        rendered = prompts.template_for(key).render({"record_title": "a run"})
        assert SCOPE_WALK_BOUND not in rendered, key
        assert "scope walks" not in rendered, key


def test_only_the_per_issue_session_templates_read_the_team_roster():
    """Derived from the shipped sets and the scheduled pass table, never listed."""
    session_keys = {
        key.value for key in prompt_pass_schedule(AppConfig(_env_file=None))
    }
    readers = {
        path.relative_to(default_sets_root())
        for path in default_sets_root().rglob("*.md")
        if {name.split(".")[0] for name in binding_names(path.read_text())}
        & ROSTER_BINDINGS
    }

    assert readers, "no template reads the roster, so this guards nothing"
    assert {path.stem for path in readers} <= session_keys, readers
