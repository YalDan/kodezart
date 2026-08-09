"""Boot validation: one bad mapping aborts startup naming exactly that entry."""

import pytest

from kodezart.core.errors import TrackerBootValidationError
from kodezart.services.tracker_boot import (
    configured_mappings,
    validate_tracker_mappings,
)
from kodezart.types.domain.operation import (
    CheckStep,
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
from kodezart.types.domain.tracker import MappingKind, MappingRef
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
                check_commands=[CheckStep(name="check", command="make check")],
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
