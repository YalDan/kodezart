"""Boot validation: one bad mapping aborts startup naming exactly that entry."""

import pytest

from kodezart.core.errors import TrackerBootValidationError
from kodezart.services.tracker_boot import (
    OWNED_REF_BUILDERS,
    configured_mappings,
    owned_mappings,
    validate_tracker_mappings,
)
from kodezart.types.domain.operation import (
    FIELD_OWNERSHIP,
    CheckStep,
    ConfigOwnership,
    DocumentEntry,
    DocumentSystem,
    Initiative,
    LifecycleStage,
    OperationConfig,
    Principal,
    PrincipalRole,
    RecordDestination,
    RepoEntry,
)
from kodezart.types.domain.tracker import (
    INSTATABLE_MAPPING_KINDS,
    MappingKind,
    MappingRef,
)
from tests.fakes import FakeTrackerPort
from tests.tracker.conftest import (
    APPROVER,
    BYSTANDER,
    QUEUE_STATE_LABELS,
    TEAM_IDENTIFIERS,
    WORKFLOW_STATE_NAMES,
    fixture_server,
    linear_over_fake_mcp,
)


def operation_config() -> OperationConfig:
    """A structurally valid config naming only entities the fixture resolves."""
    return OperationConfig(
        operation_name="fixture",
        workspace="fixture-workspace",
        principals=[
            Principal(
                tracker_user=APPROVER,
                roles=frozenset(
                    {
                        PrincipalRole.APPROVER,
                        PrincipalRole.PRINCIPAL,
                        PrincipalRole.ASSIGNEE,
                    },
                ),
                handle="@approver",
            ),
            Principal(
                tracker_user=BYSTANDER,
                roles=frozenset({PrincipalRole.PRINCIPAL}),
                handle="@bystander",
            ),
        ],
        agent_identities=[],
        teams=dict(TEAM_IDENTIFIERS),
        queue_states=dict(QUEUE_STATE_LABELS),
        workflow_states=dict(WORKFLOW_STATE_NAMES),
        repos=[
            RepoEntry(
                url="https://example.invalid/repo",
                trunk="main",
                checks=[CheckStep(name="check", command="make check")],
            )
        ],
        documents={
            "checkpoint": DocumentEntry(
                system=DocumentSystem.TRACKER,
                id="doc-1",
            ),
        },
        records={
            "run_log": RecordDestination(
                system=DocumentSystem.KNOWLEDGE,
                id="record-1",
                append_only=True,
            ),
        },
        knowledge={},
        endpoints={},
        initiatives=[Initiative(id="init-1")],
    )


class TestConfiguredMappings:
    """Every configured identity, team and state becomes a ref."""

    def test_every_category_is_covered(self) -> None:
        refs = configured_mappings(operation_config())
        kinds = {ref.kind for ref in refs}
        assert kinds == set(MappingKind)

    def test_each_principal_contributes_a_user_ref(self) -> None:
        refs = configured_mappings(operation_config())
        users = {ref.identifier for ref in refs if ref.kind is MappingKind.USER}
        assert users == {APPROVER, BYSTANDER}

    def test_the_ref_order_is_stable_across_calls(self) -> None:
        assert configured_mappings(operation_config()) == configured_mappings(
            operation_config(),
        )


class TestBootValidation:
    """Resolution against the workspace, fail-loud."""

    async def test_a_fully_resolvable_config_boots(self) -> None:
        tracker = linear_over_fake_mcp(fixture_server())
        await validate_tracker_mappings(
            tracker=tracker,
            config=operation_config(),
        )

    async def test_one_bad_state_mapping_aborts_naming_exactly_that_entry(
        self,
    ) -> None:
        """AC: a config with one bad mapping -> typed error listing that entry."""
        config = operation_config().model_copy(
            update={
                "queue_states": {
                    **QUEUE_STATE_LABELS,
                    "approved": "queue:does-not-exist",
                },
            },
        )
        tracker = linear_over_fake_mcp(fixture_server())
        with pytest.raises(TrackerBootValidationError) as caught:
            await validate_tracker_mappings(tracker=tracker, config=config)
        assert caught.value.unresolved == (
            "queue_state 'approved' -> 'queue:does-not-exist'",
        )

    async def test_one_bad_identity_mapping_aborts_naming_exactly_that_entry(
        self,
    ) -> None:
        config = operation_config().model_copy(
            update={
                "principals": [
                    Principal(
                        tracker_user="ghost",
                        roles=frozenset(
                            {
                                PrincipalRole.APPROVER,
                                PrincipalRole.PRINCIPAL,
                                PrincipalRole.ASSIGNEE,
                            },
                        ),
                        handle="@approver",
                    ),
                    Principal(
                        tracker_user=BYSTANDER,
                        roles=frozenset({PrincipalRole.PRINCIPAL}),
                        handle="@bystander",
                    ),
                ],
            },
        )
        tracker = linear_over_fake_mcp(fixture_server())
        with pytest.raises(TrackerBootValidationError) as caught:
            await validate_tracker_mappings(tracker=tracker, config=config)
        assert caught.value.unresolved == (
            "user 'approver+assignee+principal' -> 'ghost'",
        )

    async def test_one_bad_team_mapping_aborts_naming_exactly_that_entry(
        self,
    ) -> None:
        config = operation_config().model_copy(
            update={"teams": {"engineering": "no-such-team"}},
        )
        tracker = linear_over_fake_mcp(fixture_server())
        with pytest.raises(TrackerBootValidationError) as caught:
            await validate_tracker_mappings(tracker=tracker, config=config)
        assert caught.value.unresolved == ("team 'engineering' -> 'no-such-team'",)

    async def test_every_failure_is_named_at_once_not_the_first(self) -> None:
        config = operation_config().model_copy(
            update={
                "teams": {"engineering": "no-such-team"},
                "workflow_states": {
                    **WORKFLOW_STATE_NAMES,
                    LifecycleStage.DONE: "Shipped",
                },
            },
        )
        tracker = linear_over_fake_mcp(fixture_server())
        with pytest.raises(TrackerBootValidationError) as caught:
            await validate_tracker_mappings(tracker=tracker, config=config)
        assert caught.value.unresolved == (
            "team 'engineering' -> 'no-such-team'",
            "workflow_state 'done' -> 'Shipped'",
        )

    async def test_validation_is_tracker_agnostic(self) -> None:
        """The same check runs over any port implementation."""
        bad = MappingRef(
            kind=MappingKind.TEAM,
            name="engineering",
            identifier=TEAM_IDENTIFIERS["engineering"],
        )
        # The double resolves against what its workspace KNOWS, exactly as a
        # real one does, so the fixture names everything the config declares
        # except the team identifier under test.
        resolvable = [
            APPROVER,
            BYSTANDER,
            *QUEUE_STATE_LABELS.values(),
            *WORKFLOW_STATE_NAMES.values(),
        ]
        with pytest.raises(TrackerBootValidationError) as caught:
            await validate_tracker_mappings(
                tracker=FakeTrackerPort(known_identifiers=resolvable),
                config=operation_config(),
            )
        assert caught.value.unresolved == (bad.describe(),)


class TestOwnershipPartition:
    """The partition is total, and boot READS it rather than restating it."""

    def test_every_declared_field_carries_an_ownership_class(self) -> None:
        """Derived from ``model_fields``, never from a hand-written list.

        A hand-written list is checked against itself: a field added to the
        model and forgotten here would be absent from both sides and the
        assertion would pass.
        """
        assert set(FIELD_OWNERSHIP) == set(OperationConfig.model_fields)

    def test_the_owned_set_is_what_boot_instates(self) -> None:
        """The model decides; this is not a second opinion held elsewhere.

        `owned_mappings` previously named `queue_states` directly and read
        the partition not at all — so promoting a field to OWNED changed
        nothing at boot, and the declaration was decoration.
        """
        owned = {
            field
            for field, ownership in FIELD_OWNERSHIP.items()
            if ownership is ConfigOwnership.OWNED
        }
        assert owned == set(OWNED_REF_BUILDERS)

        config = operation_config()
        assert {ref.name for ref in owned_mappings(config)} == set(config.queue_states)

    def test_every_owned_ref_is_of_a_kind_an_ensure_may_instate(self) -> None:
        """The two halves of instatability cannot drift apart silently.

        ``OWNED_REF_BUILDERS`` says which FIELDS boot instates;
        ``INSTATABLE_MAPPING_KINDS`` says which KINDS an ensure may create,
        and every port refuses the rest.  A builder emitting a kind outside
        that set would produce refs boot itself makes and every adapter
        rejects — a config that cannot boot for a reason no config file
        names.
        """
        kinds = {ref.kind for ref in owned_mappings(operation_config())}

        assert kinds
        assert kinds <= INSTATABLE_MAPPING_KINDS

    def test_an_owned_field_boot_cannot_instate_fails_loudly(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The promotion is what turns the ensure on — or stops the boot.

        Written as a failing case rather than a comment because the whole
        point of reading the partition is that a field promoted to OWNED
        with no ensure behind it must not boot into quietly owning nothing.
        This is the state `documents` is in today, held one step away by its
        EXTERNAL classification alone.
        """
        monkeypatch.setitem(FIELD_OWNERSHIP, "documents", ConfigOwnership.OWNED)

        with pytest.raises(TrackerBootValidationError) as caught:
            owned_mappings(operation_config())
        assert caught.value.unresolved == ("documents",)
