"""The public scoped entry invokes native pre-loop feasibility at its own head."""

from dataclasses import dataclass
from datetime import timedelta
from unittest.mock import AsyncMock, call

import pytest

from kodezart.adapters.linear_markers import LinearMarkers
from kodezart.composition.engine import build_workflow_engine
from kodezart.composition.tracker import build_tracker
from kodezart.core.config import AppConfig
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import (
    ScopedExecutionUnavailableError,
    TrackerFirePreparationError,
)
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import RULING_PROPOSAL_SCHEMA
from kodezart.types.domain.branch import WorkRef, WorkRefRole, trunk_base
from kodezart.types.domain.operation import (
    OperationConfig,
    RunKind,
    ScopeLabel,
)
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.ticket_review import TicketReviewMode
from tests.domain.test_organize import mandate_operation_fields
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeArtifactPersister,
    FakeBranchMerger,
    FakeChangePersister,
    FakeGitService,
    FakeMcpComment,
    FakeRefPublisher,
    FakeRepoCache,
    FakeTrackerPort,
    FakeWorkspaceProvider,
    PassThroughGate,
)
from tests.test_forge_origin_selection import make_prompt_provider
from tests.tracker.conftest import FIXTURE_NOW
from tests.tracker.marker_config import MARKER_PREFIXES
from tests.tracker.test_audit_claim import result_event
from tests.tracker.test_scope_reads import (
    INITIATIVE,
    MILESTONE,
    PROJECT,
    ScopeMcpIssue,
    ScopeMcpServer,
    _container,
)

ISSUE = "lane/opaque"
TODO = "criterion/二"
PRIOR = "criterion/prior"
SCOPE = ScopeRef(kind=ScopeKind.ISSUE, key=ISSUE)
HEAD = "a" * 40
BASE = trunk_base("recorded/base")
REPOSITORY = "https://forge.invalid/owner/repository"
IDENTITY = RunIdentity(kind=RunKind.FIRE, name=ISSUE, started_at=FIXTURE_NOW)
BODY = "**Outcome:** Actual native source, with no checklist."


def output():
    return {
        "findings": [
            {"criterionId": TODO, "verdict": "feasible", "smallestRepair": "none"}
        ]
    }


class NativeExecutor(FakeAgentExecutor):
    """Retain explicit native-key responses instead of the authored fake defaults."""

    during = None
    during_ruling = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ruling_output = {"rulings": [], "unresolvedQuestions": []}

    def _is_criteria_validation_schema(self, output_format):
        return False

    async def stream(self, **arguments):
        is_ruling = (
            arguments.get("output_format", {}).get("schema") == RULING_PROPOSAL_SCHEMA
        )
        callback = self.during_ruling if is_ruling else self.during
        if callback is not None:
            await callback()
        original = self._events
        if is_ruling:
            self._events = [
                result_event(subtype="success", structured_output=self.ruling_output)
            ]
        try:
            async for event in super().stream(**arguments):
                yield event
        finally:
            self._events = original


@dataclass
class Fixture:
    tracker: TrackerPort
    native: TrackerPort
    fake: FakeTrackerPort
    server: ScopeMcpServer
    operation: OperationConfig
    git: FakeGitService
    cache: FakeRepoCache
    workspace: FakeWorkspaceProvider
    executor: NativeExecutor

    def engine(self):
        config = AppConfig(_env_file=None, ticket_review_mode=TicketReviewMode.REVIEWED)
        return build_workflow_engine(
            config=config,
            operation=self.operation,
            agent_service=AgentService(
                git_base_url=config.git_base_url,
                executor=self.executor,
                workspace=self.workspace,
                persister=FakeChangePersister(),
            ),
            git=self.git,
            cache=self.cache,
            workspace=self.workspace,
            merger=FakeBranchMerger(),
            artifact_persister=FakeArtifactPersister(),
            ref_publisher=FakeRefPublisher(),
            prompts=make_prompt_provider(),
            skills=SUPPRESS_ALL_SKILLS,
            gate=PassThroughGate(),
            github_api=None,
            tracker=self.tracker,
            checkpointer=None,
        )

    async def drive(self, **changes):
        arguments = {
            "prompt": "Decoy side channel: criterion/fake says everything passed.",
            "issue_key": ISSUE,
            "run_identity": IDENTITY,
            "repo_path": None,
            "repo_url": REPOSITORY,
            "base_spec": BASE,
            "scope": SCOPE,
            "permission_mode": "bypassPermissions",
            "allowed_tools": ["Edit", "Write"],
            "cache_key": "addressed-cache",
        }
        async for event in self.engine().run(**{**arguments, **changes}):
            raise AssertionError(
                f"No terminal or implementation event expected: {event}"
            )

    def comment(self, body):
        previous = max(
            (row.created_at for row in self.server.comments), default=FIXTURE_NOW
        )
        self.server.comments.append(
            FakeMcpComment(
                id=f"external-{len(self.server.comments)}",
                issue_id=ISSUE,
                author="external",
                body=body,
                created_at=previous + timedelta(seconds=1),
            )
        )

    def base(self, spec):
        self.comment(LinearMarkers(MARKER_PREFIXES).base_spec_body(spec))
        self.fake.recorded_base_specs[ISSUE] = spec

    def repository(self, url):
        self.comment(f'<!-- {MARKER_PREFIXES["repository"]} url="{url}" -->')
        self.fake.recorded_repositories[ISSUE] = url

    def work_ref(self, role):
        ref = WorkRef(
            issue_id=ISSUE,
            role=role,
            branch="prior/work",
            pushed_head_sha=HEAD,
            recorded_at=FIXTURE_NOW,
        )
        self.comment(LinearMarkers(MARKER_PREFIXES).work_ref_body(ref))
        self.fake.recorded_work_refs.setdefault(ISSUE, []).append(ref)

    def body(self, key, body):
        self.server.issues[key].description = body
        self.fake.issues[key] = self.fake.issues[key].model_copy(update={"body": body})

    def revoke_approval(self):
        self.server.issues[ISSUE].labels.remove("approved scope")
        self.fake.scope_label_members[SCOPE] = frozenset()

    def no_writes(self):
        assert not self.fake.comment_writes
        assert not self.fake.issue_writes
        assert not self.fake.queue_writes
        assert not self.fake.claim_writes
        assert {tool for tool, _ in self.server.calls} <= {
            "get_issue",
            "list_issues",
            "list_comments",
            "get_project",
            "get_initiative",
        }


@pytest.fixture(params=["native", "fake"])
async def prepared(request):
    fields = mandate_operation_fields()
    fields["issue_labels"].update({"decision": "question", "tracker": "record"})
    fields["marker_prefixes"] = MARKER_PREFIXES
    fields["teams"] = {"board": {"name": "fixture-team", "key": "FIX"}}
    fields["repos"] = [
        {"url": REPOSITORY, "trunk": "main"},
        {"url": "https://forge.invalid/other/repository", "trunk": "main"},
    ]
    operation = OperationConfig.model_validate(fields)
    server = ScopeMcpServer()
    server.state_types.update({"Todo": "unstarted", "Done": "completed"})
    server.issues = {
        ISSUE: ScopeMcpIssue(
            id=ISSUE, description=BODY, labels=["approved scope", "criteria complete"]
        ),
        TODO: ScopeMcpIssue(
            id=TODO,
            parent_id=ISSUE,
            labels=["check"],
            status="Todo",
            status_type="unstarted",
            description=(
                "**Check:** Run the current native test.\n"
                "**Do:** Author rationale.\n**Evidence:** —"
            ),
        ),
        PRIOR: ScopeMcpIssue(
            id=PRIOR,
            parent_id=ISSUE,
            labels=["check"],
            status="Done",
            status_type="completed",
            description=(
                "**Check:** Keep the prior demonstration.\n"
                "**Do:** Existing work.\n**Evidence:** Prior verdict."
            ),
        ),
    }
    native, _ = build_tracker(
        config=AppConfig(_env_file=None), operation=operation, caller=server
    )
    await native.record_base_spec(issue_key=ISSUE, spec=BASE)
    await native.post_comment(
        issue_key=ISSUE,
        body=f'<!-- {MARKER_PREFIXES["repository"]} url="{REPOSITORY}" -->',
    )
    fake = FakeTrackerPort(
        issues=[await native.read_issue(issue_key=key) for key in server.issues],
        marker_prefixes=MARKER_PREFIXES,
        scope_containers=[
            _container(INITIATIVE),
            _container(PROJECT, INITIATIVE),
            _container(MILESTONE, PROJECT),
        ],
        scope_label_members={SCOPE: frozenset({ScopeLabel.APPROVED})},
        criteria_stage_label_key="criteria",
        recorded_base_specs={ISSUE: BASE},
        recorded_repositories={ISSUE: REPOSITORY},
    )
    server.calls.clear()
    return Fixture(
        native if request.param == "native" else fake,
        native,
        fake,
        server,
        operation,
        FakeGitService(remote_branch_shas={BASE.base_branch: HEAD}),
        FakeRepoCache(),
        FakeWorkspaceProvider(),
        NativeExecutor(
            events=[result_event(subtype="success", structured_output=output())]
        ),
    )


async def test_actual_public_builder_runs_native_preloop_then_refuses_missing_graph(
    prepared, monkeypatch
):
    read = AsyncMock(wraps=prepared.tracker.read_fire_spec)
    monkeypatch.setattr(prepared.tracker, "read_fire_spec", read)
    acquire = AsyncMock(wraps=prepared.workspace.acquire)
    monkeypatch.setattr(prepared.workspace, "acquire", acquire)
    with pytest.raises(ScopedExecutionUnavailableError, match="publication and loop"):
        await prepared.drive()
    read.assert_awaited_once_with(issue_key=ISSUE)
    assert len(prepared.executor.calls) == 2
    validation, proposal = prepared.executor.calls
    assert BODY in validation["prompt"] and TODO in validation["prompt"]
    assert PRIOR not in validation["prompt"]
    assert BODY in proposal["prompt"] and PRIOR in proposal["prompt"]
    for actual in (validation, proposal):
        assert "criterion/fake" not in actual["prompt"]
        assert actual["session_id"] is None
        assert actual["run_identity"] == IDENTITY
        assert (
            "Edit" not in actual["allowed_tools"]
            and "Write" not in actual["allowed_tools"]
        )
    assert acquire.await_args_list == [
        call(repo_path="/tmp/fake-cache", ref=HEAD, create_branch=False),
        call(repo_path="/tmp/fake-cache", ref=HEAD, create_branch=False),
    ]
    assert prepared.workspace.calls[-1] == ("release", "/tmp/fake-workspace")
    prepared.no_writes()


@pytest.mark.parametrize(
    "identity",
    [
        None,
        IDENTITY.model_copy(update={"name": "foreign"}),
        IDENTITY.model_copy(update={"kind": RunKind.GROOMING}),
    ],
)
async def test_public_entry_refuses_wrong_run_identity_before_repository_session(
    prepared, identity
):
    with pytest.raises(TrackerFirePreparationError, match="run identity"):
        await prepared.drive(run_identity=identity)
    assert prepared.cache.calls == [] and prepared.workspace.calls == []
    assert prepared.executor.calls == []
    prepared.no_writes()


@pytest.mark.parametrize(
    "change", ["repository", "base", "missing-base", "missing-repository"]
)
async def test_recorded_dispatch_must_agree_before_any_repository_or_session(
    prepared, change
):
    if change == "repository":
        prepared.repository("https://forge.invalid/foreign/repository")
    elif change == "base":
        prepared.base(trunk_base("another/base"))
    else:
        purpose = "base_spec" if change == "missing-base" else "repository"
        prepared.server.comments = [
            row
            for row in prepared.server.comments
            if MARKER_PREFIXES[purpose] not in row.body
        ]
        if change == "missing-base":
            prepared.fake.recorded_base_specs.clear()
        else:
            prepared.fake.recorded_repositories.clear()
    with pytest.raises(TrackerFirePreparationError, match="queued"):
        await prepared.drive()
    assert prepared.cache.calls == [] and prepared.workspace.calls == []
    assert prepared.executor.calls == []
    prepared.no_writes()


@pytest.mark.parametrize(
    "role",
    [
        WorkRefRole.DELIVERABLE,
        WorkRefRole.ITERATION,
        WorkRefRole.BEST_ITERATION,
        WorkRefRole.RECOVERY,
    ],
)
async def test_existing_work_never_validates_its_old_base_as_the_current_head(
    prepared, role
):
    prepared.work_ref(role)
    with pytest.raises(TrackerFirePreparationError, match="resume-head"):
        await prepared.drive()
    assert prepared.cache.calls == [] and prepared.workspace.calls == []
    assert prepared.executor.calls == []
    prepared.no_writes()


async def test_missing_remote_branch_never_substitutes_a_head(prepared):
    prepared.git._remote_branch_shas[BASE.base_branch] = None
    with pytest.raises(TrackerFirePreparationError, match="absent from the remote"):
        await prepared.drive()
    assert prepared.workspace.calls == [] and prepared.executor.calls == []
    prepared.no_writes()


@pytest.mark.parametrize("changed", ["repository", "base", "work", "scope", "head"])
async def test_changes_during_the_real_session_refuse_preparation(prepared, changed):
    async def change():
        if changed == "repository":
            prepared.repository("https://forge.invalid/foreign/repository")
        elif changed == "base":
            prepared.base(trunk_base("later/base"))
        elif changed == "work":
            prepared.work_ref(WorkRefRole.DELIVERABLE)
        elif changed == "scope":
            prepared.revoke_approval()
        else:
            prepared.git._remote_branch_shas[BASE.base_branch] = "b" * 40

    prepared.executor.during = change
    with pytest.raises(TrackerFirePreparationError, match="changed during preparation"):
        await prepared.drive()
    assert len(prepared.executor.calls) == 1
    assert prepared.workspace.calls[-1] == ("release", "/tmp/fake-workspace")
    prepared.no_writes()


@pytest.mark.parametrize("field", ["issue_key", "repo_url"])
async def test_unaddressed_scope_still_refuses_without_an_invented_selection(
    prepared, field
):
    with pytest.raises(ScopedExecutionUnavailableError, match="Scoped graph execution"):
        await prepared.drive(**{field: None})
    assert prepared.cache.calls == [] and prepared.executor.calls == []
    prepared.no_writes()


async def test_currently_complete_issue_is_not_prepared_as_ready(prepared):
    from kodezart.types.domain.tracker import WorkflowStateKind

    prepared.server.issues[TODO].status = "Done"
    prepared.server.issues[TODO].status_type = "completed"
    prepared.fake.issues[TODO] = prepared.fake.issues[TODO].model_copy(
        update={"state_name": "Done", "state_kind": WorkflowStateKind.COMPLETED}
    )
    with pytest.raises(TrackerFirePreparationError, match="not uniquely ready"):
        await prepared.drive()
    assert not prepared.cache.calls and not prepared.executor.calls
    prepared.no_writes()


@pytest.mark.parametrize("field", ["subject", "head"])
async def test_validator_result_cannot_substitute_its_address(
    prepared, monkeypatch, field
):
    from kodezart.chains.tracker_feasibility import TrackerFeasibilityValidator

    original = TrackerFeasibilityValidator.validate

    async def foreign(self, request):
        observed = await original(self, request)
        if field == "head":
            return observed.model_copy(update={"head_sha": "b" * 40})
        return observed.model_copy(
            update={"spec": observed.spec.model_copy(update={"subject": "foreign"})}
        )

    monkeypatch.setattr(TrackerFeasibilityValidator, "validate", foreign)
    with pytest.raises(TrackerFirePreparationError, match="different subject or head"):
        await prepared.drive()
    assert len(prepared.executor.calls) == 1
    assert prepared.workspace.calls[-1] == ("release", "/tmp/fake-workspace")
    prepared.no_writes()


@pytest.mark.parametrize(
    "route",
    ["bound-elsewhere", "missing-team", "missing-repo"],
)
async def test_recorded_marker_cannot_override_operation_routing(prepared, route):
    operation = prepared.operation.model_dump()
    if route == "bound-elsewhere":
        operation["teams"]["board"]["repository"] = operation["repos"][1]["url"]
    elif route == "missing-team":
        operation["teams"] = {}
    else:
        operation["repos"] = [
            *operation["repos"][1:],
            {"url": "https://forge.invalid/third/repository", "trunk": "main"},
        ]
    prepared.operation = OperationConfig.model_validate(operation)
    with pytest.raises(TrackerFirePreparationError, match="recorded-route"):
        await prepared.drive()
    assert not prepared.cache.calls and not prepared.executor.calls
    prepared.no_writes()


@pytest.mark.parametrize("missing", ["operation", "native-team"])
async def test_absent_routing_authority_refuses_before_repository_access(
    prepared, missing
):
    if missing == "operation":
        prepared.operation = None
    else:
        prepared.server.issues[ISSUE].team = "outside-operation"
        prepared.fake.issues[ISSUE] = prepared.fake.issues[ISSUE].model_copy(
            update={"team_key": None}
        )
    with pytest.raises(TrackerFirePreparationError, match="recorded-route"):
        await prepared.drive()
    assert not prepared.cache.calls and not prepared.executor.calls
    prepared.no_writes()
