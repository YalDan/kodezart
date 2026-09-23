"""One declared scope roster serves the organize tick, the audit and the
observation tick, and the keys it replaced are refused at load (KOD-885).

The composition cases go through the real factory rather than the three
builders, because what the criterion is about is which table a BOOT reads: a
builder handed a roster by a test proves only that the builder can read one.
"""

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition import audit as audit_composition
from kodezart.composition import organize as organize_composition
from kodezart.composition import supervisor as supervisor_composition
from kodezart.core.errors import OperationConfigError
from kodezart.types.domain.operation import OperationConfig, OrganizeScopeBinding
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from tests.integration.test_audit_scheduler import dependencies, schedule_over
from tests.prompts.test_operation_config import raw_example, write_toml
from tests.tracker.test_scope_reads import CHILDLESS, EMPTY_INITIATIVE

#: A scope neither declared row names. The dialled tracker's reconciled copy
#: declares this one and nothing else, so an arm reading that copy instead of
#: the one handed in records it and fails the comparison below by content
#: rather than by count.
STRAY = ScopeRef(kind=ScopeKind.PROJECT, key="stray-project")

#: Per pass: the module the composition calls its constructor from, the name it
#: calls it by, and the name the scheduler registers the pass under. Patched at
#: the call site rather than at the definition, because the question is what
#: THIS composition hands over.
COMPOSED = {
    "organize": (organize_composition, "OrganizeTarget", "organize_pass"),
    "audit": (audit_composition, "AuditTarget", "audit"),
    "supervisor": (supervisor_composition, "SupervisorPass", "supervisor"),
}


def two_scope_deployment():
    """The audit module's own deployment, with a second declared scope.

    Two rows, because one row cannot tell a pass composed from the roster from
    a pass composed from the first row it found. The second row carries its own
    report destination, so comparing whole rows is comparing something: a
    roster that let one scope's destination stand in for another's would pass a
    scope-only comparison.
    """
    config, operation, server, tracker, forge = dependencies()
    fields = operation.model_dump()
    fields["organize_scopes"] = [
        *fields["organize_scopes"],
        {
            **fields["organize_scopes"][0],
            "scope": EMPTY_INITIATIVE.model_dump(),
            "report_issue_key": CHILDLESS.key,
        },
    ]
    return config, OperationConfig.model_validate(fields), server, tracker, forge


@pytest.mark.parametrize("pass_name", list(COMPOSED))
async def test_each_pass_is_composed_from_the_one_roster(monkeypatch, pass_name):
    """The registered pass holds exactly the declared rows, for each of the three.

    Recorded at the constructor the composition reaches for, so what is checked
    is the handover and not a re-derivation of it: a wrapper that recorded
    nothing leaves an empty list, which is not two rows, so a case cannot pass
    by observing nothing. No tick is run and nothing walks — the claim is about
    what a boot composes, and a pass that reads the roster correctly and then
    does the wrong thing with a scope is another criterion's business.
    """
    config, operation, server, tracker, forge = two_scope_deployment()
    declared = list(operation.organize_scopes)
    assert len(declared) == 2
    assert STRAY not in [row.scope for row in declared]

    module, attribute, registered_as = COMPOSED[pass_name]
    real = getattr(module, attribute)
    calls: list[dict[str, object]] = []

    def recording(**kwargs: object) -> object:
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(module, attribute, recording)
    registered, _executor = await schedule_over(
        config,
        operation,
        server,
        tracker,
        forge,
        reconciled=operation.model_copy(
            update={
                "organize_scopes": (
                    OrganizeScopeBinding(scope=STRAY, repo_url=operation.repos[0].url),
                )
            }
        ),
    )

    assert registered_as in {entry.name for entry in registered}
    if pass_name == "supervisor":
        # One construction for the whole roster, projected to bare scope refs:
        # the tick reads tracker state, so the repository and the report
        # destination beside each scope are no part of what it is handed.
        assert [call["scopes"] for call in calls] == [
            tuple(row.scope for row in declared)
        ]
    else:
        # One construction per row, and the WHOLE row each time, in the order
        # the table declares it.
        assert [call["binding"] for call in calls] == declared
        if pass_name == "audit":
            # The destination is a sibling keyword of the row, not a member of
            # it, so the comparison above does not reach it: a roster where one
            # scope's report destination stood in for another's would compose a
            # pass that reports scope B's verified summary onto scope A's issue.
            assert [call["report_issue_key"] for call in calls] == [
                row.report_issue_key for row in declared
            ]
    # Composition only: the boot opened no session and wrote nothing.
    assert server.comments == []


#: The repository the shipped example declares, so a retired audit row below
#: names something real. Nothing reads it: the key is rejected before any row is
#: looked at, which is also the whole of the no-alias claim.
DECLARED_REPO = str(raw_example()["repos"][0]["url"])

#: Each retired key, in the two shapes a deployment carries it in: rows, and
#: the empty table an operator leaves behind after deleting them. The ids are
#: written out rather than derived, so emptying the loader's mapping reddens
#: this rather than collecting nothing.
#: No row carries a nested ``scope`` table, because the refusal keys on the
#: key's presence and not on any row's contents.
RETIRED_PAYLOADS: dict[str, tuple[str, list[dict[str, str]]]] = {
    "audit_scopes-rows": (
        "audit_scopes",
        [{"repo_url": DECLARED_REPO, "report_issue_key": "X-1"}],
    ),
    "supervisor_scopes-rows": (
        "supervisor_scopes",
        [{"kind": "project", "key": "k"}],
    ),
    "audit_scopes-empty": ("audit_scopes", []),
    "supervisor_scopes-empty": ("supervisor_scopes", []),
}

#: The whole of what the loader says about each retired key, written out: one
#: line, naming the key and the table that replaces it, with no trace of
#: pydantic's own rejection and no default read out of an empty table.
REFUSALS = {
    "audit_scopes": (
        "audit_scopes: retired, declare each audited scope once as an "
        "[[organize_scopes]] row carrying its report_issue_key"
    ),
    "supervisor_scopes": (
        "supervisor_scopes: retired, declare each observed scope once as an "
        "[[organize_scopes]] row"
    ),
}


@pytest.mark.parametrize("case", list(RETIRED_PAYLOADS))
def test_a_retired_scope_key_is_refused_at_load_naming_the_one_table(tmp_path, case):
    """A file that declares a scope twice does not load, and says where to put it.

    Exact tuple equality, because the clause is the whole of it at once: one
    typed failure, that failure naming the surviving table, no "Extra inputs"
    text left over, nothing aliased onto the surviving key, and no default read
    out of an empty table.
    """
    retired, rows = RETIRED_PAYLOADS[case]
    raw = raw_example()
    raw[retired] = rows

    with pytest.raises(OperationConfigError) as excinfo:
        load_operation_config(write_toml(tmp_path, raw))

    expected = REFUSALS[retired]
    assert retired in expected
    assert "[[organize_scopes]]" in expected
    assert excinfo.value.failures == (expected,)
