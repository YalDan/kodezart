"""Native fire sources flow through feasibility into fresh read-only proposals."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from kodezart.chains.rule_open_questions import TrackerRulingProposer
from kodezart.core.constants import EVAL_PERMISSION_MODE, EVAL_TOOLS
from kodezart.core.errors import NoStructuredOutputError
from kodezart.domain.agent import mint_ruling_id
from kodezart.domain.errors import RulingProposalError, ScopeReadError
from kodezart.types.domain.agent import (
    RULING_PROPOSAL_SCHEMA,
    TRACKER_CRITERIA_VALIDATION_SCHEMA,
    RulingAuthor,
    RulingClass,
)
from kodezart.types.domain.session import SessionType
from kodezart.types.domain.subagents import NO_SUBAGENTS
from tests.fakes import SUPPRESS_ALL_SKILLS, FakeMcpIssue, FakeTrackerPort
from tests.prompts.sets import OPUS_SET, V5_SET
from tests.prompts.test_prompt_wiring import load_registry
from tests.tracker import test_tracker_feasibility as source
from tests.tracker.test_audit_claim import result_event


@pytest.fixture
def server():
    value = source.server.__wrapped__()
    value.issues["nested/context"] = FakeMcpIssue(
        id="nested/context",
        parent_id=source.KEYS[0],
        description="Nested native context.",
    )
    return value


setup = source.setup


def answer(**changes):
    return {
        "issueRef": source.KEYS[0],
        "question": "Must this wait indefinitely or stop on the declared bound?",
        "rulingClass": "pin_reading",
        "resolution": "Stop on the declared bound.",
        "rejectedAlternative": "An indefinite wait never terminates the fire.",
        "repoEvidence": ["src/worker.py:wait_for_result"],
        **changes,
    }


class ProposalRunner:
    def __init__(self):
        self.arguments = []
        self.output = {"rulings": [answer()], "unresolvedQuestions": []}
        self.during = None
        self.is_error = False
        self.missing = False

    async def stream_in_workspace(self, **kwargs):
        self.arguments.append(kwargs)
        if self.during:
            await self.during()
        if not self.missing:
            yield result_event(
                subtype="success",
                structured_output=self.output,
                is_error=self.is_error,
            )


@pytest.fixture
async def proposal(setup, tracker):
    build_validator, validation_runner, git, cache, workspace = setup
    runner = ProposalRunner()

    def build(family=V5_SET, **changes):
        return TrackerRulingProposer(
            **{
                "tracker": tracker,
                "validator": build_validator(family),
                "cache": cache,
                "git": git,
                "workspace": workspace,
                "runner": runner,
                "prompts": load_registry(default_set=family),
                "skills": SUPPRESS_ALL_SKILLS,
                **changes,
            }
        )

    return build, runner, validation_runner, git, cache, workspace


@pytest.mark.parametrize("family", [OPUS_SET, V5_SET])
async def test_native_feasibility_then_fresh_read_only_ruling(
    proposal, tracker_writes, server, family, monkeypatch
):
    build, runner, validation, _, _, workspace = proposal
    server.issues["nested/context"] = FakeMcpIssue(
        id="nested/context",
        parent_id=source.KEYS[0],
        description="Nested native context.",
    )
    acquire = AsyncMock(wraps=workspace.acquire)
    monkeypatch.setattr(workspace, "acquire", acquire)
    before = tracker_writes()
    output = await build(family).propose(source.REQUEST)
    assert tracker_writes() == before
    assert len(output.rulings) == 1
    ruling = output.rulings[0]
    assert ruling.ruling_id == mint_ruling_id(
        issue_ref=source.KEYS[0], question=answer()["question"]
    )
    assert ruling.ruling_class is RulingClass.PIN_READING
    assert ruling.authored_by is RulingAuthor.MACHINE
    assert ruling.rejected_alternative == answer()["rejectedAlternative"]
    assert ruling.protected_tests is None
    assert len(validation.arguments) == len(runner.arguments) == 1
    assert validation.arguments[0]["output_format"]["schema"] == (
        TRACKER_CRITERIA_VALIDATION_SCHEMA
    )
    args = runner.arguments[0]
    assert args["output_format"]["schema"] == RULING_PROPOSAL_SCHEMA
    assert args["session_id"] is None
    assert args["session_type"] is SessionType.TICKET_FIRE
    assert args["allowed_tools"] == list(EVAL_TOOLS)
    assert args["permission_mode"] == EVAL_PERMISSION_MODE
    assert args["agents"] == NO_SUBAGENTS
    assert source.BODY in args["prompt"] and source.HEAD in args["prompt"]
    assert all(key in args["prompt"] for key in (*source.KEYS, source.DONE))
    assert "Nested native context." in args["prompt"]
    assert '"criterionId":"condition/café"' in args["prompt"]
    assert acquire.await_count == 2
    assert all(
        call.kwargs["create_branch"] is False for call in acquire.await_args_list
    )
    assert sum(call[0] == "release" for call in workspace.calls) == 2


async def test_repeat_proposal_mints_same_identity_without_tracker_writes(
    proposal, tracker_writes
):
    build, runner, *_ = proposal
    before = tracker_writes()
    first = await build().propose(source.REQUEST)
    runner.output["rulings"][0]["resolution"] = "Amended bounded resolution."
    second = await build().propose(source.REQUEST)
    assert first.rulings[0].ruling_id == second.rulings[0].ruling_id
    assert first.rulings[0].resolution != second.rulings[0].resolution
    assert tracker_writes() == before


@pytest.mark.parametrize("field", ["rulingId", "authoredBy", "protectedTests"])
async def test_session_cannot_supply_harness_owned_record_fields(proposal, field):
    build, runner, *_ = proposal
    runner.output["rulings"][0][field] = "fabricated"
    with pytest.raises(ValidationError):
        await build().propose(source.REQUEST)


@pytest.mark.parametrize("kind", ["foreign", "duplicate", "unresolved", "loser"])
async def test_unrecordable_answer_never_becomes_a_valid_proposal(proposal, kind):
    build, runner, *_ = proposal
    if kind == "foreign":
        runner.output["rulings"][0]["issueRef"] = "another/fire"
    elif kind == "duplicate":
        runner.output["rulings"].append(answer(resolution="Conflicting answer."))
    elif kind == "unresolved":
        runner.output["unresolvedQuestions"] = ["Requires an undeclared subsystem."]
    else:
        runner.output["rulings"][0]["rejectedAlternative"] = None
    with pytest.raises((RulingProposalError, ValidationError)):
        await build().propose(source.REQUEST)


@pytest.mark.parametrize("mode", ["empty", "none", "error"])
async def test_missing_or_error_session_output_refuses_and_releases(proposal, mode):
    build, runner, _, _, _, workspace = proposal
    if mode == "empty":
        runner.missing = True
    elif mode == "none":
        runner.output = None
    else:
        runner.is_error = True
    with pytest.raises(NoStructuredOutputError):
        await build().propose(source.REQUEST)
    assert workspace.calls[-1][0] == "release"


async def test_explicit_empty_proposals_remain_read_only(proposal, tracker_writes):
    build, runner, *_ = proposal
    runner.output["rulings"] = []
    before = tracker_writes()
    assert not (await build().propose(source.REQUEST)).rulings
    assert tracker_writes() == before


@pytest.mark.parametrize("kind", ["subject", "criterion", "nested", "membership"])
async def test_changed_native_source_during_proposal_refuses(
    proposal, server, tracker, kind
):
    build, runner, *_ = proposal

    async def change():
        if kind == "subject":
            await tracker.update_issue(
                issue_key=source.SUBJECT, body="Changed subject."
            )
        elif kind == "criterion":
            await tracker.update_issue(
                issue_key=source.KEYS[0], body="**Check:** Changed native Check."
            )
        elif kind == "nested":
            await tracker.update_issue(
                issue_key="nested/context", body="Changed descendant."
            )
        else:
            if isinstance(tracker, FakeTrackerPort):
                tracker.issues["new/member"] = tracker.issues[
                    "nested/context"
                ].model_copy(
                    update={"issue_key": "new/member", "parent_key": source.SUBJECT}
                )
            else:
                server.issues["new/member"] = FakeMcpIssue(
                    id="new/member",
                    parent_id=source.SUBJECT,
                    description="New context.",
                )

    runner.during = change
    with pytest.raises(RulingProposalError):
        await build().propose(source.REQUEST)


@pytest.mark.parametrize("mode", ["foreign", "duplicate", "detached", "missing"])
async def test_incomplete_or_foreign_subtree_refuses_before_session(
    proposal, tracker, monkeypatch, mode
):
    build, runner, *_ = proposal
    read = tracker.scope_issues

    async def changed(**kwargs):
        rows = list(await read(**kwargs))
        if mode == "missing":
            return [row for row in rows if row.issue_key != source.SUBJECT]
        if mode == "duplicate":
            return [*rows, rows[0]]
        child = next(row for row in rows if row.issue_key != source.SUBJECT)
        updates = (
            {"issue_key": "foreign", "parent_key": "another/root"}
            if mode == "foreign"
            else {"parent_key": None}
        )
        return [*rows, child.model_copy(update=updates)]

    monkeypatch.setattr(tracker, "scope_issues", changed)
    with pytest.raises((RulingProposalError, ScopeReadError)):
        await build().propose(source.REQUEST)
    assert not runner.arguments


@pytest.mark.parametrize("change", ["sha", "dirty", "replacements"])
async def test_mutated_workspace_refuses_before_return(proposal, change, monkeypatch):
    build, runner, _, git, _, _ = proposal

    async def mutate():
        if change == "sha":
            monkeypatch.setattr(git, "current_sha", AsyncMock(return_value="b" * 40))
        elif change == "dirty":
            git.has_changes_result = True
        else:
            git.has_replace_refs_result = True

    runner.during = mutate
    with pytest.raises(RulingProposalError):
        await build().propose(source.REQUEST)


async def test_invalid_commit_is_rejected_before_validation(proposal):
    build, runner, validation, *_ = proposal
    with pytest.raises(RulingProposalError, match="complete commit"):
        await build().propose(source.REQUEST.model_copy(update={"head_sha": "abc123"}))
    assert not runner.arguments and not validation.arguments


async def test_cancellation_in_session_releases_without_returning_proposals(proposal):
    build, runner, _, _, _, workspace = proposal
    started = asyncio.Event()

    async def wait():
        started.set()
        await asyncio.Event().wait()

    runner.during = wait
    task = asyncio.create_task(build().propose(source.REQUEST))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert workspace.calls[-1][0] == "release"


@pytest.mark.parametrize(
    "change", ["head", "subject", "missing", "duplicate", "contradiction"]
)
async def test_inconsistent_feasibility_cannot_start_ruling(proposal, setup, change):
    from kodezart.types.domain.criteria import TrackerContradiction

    build, runner, *_ = proposal
    build_validator, *_ = setup
    observed = await build_validator().validate(source.REQUEST)
    if change == "head":
        observed = observed.model_copy(update={"head_sha": "b" * 40})
    elif change == "subject":
        observed = observed.model_copy(
            update={"spec": observed.spec.model_copy(update={"subject": "foreign"})}
        )
    else:
        judgment = observed.judgment
        assert judgment is not None
        if change == "missing":
            judgment = judgment.model_copy(update={"findings": judgment.findings[:1]})
        elif change == "duplicate":
            judgment = judgment.model_copy(update={"findings": judgment.findings * 2})
        else:
            judgment = judgment.model_copy(
                update={
                    "contradictions": [
                        TrackerContradiction(
                            criterion_ids=source.KEYS,
                            explanation="The full current conjunction cannot stand.",
                        )
                    ]
                }
            )
        observed = observed.model_copy(update={"judgment": judgment})

    class Inconsistent:
        async def validate(self, request):
            assert request == source.REQUEST
            return observed

    with pytest.raises(RulingProposalError):
        await build(validator=Inconsistent()).propose(source.REQUEST)
    assert not runner.arguments


@pytest.mark.parametrize(
    ("verdict", "repair", "evidence"),
    [
        (
            "infeasible",
            "criterion_text",
            {"refutation": "The named switch lacks this arm."},
        ),
        ("unverifiable", "environment_supply", {"missingResource": "The database."}),
    ],
)
async def test_nonfeasible_current_criteria_do_not_reach_ruling(
    proposal, verdict, repair, evidence
):
    build, runner, validation, *_ = proposal
    validation.answers = [
        source.output(
            findings=[
                source.finding(key, verdict=verdict, smallestRepair=repair, **evidence)
                for key in source.KEYS
            ]
        )
    ]
    with pytest.raises(RulingProposalError, match="feasible entry"):
        await build().propose(source.REQUEST)
    assert not runner.arguments


async def test_source_change_after_feasibility_refuses_before_proposal(
    proposal, setup, tracker
):
    build, runner, *_ = proposal
    build_validator, *_ = setup
    validator = build_validator()

    class Changed:
        async def validate(self, request):
            observation = await validator.validate(request)
            await tracker.update_issue(
                issue_key=source.SUBJECT, body="New specification."
            )
            return observation

    with pytest.raises(RulingProposalError):
        await build(validator=Changed()).propose(source.REQUEST)
    assert not runner.arguments
