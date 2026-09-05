"""The optionality redesign: an empty board boots, consumers refuse at need.

Every collection field defaults empty, because each one's absence is a real
operation.  What an empty collection costs is paid at the point of need — a
typed refusal naming the missing role or key and what stops working — never
at load.  Structural validation applies to what IS present: a populated
registry missing the member code addresses by name is a typo and fails
loudly, an empty one is a decision and loads.
"""

import pytest

from kodezart.types.domain.operation import (
    CHECKPOINT_DOCUMENT_KEY,
    DocumentEntry,
    DocumentSystem,
    OperationConfig,
    OperationMemberAbsentError,
    Principal,
    PrincipalRole,
    RecordDestination,
    RunKind,
)

COLLECTION_FIELDS = (
    "principals",
    "agent_identities",
    "teams",
    "queue_states",
    "workflow_states",
    "repos",
    "documents",
    "records",
    "knowledge",
    "endpoints",
    "initiatives",
)


def minimal_config() -> OperationConfig:
    """The floor: the two required scalars and nothing else."""
    return OperationConfig(
        operation_name="fixture",
        workspace="fixture-workspace",
    )


def _principal(user: str, *roles: PrincipalRole) -> Principal:
    return Principal(
        tracker_user=user,
        roles=frozenset({PrincipalRole.PRINCIPAL, *roles}),
        handle=f"@{user}",
    )


# ---------------------------------------------------------------------------
# An empty board boots
# ---------------------------------------------------------------------------


def test_the_two_scalars_alone_are_a_valid_config() -> None:
    """The defect in one line: this used to be unconstructable."""
    config = minimal_config()
    assert config.operation_name == "fixture"
    assert config.workspace == "fixture-workspace"


@pytest.mark.parametrize("field", COLLECTION_FIELDS)
def test_every_collection_field_defaults_empty(field: str) -> None:
    """Eleven collections, one rule: absence has a workable meaning."""
    value = getattr(minimal_config(), field)
    assert len(value) == 0


def test_the_scalars_are_still_required() -> None:
    """The floor does not extend to fields whose absence describes nothing."""
    with pytest.raises(ValueError, match="operation_name"):
        OperationConfig.model_validate({"workspace": "w"})
    with pytest.raises(ValueError, match="workspace"):
        OperationConfig.model_validate({"operation_name": "o"})


# ---------------------------------------------------------------------------
# Validation applies to what IS present
# ---------------------------------------------------------------------------


def test_declared_principals_without_an_approver_still_fail() -> None:
    """Non-empty principals keep the exactly-one-APPROVER invariant."""
    with pytest.raises(ValueError, match="exactly one APPROVER"):
        OperationConfig(
            operation_name="fixture",
            workspace="fixture-workspace",
            principals=[_principal("user-a")],
        )


def test_two_assignees_are_refused_at_load() -> None:
    """At most one target for prepared work; two is silent mis-routing."""
    with pytest.raises(ValueError, match="at most one ASSIGNEE"):
        OperationConfig(
            operation_name="fixture",
            workspace="fixture-workspace",
            principals=[
                _principal("user-a", PrincipalRole.APPROVER, PrincipalRole.ASSIGNEE),
                _principal("user-b", PrincipalRole.ASSIGNEE),
            ],
        )


def test_an_absent_assignee_loads() -> None:
    """The count is at-most-one, not exactly-one: absence is a legal state."""
    config = OperationConfig(
        operation_name="fixture",
        workspace="fixture-workspace",
        principals=[_principal("user-a", PrincipalRole.APPROVER)],
    )
    assert config.approver().tracker_user == "user-a"


def test_a_populated_queue_mapping_still_requires_the_core() -> None:
    """The five members code addresses by name bind when anything is declared."""
    with pytest.raises(ValueError, match="approved"):
        OperationConfig(
            operation_name="fixture",
            workspace="fixture-workspace",
            queue_states={"triage": "queue:triage"},
        )


def test_a_records_key_outside_the_kind_vocabulary_is_refused() -> None:
    """Record keys are the run kinds; absence is legal, a free name is not.

    The old rule required a stable run_log key; the kind vocabulary
    replaced it (KOD-170) — every declared key must BE a kind, and no
    kind is required.
    """
    with pytest.raises(ValueError, match="is not a run kind"):
        OperationConfig(
            operation_name="fixture",
            workspace="fixture-workspace",
            records={
                "audit": RecordDestination(
                    system=DocumentSystem.KNOWLEDGE,
                    name="Run log",
                    id="destination-1",
                    append_only=True,
                ),
            },
        )


def test_a_populated_documents_registry_still_requires_the_checkpoint_key() -> None:
    with pytest.raises(ValueError, match=CHECKPOINT_DOCUMENT_KEY):
        OperationConfig(
            operation_name="fixture",
            workspace="fixture-workspace",
            documents={
                "house_rules": DocumentEntry(
                    system=DocumentSystem.KNOWLEDGE,
                    name="house rules",
                    id="document-1",
                ),
            },
        )


# ---------------------------------------------------------------------------
# The point-of-need refusals
# ---------------------------------------------------------------------------


def test_the_approver_refusal_names_the_role_and_what_stops() -> None:
    """No StopIteration, no blank: a typed error carrying both facts."""
    with pytest.raises(OperationMemberAbsentError) as excinfo:
        minimal_config().approver()
    assert "APPROVER" in excinfo.value.missing
    assert "dispatched" in excinfo.value.stops
    assert excinfo.value.missing in str(excinfo.value)
    assert excinfo.value.stops in str(excinfo.value)


def test_the_assignee_refusal_names_the_role_and_what_stops() -> None:
    with pytest.raises(OperationMemberAbsentError) as excinfo:
        minimal_config().assignee()
    assert "ASSIGNEE" in excinfo.value.missing
    assert "refuses to run" in excinfo.value.stops


def test_the_accessors_return_the_member_when_it_is_declared() -> None:
    """The present case: each accessor is a reader, not only a refusal."""
    config = OperationConfig(
        operation_name="fixture",
        workspace="fixture-workspace",
        principals=[
            _principal("user-a", PrincipalRole.APPROVER, PrincipalRole.ASSIGNEE),
        ],
        documents={
            CHECKPOINT_DOCUMENT_KEY: DocumentEntry(
                system=DocumentSystem.TRACKER,
                name="checkpoint",
                id="document-1",
            ),
        },
        records={
            RunKind.FIRE_PREP.value: RecordDestination(
                system=DocumentSystem.KNOWLEDGE,
                name="Run log",
                id="destination-1",
                append_only=True,
            ),
        },
    )
    assert config.assignee().tracker_user == "user-a"


def test_the_two_registry_keys_carry_no_point_of_need_accessor() -> None:
    """The registries refuse nowhere on this model, and that is the design.

    An absent checkpoint or run-log entry is answered by the three-state
    render — a bootstrap census, a record-nothing-outside-the-tracker
    instruction — and a declared destination no session can reach is
    refused at boot.  An accessor here would be a third refusal for a
    state the other two already answer, and no production caller had one.
    """
    for name in ("checkpoint_document", "run_log_record"):
        assert not hasattr(minimal_config(), name), name
