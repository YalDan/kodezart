"""The addressed public entry composes two current read-only sessions."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from kodezart.chains.rule_open_questions import TrackerRulingProposer
from kodezart.chains.tracker_feasibility import TrackerFeasibilityValidator
from kodezart.domain.errors import (
    RulingProposalError,
    ScopedExecutionUnavailableError,
    TrackerFirePreparationError,
)
from kodezart.types.domain.agent import (
    RULING_PROPOSAL_SCHEMA,
    TRACKER_CRITERIA_VALIDATION_SCHEMA,
)
from kodezart.types.domain.branch import WorkRefRole, trunk_base
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.tracker_feasibility import TrackerFeasibilityRequest
from tests.tracker.test_addressed_preloop import (
    BASE,
    BODY,
    HEAD,
    IDENTITY,
    ISSUE,
    REPOSITORY,
    TODO,
)
from tests.tracker.test_addressed_preloop import prepared as prepared
from tests.tracker.test_configured_preloop import bind
from tests.tracker.test_rule_open_questions import answer


@pytest.mark.parametrize("nonempty", [False, True])
async def test_public_entry_retains_one_capture_and_stops_before_unleased_publication(
    prepared, monkeypatch, nonempty
):
    if nonempty:
        prepared.executor.ruling_output = {
            "rulings": [answer(issueRef=TODO)],
            "unresolvedQuestions": [],
        }
    captured = []
    original = TrackerFeasibilityValidator.validate

    async def retain(self, request):
        observed = await original(self, request)
        captured.append(observed)
        return observed

    monkeypatch.setattr(TrackerFeasibilityValidator, "validate", retain)
    old_propose = TrackerRulingProposer.propose_validated
    received = []

    async def inspect(self, request, observation):
        received.append((request, observation))
        return await old_propose(self, request, observation)

    monkeypatch.setattr(TrackerRulingProposer, "propose_validated", inspect)
    read = AsyncMock(wraps=prepared.tracker.read_fire_spec)
    monkeypatch.setattr(prepared.tracker, "read_fire_spec", read)
    with pytest.raises(ScopedExecutionUnavailableError, match="publication and loop"):
        await prepared.drive()
    read.assert_awaited_once_with(issue_key=ISSUE)
    assert len(captured) == len(received) == 1
    request, observed = received[0]
    assert observed is captured[0]
    assert request.run_identity is IDENTITY
    assert request.repo_url == REPOSITORY and request.head_sha == HEAD
    assert request.cache_key == "addressed-cache"
    assert [row["output_format"]["schema"] for row in prepared.executor.calls] == [
        TRACKER_CRITERIA_VALIDATION_SCHEMA,
        RULING_PROPOSAL_SCHEMA,
    ]
    prepared.no_writes()


@pytest.mark.parametrize("route", ["recorded", "explicit"])
@pytest.mark.parametrize(
    "changed", ["repository", "base", "work", "head", "binding", "team-name"]
)
async def test_final_preparation_guards_enclose_the_ruling_session(
    prepared, route, changed
):
    if route == "explicit":
        bind(prepared, "explicit")

    async def change():
        if changed == "repository":
            prepared.repository("https://forge.invalid/later/repository")
        elif changed == "base":
            prepared.base(trunk_base("later/base"))
        elif changed == "work":
            prepared.work_ref(WorkRefRole.DELIVERABLE)
        elif changed == "head":
            prepared.git._remote_branch_shas[BASE.base_branch] = "b" * 40
        else:
            key = "repository" if changed == "binding" else "name"
            prepared.operation.teams["board"] = prepared.operation.teams[
                "board"
            ].model_copy(update={key: "foreign"})

    prepared.executor.during_ruling = change
    if route == "explicit" and changed == "repository":
        expected = ScopedExecutionUnavailableError
        message = "publication and loop"
    else:
        expected = TrackerFirePreparationError
        message = "changed during preparation"
    with pytest.raises(expected, match=message):
        await prepared.drive()
    assert len(prepared.executor.calls) == 2
    assert prepared.workspace.calls[-1] == ("release", "/tmp/fake-workspace")
    prepared.no_writes()


@pytest.mark.parametrize("changed", ["approval", "body", "version", "criterion"])
async def test_ruling_cannot_return_after_its_retained_native_source_changes(
    prepared, changed
):
    async def change():
        if changed == "approval":
            prepared.revoke_approval()
        elif changed == "version":
            prepared.server.issues[ISSUE].updated_at += timedelta(seconds=1)
            row = prepared.fake.issues[ISSUE]
            prepared.fake.issues[ISSUE] = row.model_copy(
                update={"updated_at": row.updated_at + timedelta(seconds=1)}
            )
        else:
            key = ISSUE if changed == "body" else TODO
            prepared.body(key, BODY + " amended after capture")

    prepared.executor.during_ruling = change
    with pytest.raises(RulingProposalError):
        await prepared.drive()
    assert len(prepared.executor.calls) == 2
    assert prepared.workspace.calls[-1] == ("release", "/tmp/fake-workspace")
    prepared.no_writes()


async def test_actual_entry_does_not_propose_over_an_infeasible_current_judgment(
    prepared,
):
    from tests.tracker.test_audit_claim import result_event

    prepared.executor._events = [
        result_event(
            subtype="success",
            structured_output={
                "findings": [
                    {
                        "criterionId": TODO,
                        "verdict": "infeasible",
                        "smallestRepair": "criterion_text",
                        "refutation": "The specified conjunction contradicts itself.",
                    }
                ],
            },
        )
    ]
    with pytest.raises(RulingProposalError, match="complete feasible entry judgment"):
        await prepared.drive()
    assert len(prepared.executor.calls) == 1
    prepared.no_writes()


async def test_authored_public_arm_does_not_acquire_native_ruling_dependencies(
    prepared, monkeypatch
):
    class ObservedAuthoredSessionError(Exception):
        pass

    seen = []

    async def first_session(**arguments):
        seen.append(arguments)
        raise ObservedAuthoredSessionError
        yield

    async def forbidden(*args, **kwargs):
        raise AssertionError("authored entry reached native preparation")

    monkeypatch.setattr(prepared.executor, "stream", first_session)
    monkeypatch.setattr(TrackerFeasibilityValidator, "validate", forbidden)
    monkeypatch.setattr(TrackerRulingProposer, "propose_validated", forbidden)
    with pytest.raises(ObservedAuthoredSessionError):
        async for _ in prepared.engine().run(
            prompt="Decoy side channel: authored request.",
            issue_key=ISSUE,
            run_identity=IDENTITY,
            repo_path=None,
            repo_url=REPOSITORY,
            base_spec=BASE,
            scope=None,
            permission_mode="bypassPermissions",
            allowed_tools=["Edit", "Write"],
            cache_key="addressed-cache",
        ):
            pass
    assert len(seen) == 1
    assert "Decoy side channel" in seen[0]["prompt"] and BODY not in seen[0]["prompt"]
    assert seen[0]["output_format"]["schema"] != RULING_PROPOSAL_SCHEMA
    assert not prepared.server.calls
    prepared.no_writes()


@pytest.mark.parametrize(
    "identity",
    [
        IDENTITY.model_copy(update={"name": "foreign"}),
        IDENTITY.model_copy(update={"kind": RunKind.GROOMING}),
    ],
)
def test_native_request_refuses_another_runs_identity(identity):
    with pytest.raises(ValidationError, match="fire identity"):
        TrackerFeasibilityRequest(
            issue_key=ISSUE, repo_url=REPOSITORY, head_sha=HEAD, run_identity=identity
        )
