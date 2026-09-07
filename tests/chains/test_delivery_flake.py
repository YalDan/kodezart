"""Actual delivery recovery uses the original branch watch's immutable commit."""

import json

import httpx
import pytest

from kodezart.core.config import AppConfig
from kodezart.domain.errors import CheckObservationError, DeliveryRouteUnavailableError
from kodezart.types.domain.operation import (
    CheckPrerequisite,
    CheckStep,
    OperationConfig,
    OperationMemberAbsentError,
    RepoEntry,
)
from kodezart.types.domain.outcome import WorkflowOutcome
from tests.adapters.test_ci_rerun import ActionsAPI
from tests.adapters.test_github_api import _make_client
from tests.chains.test_delivery_runtime import (
    BASE,
    HEAD,
    REPOSITORY,
    SHA,
    deliver,
    setup,
)
from tests.fakes import FakeArtifactPersister, FakeCIMonitor, FakeGitService

CLEAN_SHA = "c" * 40


def declarations(**changes):
    return OperationConfig(
        operation_name="fixture",
        workspace="fixture",
        repos=[RepoEntry(url=REPOSITORY, trunk=BASE, **changes)],
    )


def flake_fixture(*, passed=True, declared=False, operation=None, **changes):
    monitor = FakeCIMonitor(
        passed=False,
        failed_names=frozenset({"lint"}),
        summary="Initial red",
        declared=declared,
        rerun_results=[(passed, "Observed rerun", frozenset())],
    )
    return setup(monitor=monitor, operation=operation, **changes)


@pytest.mark.parametrize("passed", [True, None])
async def test_real_coordinator_recovers_nonred_rerun_without_another_session(passed):
    fixture = flake_fixture(passed=passed)
    result = await deliver(fixture.coordinator)
    assert result.checks_passed is passed
    assert result.outcome is (
        WorkflowOutcome.ci_passed if passed else WorkflowOutcome.ci_not_configured
    )
    assert result.checks_summary == "Observed rerun"
    assert fixture.monitor.rerun_calls == [(REPOSITORY, SHA)]
    assert fixture.monitor.calls == [
        {"repo_url": REPOSITORY, "ref": HEAD},
        {"repo_url": REPOSITORY, "ref": SHA},
    ]
    # Both comparison sets use the one ruled reader. Its original ref is
    # pinned to the completed watch, so it cannot replace the original set.
    assert fixture.monitor.failed_name_calls == [(REPOSITORY, HEAD)]
    assert len(fixture.runner.calls) == len(fixture.forge.calls) == 1
    assert result.pr.state == "open"


async def test_rerun_without_checks_still_requires_the_declaration_reader():
    fixture = flake_fixture(passed=None, declared=True)
    with pytest.raises(DeliveryRouteUnavailableError) as caught:
        await deliver(fixture.coordinator)
    assert caught.value.checks_passed is None
    assert fixture.monitor.declaration_calls == [REPOSITORY]


@pytest.mark.parametrize("case", ["work", "unclassified", "prerequisite"])
async def test_other_positive_red_classes_refuse_without_silent_green(case):
    monitor = FakeCIMonitor(
        passed=False,
        failed_names=frozenset({"lint"}),
        rerun_results=[(False, "Still red", frozenset({"different"}))],
    )
    operation = declarations()
    if case == "prerequisite":
        operation = declarations(
            checks=(
                CheckStep(
                    name="gate",
                    command="check",
                    forge_check="lint",
                    requires=(CheckPrerequisite.REPOSITORY_HISTORY,),
                ),
            ),
            runner_environment={CheckPrerequisite.REPOSITORY_HISTORY: False},
        )
    fixture = setup(
        monitor=monitor,
        operation=operation,
        config=AppConfig(delivery_red_rerun_max_attempts=0 if case == "work" else 1),
    )
    expected = {
        "work": "work_defect",
        "unclassified": "unclassified",
        "prerequisite": "environment_prerequisite_unmet",
    }[case]
    with pytest.raises(DeliveryRouteUnavailableError, match=expected):
        await deliver(fixture.coordinator)
    assert len(monitor.rerun_calls) == (1 if case == "unclassified" else 0)
    assert len(fixture.runner.calls) == len(fixture.forge.calls) == 1


@pytest.mark.parametrize("mapped,declared", [(False, False), (True, None)])
async def test_missing_environment_declaration_does_not_excuse_the_red(
    mapped, declared
):
    fixture = flake_fixture(
        operation=declarations(
            checks=(
                CheckStep(
                    name="gate",
                    command="check",
                    forge_check="lint" if mapped else None,
                    requires=(CheckPrerequisite.REPOSITORY_HISTORY,),
                ),
            ),
            runner_environment={}
            if declared is None
            else {CheckPrerequisite.REPOSITORY_HISTORY: declared},
        )
    )
    assert (await deliver(fixture.coordinator)).outcome is WorkflowOutcome.ci_passed
    assert fixture.monitor.rerun_calls == [(REPOSITORY, SHA)]


@pytest.mark.parametrize("problem", ["missing", "ambiguous"])
async def test_red_needs_one_declared_repository_not_a_default_profile(problem):
    operation = declarations()
    operation.repos[:] = (
        []
        if problem == "missing"
        else [
            operation.repos[0],
            operation.repos[0].model_copy(update={"url": REPOSITORY + ".git"}),
        ]
    )
    fixture = flake_fixture(operation=operation)
    expected = (
        OperationMemberAbsentError if problem == "missing" else CheckObservationError
    )
    with pytest.raises(expected):
        await deliver(fixture.coordinator)
    assert fixture.monitor.rerun_calls == []


async def test_equivalent_configured_clone_url_keeps_its_declared_facts():
    operation = declarations()
    operation.repos[:] = [
        operation.repos[0].model_copy(update={"url": REPOSITORY + ".git"})
    ]
    fixture = flake_fixture(operation=operation)
    assert (await deliver(fixture.coordinator)).outcome is WorkflowOutcome.ci_passed
    assert fixture.monitor.rerun_calls == [(REPOSITORY, SHA)]


async def test_branch_watch_for_another_commit_refuses_before_any_rerun():
    fixture = flake_fixture()
    fixture.monitor.observed_sha_by_ref[HEAD] = "b" * 40
    with pytest.raises(CheckObservationError, match="delivered red head"):
        await deliver(fixture.coordinator)
    assert fixture.monitor.rerun_calls == []


async def test_head_movement_during_recovery_cannot_publish_green_for_new_code():
    fixture = flake_fixture(
        git=FakeGitService(
            remote_branch_shas={BASE: "b" * 40},
            remote_branch_sha_sequences={HEAD: [SHA, "d" * 40]},
        )
    )
    with pytest.raises(CheckObservationError, match="moved during"):
        await deliver(fixture.coordinator)
    assert fixture.monitor.rerun_calls == [(REPOSITORY, SHA)]
    assert len(fixture.forge.calls) == 1


@pytest.mark.parametrize("cleanup", [False, True])
@pytest.mark.parametrize("changed_names", [False, True])
async def test_native_delivery_reruns_original_observed_sha_after_cleanup(
    cleanup, changed_names
):
    observed_sha = CLEAN_SHA if cleanup else SHA
    server = ActionsAPI(
        sha=observed_sha, fresh_failed=("different",) if changed_names else ()
    )
    requests = []
    rows = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/repos/example/project/pulls":
            if request.method == "GET":
                return httpx.Response(200, json=rows)
            assert request.method == "POST" and not rows
            body = json.loads(request.content)
            rows.append(
                {
                    "html_url": f"{REPOSITORY}/pull/7",
                    "number": 7,
                    "title": body["title"],
                    "body": body["body"],
                    "head": {"ref": body["head"]},
                    "base": {"ref": body["base"]},
                }
            )
            return httpx.Response(201, json=rows[0])
        if request.url.path == f"/repos/example/project/commits/{HEAD}/check-runs":
            checks = [
                {**job, "check_suite": {"id": 1101}} for job in server.jobs(101, 1)
            ]
            if changed_names:
                # A later read now reports another original set. The
                # classifier must still compare with the watched lint set.
                server.names = ("different",)
            return httpx.Response(
                200, json={"total_count": len(checks), "check_runs": checks}
            )
        return server(request)

    client = _make_client(handler)
    fixture = setup(
        forge=client,
        editor=client,
        query=client,
        monitor=client,
        observations=client,
        cleaner=FakeArtifactPersister() if cleanup else None,
        git=FakeGitService(
            remote_branch_shas={BASE: "b" * 40, HEAD: observed_sha},
            remote_branch_sha_sequences={HEAD: [SHA, observed_sha]},
        ),
    )
    try:
        if changed_names:
            with pytest.raises(DeliveryRouteUnavailableError, match="unclassified"):
                await deliver(fixture.coordinator)
        else:
            result = await deliver(fixture.coordinator)
            assert result.outcome is WorkflowOutcome.ci_passed
            assert result.pr.state == "open"
    finally:
        await client.close()
    assert len(server.writes) == 1 and server.attempts == {101: 2}
    assert len(fixture.runner.calls) == 1
    assert len(rows) == 1
    refs = [
        request.url.path.split("/commits/")[1].split("/check-runs")[0]
        for request in requests
        if "/commits/" in request.url.path
    ]
    assert refs[0] == HEAD and all(ref == observed_sha for ref in refs[1:])
    writes = [request for request in requests if request.method != "GET"]
    assert [request.url.path for request in writes] == [
        "/repos/example/project/pulls",
        "/repos/example/project/actions/runs/101/rerun",
    ]
