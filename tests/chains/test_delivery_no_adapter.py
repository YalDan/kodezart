"""Capability absence has its own delivery outcome before any outbound work."""

import json
from dataclasses import dataclass

import httpx
import pytest

from kodezart.chains.delivery_coordinator import DeliveryCoordinator
from kodezart.composition.forge import build_forge_client, delivery_client_for_origin
from kodezart.core.config import AppConfig
from kodezart.domain.errors import (
    DeliveryContextError,
    DeliveryRouteUnavailableError,
    ForgeAPIError,
)
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.operation import OperationConfig, RepoEntry, RunKind
from kodezart.types.domain.outcome import WorkflowOutcome
from tests.adapters.test_github_api import _completed_run, _make_client
from tests.chains.test_delivery_runtime import (
    BASE,
    HEAD,
    REPOSITORY,
    SHA,
    context,
    deliver,
    description,
    dispatch,
)
from tests.chains.test_delivery_stalled import stalled_context
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentRunner,
    FakeArtifactPersister,
    FakeGitService,
    FakeRepoCache,
    PassThroughGate,
    make_prompt_provider,
)


@dataclass
class Fixture:
    coordinator: DeliveryCoordinator
    runner: FakeAgentRunner
    gate: PassThroughGate
    cleaner: FakeArtifactPersister
    git: FakeGitService
    cache: FakeRepoCache

    def assert_no_activity(self):
        assert self.runner.calls == self.gate.calls == self.cleaner.clean_calls == []
        assert self.git.calls == self.cache.calls == []


def build(*, client=None, **capabilities):
    runner = FakeAgentRunner([description()])
    gate = PassThroughGate()
    cleaner = FakeArtifactPersister()
    git = FakeGitService(remote_branch_shas={HEAD: SHA, BASE: "b" * 40})
    cache = FakeRepoCache()
    return Fixture(
        DeliveryCoordinator(
            runner=runner,
            prompts=make_prompt_provider(),
            skills=SUPPRESS_ALL_SKILLS,
            gate=gate,
            **{
                "pr_creator": client,
                "forge_query": client,
                "pr_editor": client,
                "ci": client,
                "ci_observations": client,
                **capabilities,
            },
            operation=OperationConfig(
                operation_name="fixture",
                workspace="fixture",
                repos=[RepoEntry(url=REPOSITORY, trunk=BASE)],
            ),
            git=git,
            cache=cache,
            git_remote="upstream",
            config=AppConfig(),
            artifact_persister=cleaner,
        ),
        runner,
        gate,
        cleaner,
        git,
        cache,
    )


@pytest.mark.parametrize("cached", [False, True])
async def test_absence_retains_dispatch_and_unknown_facts_without_side_effects(cached):
    fixture = build()
    facts = context()
    if cached:
        facts = facts.model_copy(
            update={"execution": facts.execution.model_copy(update={"repo_path": None})}
        )
    record = dispatch()
    first = await deliver(fixture.coordinator, record=record, facts=facts)
    second = await deliver(fixture.coordinator, record=record, facts=facts)
    assert first == second
    assert first.model_dump(mode="json", by_alias=True) == {
        "laneKey": record.lane_key,
        "issueId": record.issue_id,
        "headBranch": record.head_branch,
        "baseBranch": record.resolved_base.base_branch,
        "pr": None,
        "checksPassed": None,
        "checksSummary": None,
        "outcome": "review_passed_no_pr_adapter",
    }
    fixture.assert_no_activity()


@pytest.mark.parametrize(
    "mismatch", ["missing_identity", "kind", "issue", "head", "base", "sha", "repo"]
)
async def test_absence_does_not_bypass_dispatch_identity_validation(mismatch):
    fixture = build()
    facts = context()
    record = dispatch()
    branch, sha = HEAD, SHA
    if mismatch in {"missing_identity", "kind", "issue"}:
        identity = facts.execution.run_identity
        if mismatch == "missing_identity":
            identity = None
        elif mismatch == "kind":
            identity = identity.model_copy(update={"kind": RunKind.FIRE_PREP})
        else:
            identity = identity.model_copy(update={"name": "different/17"})
        facts = facts.model_copy(
            update={
                "execution": facts.execution.model_copy(
                    update={"run_identity": identity}
                )
            }
        )
    elif mismatch == "head":
        branch = "different-head"
    elif mismatch == "base":
        record = dispatch(resolved_base=trunk_base("different-base"))
    elif mismatch == "sha":
        sha = " "
    else:
        facts = facts.model_copy(
            update={"execution": facts.execution.model_copy(update={"repo_url": None})}
        )
    with pytest.raises(DeliveryContextError):
        await deliver(
            fixture.coordinator, record=record, facts=facts, branch=branch, sha=sha
        )
    fixture.assert_no_activity()


@pytest.mark.parametrize(
    "outcome",
    [
        WorkflowOutcome.review_failed_fix_budget_exhausted,
        WorkflowOutcome.loop_not_accepted,
        WorkflowOutcome.ci_not_configured,
        WorkflowOutcome.review_passed_no_pr_adapter,
    ],
)
async def test_absence_cannot_relabel_an_unaccepted_or_already_delivered_fire(outcome):
    fixture = build()
    with pytest.raises(DeliveryRouteUnavailableError):
        await deliver(fixture.coordinator, facts=context(fire_outcome=outcome))
    fixture.assert_no_activity()


async def test_valid_stalled_facts_still_require_their_pull_request_route():
    fixture = build()
    with pytest.raises(DeliveryRouteUnavailableError, match="configured PR creator"):
        await deliver(fixture.coordinator, facts=stalled_context())
    fixture.assert_no_activity()


@pytest.mark.parametrize(
    "origin", ["file:///tmp/origin.git", "file://localhost/tmp/origin.git"]
)
@pytest.mark.parametrize("configured", [False, True])
async def test_real_origin_selection_prevents_native_forge_access(origin, configured):
    requests = []

    def handler(request):
        requests.append(request)
        raise AssertionError("a local origin cannot ask the forge")

    client = _make_client(handler)
    try:
        selected = delivery_client_for_origin(
            client=client if configured else None, repo_url=origin
        )
        fixture = build(client=selected)
        facts = context()
        facts = facts.model_copy(
            update={
                "execution": facts.execution.model_copy(update={"repo_url": origin})
            }
        )
        result = await deliver(fixture.coordinator, facts=facts)
        assert result.outcome is WorkflowOutcome.review_passed_no_pr_adapter
        assert result.pr is None and result.checks_passed is None
        assert selected is None and requests == []
        fixture.assert_no_activity()
    finally:
        await client.close()


async def test_actual_missing_credential_configuration_reaches_the_absence_route():
    client = build_forge_client(config=AppConfig(github_token=None))
    selected = delivery_client_for_origin(client=client, repo_url=REPOSITORY)
    fixture = build(client=selected)
    result = await deliver(fixture.coordinator)
    assert client is selected is None
    assert result.outcome is WorkflowOutcome.review_passed_no_pr_adapter
    fixture.assert_no_activity()


@pytest.mark.parametrize(
    "missing", ["forge_query", "pr_editor", "ci", "ci_observations"]
)
async def test_configured_creator_with_missing_support_refuses_before_any_io(missing):
    requests = []

    def handler(request):
        requests.append(request)
        raise AssertionError("partial configuration cannot reach the forge")

    client = _make_client(handler)
    try:
        fixture = build(client=client, **{missing: None})
        with pytest.raises(
            DeliveryContextError, match="all forge read/check capabilities"
        ):
            await deliver(fixture.coordinator)
        assert requests == []
        fixture.assert_no_activity()
    finally:
        await client.close()


async def test_creator_absence_is_explicit_even_when_read_capabilities_exist():
    requests = []

    def handler(request):
        requests.append(request)
        raise AssertionError("unused readers do not establish PR creation capability")

    client = _make_client(handler)
    try:
        fixture = build(client=client, pr_creator=None)
        result = await deliver(fixture.coordinator)
        assert result.outcome is WorkflowOutcome.review_passed_no_pr_adapter
        assert requests == []
        fixture.assert_no_activity()
    finally:
        await client.close()


@pytest.mark.parametrize("failure", [None, "lookup", "create"])
async def test_native_selected_capability_acts_or_raises_never_falls_back(failure):
    requests, stored = [], []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/pulls"):
            if request.method == "GET":
                if failure == "lookup":
                    return httpx.Response(403, json={"message": "refused"})
                return httpx.Response(200, json=stored)
            if request.method == "POST":
                if failure == "create":
                    return httpx.Response(422, json={"message": "refused"})
                payload = json.loads(request.content)
                stored.append(
                    {
                        "html_url": REPOSITORY + "/pull/83",
                        "number": 83,
                        "state": "open",
                        "title": payload["title"],
                        "body": payload["body"],
                        "head": {"ref": payload["head"]},
                        "base": {"ref": payload["base"]},
                    }
                )
                return httpx.Response(201, json=stored[0])
        if request.url.path.endswith("/check-runs"):
            return _completed_run()
        raise AssertionError(
            f"unexpected native request: {request.method} {request.url}"
        )

    client = _make_client(handler)
    try:
        selected = delivery_client_for_origin(client=client, repo_url=REPOSITORY)
        assert selected is client
        fixture = build(client=selected)
        if failure is not None:
            with pytest.raises(ForgeAPIError):
                await deliver(fixture.coordinator)
            assert not any("check-runs" in str(request.url) for request in requests)
        else:
            result = await deliver(fixture.coordinator)
            assert result.outcome is WorkflowOutcome.ci_passed
            assert result.pr.number == 83 and result.pr.state == "open"
            assert result.checks_passed is True
            assert len(fixture.runner.calls) == len(fixture.cleaner.clean_calls) == 1
            assert [request.method for request in requests] == [
                "GET",
                "POST",
                "GET",
                "GET",
            ]
    finally:
        await client.close()
