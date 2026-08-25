"""Boot validation: one bad mapping aborts startup naming exactly that entry."""

from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from kodezart.adapters.in_repo_prompt_registry import (
    InRepoPromptRegistry,
    default_sets_root,
)
from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.core.errors import PromptRenderError, TrackerBootValidationError
from kodezart.core.prompt_namespaces import bindings_for
from kodezart.core.protocols import TrackerPort
from kodezart.services.tracker_boot import (
    OWNED_REF_BUILDERS,
    configured_mappings,
    owned_mappings,
    reconcile_tracker_mappings,
    validate_tracker_mappings,
)
from kodezart.types.domain.operation import (
    CHECKPOINT_DOCUMENT_KEY,
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
    TeamEntry,
)
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.ticket_review import TicketReviewMode
from kodezart.types.domain.tracker import (
    INSTATABLE_MAPPING_KINDS,
    MappingKind,
    MappingRef,
)
from tests.fakes import FakeLinearMcpServer, FakeTrackerPort
from tests.prompt_census import configured_investigation_cap
from tests.tracker.conftest import (
    APPROVER,
    BYSTANDER,
    QUEUE_STATE_LABELS,
    STATE_TYPES,
    TEAM_IDENTIFIERS,
    WORKFLOW_STATE_NAMES,
    fixture_server,
    linear_over_fake_mcp,
)

EXAMPLE_CONFIG = Path(__file__).resolve().parents[2] / "docs" / "operation.example.toml"


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
        teams={
            team_key: TeamEntry(name=team_name, key="ENG")
            for team_key, team_name in TEAM_IDENTIFIERS.items()
        },
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
                name="checkpoint",
                id="doc-1",
            ),
        },
        records={
            "run_log": RecordDestination(
                system=DocumentSystem.KNOWLEDGE,
                name="Run log",
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
        """Across both passes, because the two carry different categories.

        A document is INSTATED and never resolved: the ensure reports the
        identifier the workspace holds, so a resolution pass over the same
        value would re-ask the tool that just answered. Asserting over the
        union keeps the check derived from ``MappingKind`` — a new member
        reddens this until some pass carries it.
        """
        config = operation_config()
        refs = (*configured_mappings(config), *owned_mappings(config))

        assert {ref.kind for ref in refs} == set(MappingKind)
        assert MappingKind.DOCUMENT not in {
            ref.kind for ref in configured_mappings(config)
        }

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
            update={
                "teams": {
                    "engineering": TeamEntry(name="no-such-team", key="ENG"),
                },
            },
        )
        tracker = linear_over_fake_mcp(fixture_server())
        with pytest.raises(TrackerBootValidationError) as caught:
            await validate_tracker_mappings(tracker=tracker, config=config)
        assert caught.value.unresolved == ("team 'engineering' -> 'no-such-team'",)

    async def test_every_failure_is_named_at_once_not_the_first(self) -> None:
        config = operation_config().model_copy(
            update={
                "teams": {"engineering": TeamEntry(name="no-such-team", key="ENG")},
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
        assert {ref.name for ref in owned_mappings(config)} == set(
            config.queue_states,
        ) | {
            entry.name
            for entry in config.documents.values()
            if entry.system is DocumentSystem.TRACKER
        }

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
        `records` is the field in that position now: EXTERNAL, no builder,
        and one line away from a boot that owns a destination it cannot
        instate.
        """
        monkeypatch.setitem(FIELD_OWNERSHIP, "records", ConfigOwnership.OWNED)

        with pytest.raises(TrackerBootValidationError) as caught:
            owned_mappings(operation_config())
        assert caught.value.unresolved == ("records",)


class TestReconciledConfig:
    """The config reconciliation leaves behind, and why boot must bind to it.

    KOD-57 R9.4.  An adopted document id exists only in an outcome until it
    is written back, so this is the seam between "boot created the
    document" and "every later reader knows which document that is".  The
    shipped example is the subject rather than a hand-built fixture,
    because what has to keep working is the file an operator copies.
    """

    def _fresh(self) -> OperationConfig:
        """The example, in the shape a fresh workspace is configured with."""
        config = load_operation_config(EXAMPLE_CONFIG)
        checkpoint = config.documents[CHECKPOINT_DOCUMENT_KEY]
        return config.model_copy(
            update={
                "documents": {
                    **config.documents,
                    CHECKPOINT_DOCUMENT_KEY: checkpoint.model_copy(
                        update={"id": None},
                    ),
                },
            },
        )

    def _workspace(self, config: OperationConfig) -> FakeTrackerPort:
        """A workspace holding every external entity and no documents."""
        return FakeTrackerPort(
            known_identifiers=[
                *(principal.tracker_user for principal in config.principals),
                *config.agent_identities,
                *(entry.name for entry in config.teams.values()),
                *config.queue_states.values(),
                *config.workflow_states.values(),
            ],
        )

    async def test_the_adopted_document_id_is_written_back_into_the_config(
        self,
    ) -> None:
        config = self._fresh()
        tracker = self._workspace(config)

        reconciliation = await reconcile_tracker_mappings(
            tracker=tracker,
            config=config,
        )

        adopted = reconciliation.config.documents[CHECKPOINT_DOCUMENT_KEY].id
        assert adopted is not None
        assert adopted in tracker.document_titles
        # Nothing else moved: the knowledge document declares its own id and
        # no ensure touches it.
        assert (
            reconciliation.config.documents["house_rules"]
            == (config.documents["house_rules"])
        )

    def test_a_registry_bound_before_reconciliation_cannot_render_a_pass(
        self,
    ) -> None:
        """The ordering has a consequence, and this is it, stated as a failure.

        Binding the declared copy is not merely untidy: the placeholder has
        no value, so every pass prompt naming the checkpoint document
        fails to render at all, and a scheduled tick becomes a typed
        rendering failure once per interval.
        """
        registry = InRepoPromptRegistry.load(
            sets_root=default_sets_root(),
            default_set="claude-opus",
            set_overrides={},
            template_overrides={},
            bindings=dict(bindings_for(self._fresh())),
            investigation_cap=configured_investigation_cap(),
            ticket_review_mode=TicketReviewMode.REVIEWED,
        )

        with pytest.raises(PromptRenderError) as caught:
            registry.template_for(PromptKey.FIRE_PREP_PASS).render({})

        assert "documents.checkpoint.id" in caught.value.missing

    async def test_a_registry_bound_after_reconciliation_names_the_adopted_id(
        self,
    ) -> None:
        """The paired positive: the same render, from the reconciled copy."""
        config = self._fresh()
        reconciliation = await reconcile_tracker_mappings(
            tracker=self._workspace(config),
            config=config,
        )
        registry = InRepoPromptRegistry.load(
            sets_root=default_sets_root(),
            default_set="claude-opus",
            set_overrides={},
            template_overrides={},
            bindings=dict(bindings_for(reconciliation.config)),
            investigation_cap=configured_investigation_cap(),
            ticket_review_mode=TicketReviewMode.REVIEWED,
        )

        rendered = registry.template_for(PromptKey.FIRE_PREP_PASS).render({})

        adopted = reconciliation.config.documents[CHECKPOINT_DOCUMENT_KEY].id
        assert adopted is not None
        assert adopted in rendered


class TestWorkflowStatesResolvePerTeam:
    """The status vocabulary is read per declared team, all-must-resolve.

    Ruled on KOD-143, 2026-08-25: ``list_issue_statuses`` takes a team and
    answers for that team alone, so an operation declaring several teams
    has several vocabularies and boot has to say what it means by "the
    workspace resolves this state".  It means every declared team resolves
    it — the declared states are a WRITE contract, and the lifecycle
    writer sets them on whichever declared team's issue was dispatched, so
    a team that cannot express one is unsound exactly there.  Union
    semantics would convert that unsoundness into a runtime failure on a
    live issue instead of a refusal at boot.
    """

    SECOND_TEAM = "fixture-second-team"
    DECLARED_TEAMS: ClassVar[dict[str, str]] = {
        "engineering": "fixture-team",
        "platform": SECOND_TEAM,
    }

    def _server(self, second: Sequence[str]) -> FakeLinearMcpServer:
        """A workspace of two boards, the second offering *second* only."""
        return FakeLinearMcpServer(
            users=[APPROVER, BYSTANDER],
            teams=["fixture-team", self.SECOND_TEAM],
            labels=list(QUEUE_STATE_LABELS.values()),
            statuses={
                "fixture-team": list(STATE_TYPES),
                self.SECOND_TEAM: list(second),
            },
            state_types=STATE_TYPES,
            actor=APPROVER,
        )

    def _tracker(self, server: FakeLinearMcpServer) -> TrackerPort:
        return LinearMcpTracker(
            caller=server,
            queue_state_labels=QUEUE_STATE_LABELS,
            workflow_state_names=WORKFLOW_STATE_NAMES,
            team_identifiers=dict(self.DECLARED_TEAMS),
            max_retries=0,
            retry_backoff_factor=1.0,
        )

    def _config(self) -> OperationConfig:
        return operation_config().model_copy(
            update={
                "teams": {
                    key: TeamEntry(name=name, key=key[:3].upper())
                    for key, name in self.DECLARED_TEAMS.items()
                },
            },
        )

    def _refs(self) -> list[MappingRef]:
        return [
            MappingRef(
                kind=MappingKind.WORKFLOW_STATE,
                name=stage.value,
                identifier=identifier,
            )
            for stage, identifier in WORKFLOW_STATE_NAMES.items()
        ]

    async def test_the_tool_is_called_once_per_declared_team_naming_it(self) -> None:
        """The argument the schema requires, sent once for each board."""
        server = self._server(STATE_TYPES)
        await self._tracker(server).resolve_mappings(refs=self._refs())

        assert server.tool_calls("list_issue_statuses") == [
            {"team": self.SECOND_TEAM},
            {"team": "fixture-team"},
        ]

    async def test_a_vocabulary_both_boards_share_resolves(self) -> None:
        """The passing case: every declared state on every declared team."""
        tracker = self._tracker(self._server(STATE_TYPES))
        assert await tracker.resolve_mappings(refs=self._refs()) == ()

    async def test_a_state_one_board_lacks_refuses_naming_the_team_and_state(
        self,
    ) -> None:
        """The refusal case, at boot, through the shipped validation path."""
        without_review = [name for name in STATE_TYPES if name != "In Review"]
        tracker = self._tracker(self._server(without_review))

        with pytest.raises(TrackerBootValidationError) as caught:
            await validate_tracker_mappings(tracker=tracker, config=self._config())

        assert caught.value.unresolved == (
            f"workflow_state 'in_review' -> 'In Review' on team "
            f"'{self.SECOND_TEAM}'",
        )

    async def test_the_two_vocabularies_are_never_merged(self) -> None:
        """Each board holds half the states; their union holds all of them.

        A merged read would resolve every declared state and boot clean,
        leaving both boards unable to express half the lifecycle.
        """
        server = self._server(["In Progress", "Done"])
        server.statuses["fixture-team"] = ["In Progress", "In Review"]
        tracker = self._tracker(server)

        with pytest.raises(TrackerBootValidationError) as caught:
            await tracker.resolve_mappings(refs=self._refs())

        assert caught.value.unresolved == (
            f"workflow_state 'in_review' -> 'In Review' on team "
            f"'{self.SECOND_TEAM}'",
            "workflow_state 'done' -> 'Done' on team 'fixture-team'",
        )

    async def test_a_state_no_board_holds_is_reported_rather_than_refused(
        self,
    ) -> None:
        """No team to name, so it is the ordinary unresolved entry.

        The distinction is real: "this state is missing from the whole
        workspace" is a typo in the config, and "these two boards disagree
        about it" is a workspace the operation cannot run on.  Both stop
        boot; only the second one has a team to point at.
        """
        tracker = self._tracker(self._server(STATE_TYPES))
        nowhere = MappingRef(
            kind=MappingKind.WORKFLOW_STATE,
            name="done",
            identifier="Shipped",
        )

        assert await tracker.resolve_mappings(refs=[nowhere]) == (nowhere,)
