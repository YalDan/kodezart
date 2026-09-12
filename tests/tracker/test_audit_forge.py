"""Native criterion Evidence is checked at its exact SHA through real CI ports."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from kodezart.chains.audit_forge import AuditForgeVerifier
from kodezart.core.config import AppConfig
from kodezart.domain.errors import AuditEvidenceReadError
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.audit_forge import AuditForgeObservation, AuditForgeRequest
from kodezart.types.domain.delivery import CheckRedClass
from kodezart.types.domain.fire_spec import CriterionRef
from kodezart.types.domain.operation import (
    CheckPrerequisite,
    CheckStep,
    OperationConfig,
    RepoEntry,
)
from tests.adapters.test_ci_rerun import ActionsAPI
from tests.adapters.test_github_api import _make_client
from tests.fakes import FakeCIMonitor
from tests.tracker import test_audit_evidence as evidence_fixtures
from tests.tracker.test_audit_claim import CHILD, ROOT

server = evidence_fixtures.server
REPO = "https://github.com/example/project"
SHA = "a" * 40
NAMES = frozenset({"unit", "integration"})
REQUEST = AuditForgeRequest(
    criterion_key=CriterionRef(CHILD), lane_issue_key=ROOT, repo_url=REPO
)
REPOSITORY = RepoEntry(
    url=REPO,
    trunk="declared-base",
    checks=tuple(
        CheckStep(
            name=name,
            command=f"run {name}",
            forge_check=name,
            requires=(CheckPrerequisite.REPOSITORY_HISTORY,),
        )
        for name in sorted(NAMES)
    ),
)


@pytest.fixture
async def setup(tracker):
    await tracker.update_issue(issue_key=CHILD, body=evidence_fixtures.body(SHA))
    await tracker.restore_workflow_state(issue_key=CHILD, state_name="Done")

    def build(ci, *, repository=REPOSITORY, bound=1):
        return AuditForgeVerifier(
            tracker=tracker,
            ci=ci,
            operation=OperationConfig(
                operation_name="fixture", workspace="fixture", repos=[repository]
            ),
            config=AppConfig(_env_file=None, delivery_red_rerun_max_attempts=bound),
        )

    return build


@asynccontextmanager
async def forge(backend, case):
    red = case in {"work", "flake", "unknown", "environment"}
    observed_names = frozenset({"unit"}) if case == "roster" else NAMES
    observed_sha = "b" * 40 if case == "foreign-sha" else SHA
    passed = None if case == "absent" else not red
    rerun_failed = (
        NAMES
        if case == "work"
        else frozenset({"unit"})
        if case == "unknown"
        else frozenset()
    )
    if backend == "fake":
        ci = FakeCIMonitor(
            passed=passed,
            failed_names=NAMES if red else frozenset(),
            rerun_results=[(not bool(rerun_failed), "fresh result", rerun_failed)],
            observed_sha_by_ref={SHA: observed_sha},
            check_names=observed_names,
        )
        yield ci, ci.rerun_calls
        return
    actions = ActionsAPI(names=tuple(sorted(NAMES)), fresh_failed=rerun_failed)

    def handler(request):
        if red:
            return actions(request)
        actions.requests.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/workflows"):
            return httpx.Response(200, json={"total_count": 0, "workflows": []})
        assert request.url.path == f"/repos/example/project/commits/{SHA}/check-runs"
        rows = [
            {
                "id": index,
                "name": name,
                "head_sha": observed_sha,
                "status": "completed",
                "conclusion": "success",
            }
            for index, name in enumerate(sorted(observed_names), start=1)
        ]
        if case == "absent":
            rows = []
        return httpx.Response(200, json={"total_count": len(rows), "check_runs": rows})

    ci = _make_client(handler, ci_no_workflows_grace_polls=1)
    try:
        yield ci, actions.requests
    finally:
        await ci.close()


@pytest.mark.parametrize("backend", ["fake", "github"])
@pytest.mark.parametrize(
    "case,verdict,red_class",
    [
        ("green", AuditVerdict.HOLDS, None),
        ("work", AuditVerdict.REFUTED, CheckRedClass.WORK_DEFECT),
        ("flake", AuditVerdict.HOLDS, CheckRedClass.RUNNER_FLAKE),
        ("unknown", AuditVerdict.UNVERIFIABLE, CheckRedClass.UNCLASSIFIED),
        (
            "environment",
            AuditVerdict.UNVERIFIABLE,
            CheckRedClass.ENVIRONMENT_PREREQUISITE_UNMET,
        ),
        ("absent", AuditVerdict.UNVERIFIABLE, None),
        ("foreign-sha", AuditVerdict.UNVERIFIABLE, None),
        ("roster", AuditVerdict.UNVERIFIABLE, None),
    ],
)
async def test_exact_sha_roster_and_the_one_red_classifier_determine_the_verdict(
    setup, tracker_writes, backend, case, verdict, red_class
):
    repository = REPOSITORY
    if case == "environment":
        repository = repository.model_copy(
            update={"runner_environment": {CheckPrerequisite.REPOSITORY_HISTORY: False}}
        )
    before = tracker_writes()
    async with forge(backend, case) as (ci, calls):
        result = await setup(ci, repository=repository).observe(REQUEST)
        assert result.verdict is verdict
        assert (None if result.red is None else result.red.red_class) is red_class
        assert result.criterion.issue_key == CHILD
        assert result.recorded_evidence.graded_sha == SHA and SHA in result.reason
        assert result.required_check_names == NAMES
        assert (
            AuditForgeObservation.model_validate_json(result.model_dump_json())
            == result
        )
        if result.checks is not None:
            assert result.checks.commit_sha == SHA
        reruns = (
            calls if backend == "fake" else [r for r in calls if r.method == "POST"]
        )
        assert len(reruns) == (1 if case in {"work", "flake", "unknown"} else 0)
        if backend == "fake":
            assert {row["ref"] for row in ci.calls} == {SHA}
        else:
            assert all(
                f"/commits/{SHA}" in r.url.path
                for r in calls
                if "/commits/" in r.url.path
            )
    assert tracker_writes() == before


@pytest.mark.parametrize("backend", ["fake", "github"])
async def test_zero_rerun_bound_uses_the_existing_classifier_without_a_write(
    setup, backend
):
    async with forge(backend, "work") as (ci, calls):
        result = await setup(ci, bound=0).observe(REQUEST)
        assert result.verdict is AuditVerdict.REFUTED
        assert result.red.red_class is CheckRedClass.WORK_DEFECT
        assert not (
            calls if backend == "fake" else [r for r in calls if r.method == "POST"]
        )


async def test_no_check_capability_does_not_silently_accept_done(setup):
    result = await setup(None).observe(REQUEST)
    assert result.verdict is AuditVerdict.UNVERIFIABLE and SHA in result.reason


@pytest.mark.parametrize("state", ["Todo", "In Review", "Canceled"])
async def test_only_the_current_completed_criterion_enters_this_check(
    setup, tracker, state
):
    await tracker.restore_workflow_state(issue_key=CHILD, state_name=state)
    ci = FakeCIMonitor()
    with pytest.raises(AuditEvidenceReadError, match="completed criterion"):
        await setup(ci).observe(REQUEST)
    assert ci.calls == []


async def test_foreign_recorded_prose_cannot_supply_a_convenient_sha(setup, tracker):
    await tracker.update_issue(issue_key=CHILD, body=f"**Evidence:** {SHA} test_unit")
    ci = FakeCIMonitor()
    with pytest.raises(AuditEvidenceReadError):
        await setup(ci).observe(REQUEST)
    assert ci.calls == []


@pytest.mark.parametrize("damage", ["body", "state", "membership"])
async def test_a_mid_watch_source_change_cannot_receive_a_forge_verdict(
    setup, tracker, monkeypatch, damage
):
    async with forge("fake", "green") as (ci, _):
        original = ci.wait_for_checks

        async def changing(**kwargs):
            result = await original(**kwargs)
            if damage == "body":
                await tracker.update_issue(
                    issue_key=CHILD, body=evidence_fixtures.body("c" * 40)
                )
            elif damage == "state":
                await tracker.restore_workflow_state(issue_key=CHILD, state_name="Todo")
            else:
                rows = await tracker.read_criteria(issue_key=ROOT)
                monkeypatch.setattr(
                    tracker,
                    "read_criteria",
                    AsyncMock(
                        return_value=[
                            row.model_copy(update={"parent_key": "foreign"})
                            for row in rows
                        ]
                    ),
                )
            return result

        monkeypatch.setattr(ci, "wait_for_checks", changing)
        with pytest.raises(AuditEvidenceReadError):
            await setup(ci).observe(REQUEST)


async def test_unclassified_red_cannot_be_restamped_clean_by_model_construction(setup):
    async with forge("fake", "unknown") as (ci, _):
        result = await setup(ci).observe(REQUEST)
        values = result.model_dump()
        values["verdict"] = AuditVerdict.HOLDS
        with pytest.raises(ValidationError):
            AuditForgeObservation.model_validate(values)


@pytest.mark.parametrize("backend", ["fake", "github"])
async def test_a_callers_earlier_green_rerun_cannot_replace_a_new_red_watch(
    setup, backend
):
    if backend == "fake":
        ci = FakeCIMonitor(
            passed=False,
            failed_names=NAMES,
            rerun_results=[(True, "old green", frozenset())],
            observed_sha_by_ref={SHA: SHA},
            check_names=NAMES,
        )
        await ci.rerun_checks(repo_url=REPO, ref=SHA)
        assert (await ci.wait_for_checks(repo_url=REPO, ref=SHA)).checks_passed is True
        result = await setup(ci, bound=0).observe(REQUEST)
    else:

        class TimelineAPI(ActionsAPI):
            def run(self, run_id, attempt):
                row = super().run(run_id, attempt)
                row["conclusion"] = "success" if attempt == 2 else "failure"
                return row

            def jobs(self, run_id, attempt):
                rows = super().jobs(run_id, attempt)
                for row in rows:
                    row["conclusion"] = "success" if attempt == 2 else "failure"
                return rows

        api = TimelineAPI(names=tuple(sorted(NAMES)))
        api.latest_checks = True
        ci = _make_client(api)
        try:
            await ci.rerun_checks(repo_url=REPO, ref=SHA)
            assert (
                await ci.wait_for_checks(repo_url=REPO, ref=SHA)
            ).checks_passed is True
            api.attempts[101] = 3
            result = await setup(ci, bound=0).observe(REQUEST)
            assert len(api.writes) == 1
        finally:
            await ci.close()
    assert result.verdict is AuditVerdict.REFUTED
    assert result.checks.checks_passed is False


@pytest.mark.parametrize(
    "damage",
    ["pending", "unknown", "missing-sha", "mixed-sha", "duplicate", "incomplete"],
)
async def test_unreadable_native_terminal_evidence_never_enters_classification(
    setup, damage
):
    rows = [
        {
            "id": index,
            "name": name,
            "head_sha": SHA,
            "status": "completed",
            "conclusion": "success",
        }
        for index, name in enumerate(sorted(NAMES), start=1)
    ]
    total = len(rows)
    if damage == "pending":
        rows[0]["status"] = "in_progress"
    elif damage == "unknown":
        rows[0]["conclusion"] = "new-vendor-value"
    elif damage == "missing-sha":
        rows[0].pop("head_sha")
    elif damage == "mixed-sha":
        rows[0]["head_sha"] = "b" * 40
    elif damage == "duplicate":
        rows[1]["id"] = rows[0]["id"]
    else:
        total += 1
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == f"/repos/example/project/commits/{SHA}/check-runs"
        return httpx.Response(200, json={"total_count": total, "check_runs": rows})

    ci = _make_client(handler, ci_poll_max_attempts=1, ci_check_runs_max_pages=1)
    try:
        result = await setup(ci).observe(REQUEST)
        assert result.verdict is AuditVerdict.UNVERIFIABLE
        assert result.red is None and SHA in result.reason
    finally:
        await ci.close()


async def test_a_missing_repository_declaration_refuses_before_the_forge(setup):
    ci = FakeCIMonitor()
    result = await setup(ci).observe(
        REQUEST.model_copy(update={"repo_url": "https://github.com/other/repository"})
    )
    assert result.verdict is AuditVerdict.UNVERIFIABLE and SHA in result.reason
    assert ci.calls == []


async def test_absent_checks_remain_unverifiable_even_when_delivery_is_forge_exempt(
    setup,
):
    async with forge("fake", "absent") as (ci, _):
        repo = REPOSITORY.model_copy(update={"forge_exempt": True})
        result = await setup(ci, repository=repo).observe(REQUEST)
        assert result.verdict is AuditVerdict.UNVERIFIABLE
        assert SHA in result.reason


async def test_cancellation_does_not_return_a_forge_verdict_or_leave_the_watch_alive(
    setup, tracker_writes, monkeypatch
):
    entered, finished = asyncio.Event(), asyncio.Event()
    ci = FakeCIMonitor()

    async def watch(**_kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    monkeypatch.setattr(ci, "wait_for_checks", watch)
    before = tracker_writes()
    task = asyncio.create_task(setup(ci).observe(REQUEST))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert finished.is_set() and tracker_writes() == before


@pytest.mark.parametrize("damage", ["status", "failed-names", "reader-error"])
async def test_contradictory_or_failed_port_evidence_is_not_a_classified_red(
    setup, monkeypatch, damage
):
    async with forge("fake", "work") as (ci, _):
        original = ci.wait_for_checks

        async def changed(**kwargs):
            if damage == "reader-error":
                raise OSError("forge read failed")
            row = await original(**kwargs)
            values = row.model_dump()
            if damage == "failed-names":
                values["failed_check_names"] = frozenset({"foreign"})
            else:
                values["checks_passed"] = True
            return type(row).model_validate(values)

        monkeypatch.setattr(ci, "wait_for_checks", changed)
        result = await setup(ci).observe(REQUEST)
        assert result.verdict is AuditVerdict.UNVERIFIABLE and result.red is None
        assert ci.rerun_calls == []


async def test_forge_transport_failure_is_reported_without_branch_fallback(setup):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == f"/repos/example/project/commits/{SHA}/check-runs"
        return httpx.Response(403, json={"message": "missing authorization"})

    ci = _make_client(handler)
    try:
        result = await setup(ci).observe(REQUEST)
        assert result.verdict is AuditVerdict.UNVERIFIABLE and SHA in result.reason
        assert result.checks is result.red is None
        assert len(calls) == 1
    finally:
        await ci.close()


async def test_a_classified_flake_without_an_observable_rerun_is_not_green(setup):
    ci = FakeCIMonitor(
        passed=False,
        failed_names=NAMES,
        check_names=NAMES,
        observed_sha_by_ref={SHA: SHA},
        rerun_results=[(None, "no observable rerun", frozenset())],
    )
    result = await setup(ci).observe(REQUEST)
    assert result.red.red_class is CheckRedClass.RUNNER_FLAKE
    assert result.red.checks_passed is None
    assert result.verdict is AuditVerdict.UNVERIFIABLE
    assert ci.rerun_calls == [(REPO, SHA)]


@pytest.mark.parametrize("backend", ["fake", "github"])
async def test_an_empty_declared_roster_leaves_the_native_ci_roster_authoritative(
    setup, backend
):
    repository = REPOSITORY.model_copy(
        update={"checks": (CheckStep(name="local-only", command="run local"),)}
    )
    async with forge(backend, "green") as (ci, _):
        result = await setup(ci, repository=repository).observe(REQUEST)
        assert result.verdict is AuditVerdict.HOLDS
        assert result.required_check_names == frozenset()
        assert result.checks.check_names == NAMES


@pytest.mark.parametrize("backend", ["fake", "github"])
@pytest.mark.parametrize("rerun_green", [False, True])
async def test_a_readable_red_is_classified_even_when_another_declared_check_is_missing(
    setup, backend, rerun_green
):
    failures = frozenset() if rerun_green else frozenset({"unit"})
    if backend == "fake":
        ci = FakeCIMonitor(
            passed=False,
            failed_names=frozenset({"unit"}),
            check_names=frozenset({"unit"}),
            rerun_results=[(rerun_green, "rerun result", failures)],
            observed_sha_by_ref={SHA: SHA},
        )
        result = await setup(ci).observe(REQUEST)
        assert ci.rerun_calls == [(REPO, SHA)]
    else:
        api = ActionsAPI(names=("unit",), fresh_failed=failures)
        ci = _make_client(api)
        try:
            result = await setup(ci).observe(REQUEST)
            assert len(api.writes) == 1
        finally:
            await ci.close()
    assert result.verdict is (
        AuditVerdict.UNVERIFIABLE if rerun_green else AuditVerdict.REFUTED
    )
    assert result.red.red_class is (
        CheckRedClass.RUNNER_FLAKE if rerun_green else CheckRedClass.WORK_DEFECT
    )
    assert result.required_check_names - result.checks.check_names == {"integration"}


async def test_a_constructed_green_verdict_cannot_ignore_the_declared_roster(setup):
    async with forge("fake", "roster") as (ci, _):
        result = await setup(ci).observe(REQUEST)
    assert result.verdict is AuditVerdict.UNVERIFIABLE
    data = result.model_dump()
    data["verdict"] = AuditVerdict.HOLDS
    with pytest.raises(ValidationError, match="complete declared roster"):
        AuditForgeObservation.model_validate(data)
