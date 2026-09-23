"""Which teams a scope walks and which the per-issue flow keeps (KOD-846).

Read off the declared scope rows and the existing binding rule alone: a team
bound to a repository some row names is walked, and every other team is
per-issue. With no row, every team is per-issue, exactly as before.
"""

from kodezart.composition.passes import runs_per_issue_flow
from kodezart.types.domain.operation import OperationConfig
from tests.domain.test_organize import mandate_operation_fields

SCOPED = "https://example.invalid/org/scoped"
OTHER = "https://example.invalid/org/other"


def _team(key: str, repository: str | None) -> dict[str, object]:
    return {"name": f"team {key}", "key": key.upper(), "repository": repository}


def _operation(
    *,
    teams: dict[str, str | None],
    repos: tuple[str, ...],
    scoped: tuple[str, ...],
) -> OperationConfig:
    fields = mandate_operation_fields()
    fields["teams"] = {key: _team(key, repo) for key, repo in teams.items()}
    fields["repos"] = [{"url": url, "trunk": "main"} for url in repos]
    fields["organize_scopes"] = [
        {"scope": {"kind": "issue", "key": f"SC-{index}"}, "repo_url": url}
        for index, url in enumerate(scoped, start=1)
    ]
    if not scoped:
        fields.pop("organize_mandates")
    return OperationConfig.model_validate(fields)


def test_a_team_bound_to_a_repository_a_scope_names_is_walked():
    operation = _operation(
        teams={"A": SCOPED, "B": OTHER}, repos=(SCOPED, OTHER), scoped=(SCOPED,)
    )

    assert operation.scope_walked_teams() == ("A",)
    assert operation.per_issue_teams() == ("B",)
    assert operation.teams_scanned_by(SCOPED) == ()
    assert operation.teams_scanned_by(OTHER) == ("B",)
    # The binding itself is unchanged: A is still bound where it was.
    assert operation.teams_bound_to(SCOPED) == ("A",)


def test_an_unbound_team_beside_several_repositories_stays_per_issue():
    operation = _operation(
        teams={"A": SCOPED, "B": OTHER, "U": None},
        repos=(SCOPED, OTHER),
        scoped=(SCOPED,),
    )

    assert "U" not in operation.scope_walked_teams()
    assert operation.per_issue_teams() == ("B", "U")
    assert operation.teams_scanned_by(SCOPED) == ("U",)
    assert operation.teams_scanned_by(OTHER) == ("B", "U")


def test_one_repository_named_by_a_scope_walks_every_team():
    operation = _operation(
        teams={"A": None, "B": SCOPED}, repos=(SCOPED,), scoped=(SCOPED,)
    )

    assert operation.scope_walked_teams() == ("A", "B")
    assert operation.per_issue_teams() == ()
    assert operation.teams_scanned_by(SCOPED) == ()
    assert runs_per_issue_flow(operation) is False


def test_with_no_scope_row_every_team_is_per_issue_in_declaration_order():
    operation = _operation(
        teams={"B": OTHER, "A": SCOPED, "U": None}, repos=(SCOPED, OTHER), scoped=()
    )

    assert operation.scope_walked_teams() == ()
    assert operation.per_issue_teams() == ("B", "A", "U")
    assert operation.teams_scanned_by(SCOPED) == ("A", "U")
    assert operation.teams_scanned_by(OTHER) == ("B", "U")


def test_the_per_issue_flow_runs_whenever_no_scope_row_is_declared():
    with_teams = _operation(teams={"A": SCOPED}, repos=(SCOPED,), scoped=())
    without_teams = _operation(teams={}, repos=(SCOPED,), scoped=())

    assert runs_per_issue_flow(with_teams) is True
    assert runs_per_issue_flow(without_teams) is True
