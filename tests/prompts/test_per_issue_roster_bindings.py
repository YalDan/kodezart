"""The session passes' team roster names every declared team, scopes or not.

Grooming and fire prep run for every team whether or not a scope row is
declared: they prepare the board for scopes to be approved, and the scope walk
builds what is approved. So a scope row narrows nothing either session is told,
and with no scope row the roster binds what it always did.
"""

from kodezart.adapters.in_repo_prompt_registry import default_sets_root
from kodezart.composition.passes import prompt_pass_schedule
from kodezart.config.app import AppConfig
from kodezart.core.prompt_namespaces import operation_bindings
from kodezart.types.domain.ticket_review import TicketReviewMode
from tests.prompts.test_prompt_wiring import load_registry
from tests.services.test_prompt_passes import standing_scope_operation

#: What a session render is given besides the operation's own bindings.
RENDER_VARIABLES = {"record_title": "a run", "skills_reference": ""}


def test_a_scope_deployment_binds_every_declared_team_in_the_roster():
    operation = standing_scope_operation()
    assert operation.scope_walked_teams() == ("primary",)

    bindings = operation_bindings(operation)

    teams = bindings["teams"]
    assert isinstance(teams, list)
    assert [(team["name"], team["key"]) for team in teams] == [
        ("Example Team", "EXA"),
        ("example-agent-team", "EXG"),
    ]
    assert bindings["teams_absent"] is None


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


def test_a_scope_row_changes_nothing_either_session_pass_is_told():
    """Derived from the shipped sets and the scheduled pass table, never listed.

    Each scheduled session key of each shipped set is rendered as the registry
    composes it, set fragments included, in both ticket review modes, over one
    operation with its scope row and without it. The two renders are the same
    text, and each names every declared team's board.
    """
    walked = standing_scope_operation()
    unwalked = standing_scope_operation(scopes=False)
    assert walked.organize_scopes
    assert not unwalked.organize_scopes
    session_keys = tuple(prompt_pass_schedule(AppConfig(_env_file=None)))
    assert session_keys
    sets = [path.name for path in default_sets_root().iterdir() if path.is_dir()]
    assert sets

    compared = 0
    for set_name in sets:
        for mode in TicketReviewMode:
            renders = [
                load_registry(
                    default_set=set_name,
                    ticket_review_mode=mode,
                    bindings=operation_bindings(operation),
                )
                for operation in (walked, unwalked)
            ]
            for key in session_keys:
                scoped, plain = (
                    registry.template_for(key).render(RENDER_VARIABLES)
                    for registry in renders
                )
                assert scoped == plain, (set_name, mode.value, key.value)
                for entry in walked.teams.values():
                    assert entry.key in scoped, (set_name, key.value, entry.key)
                compared += 1

    assert compared == len(sets) * len(TicketReviewMode) * len(session_keys)
