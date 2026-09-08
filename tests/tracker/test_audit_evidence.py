"""Actual tracker grading reads feed lapse or the existing fresh claim verifier."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from kodezart.chains.audit_evidence import AuditEvidenceVerifier
from kodezart.core.config import AppConfig
from kodezart.domain.criterion_evidence import render_evidence_field
from kodezart.domain.errors import AuditEvidenceReadError
from kodezart.domain.lane_record import render_lane_record
from kodezart.services.lane_records import LaneRecordReader
from kodezart.types.domain.audit import AuditVerdict
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.run_state import LaneRunState
from tests.domain.test_lane_record import record_data
from tests.fakes import FakeMcpIssue
from tests.tracker import test_audit_claim as claim_fixtures
from tests.tracker.conftest import STATE_TYPES, WORKFLOW_STATE_NAMES, fixture_server
from tests.tracker.test_audit_claim import (
    CHECK,
    CHILD,
    HEAD,
    PREFIXES,
    REQUEST,
    ROOT,
    result_event,
)

claim_setup = claim_fixtures.setup

PRIOR = "b" * 40
TEST = "recorded_test_only_not_session_input"
OPERATION = OperationConfig(
    operation_name="fixture",
    workspace="fixture",
    marker_prefixes=PREFIXES,
    workflow_states=WORKFLOW_STATE_NAMES,
)


@pytest.fixture
def server():
    workspace = fixture_server()
    workspace.issues[ROOT] = FakeMcpIssue(id=ROOT)
    workspace.issues[CHILD] = FakeMcpIssue(
        id=CHILD, parent_id=ROOT, labels=["acceptance-condition"]
    )
    for name, kind in STATE_TYPES.items():
        key = f"configured-state/{kind}/{name}"
        workspace.issues[key] = FakeMcpIssue(id=key, status=name, status_type=kind)
    workspace.issues["configured-review"] = FakeMcpIssue(
        id="configured-review", status="Prüfen", status_type="started"
    )
    workspace.state_types["Prüfen"] = "started"
    return workspace


def body(sha=PRIOR):
    return f"**Check:** {CHECK}\n**Do:** AUTHOR_REASONING\n" + render_evidence_field(
        CriterionEvidence(graded_sha=sha, test=TEST)
    )


class Source:
    def __init__(self):
        self.calls = []
        self.during = None
        self.changed = None

    async def resolve_commit(self, *, cwd, ref):
        self.calls.append((cwd, ref))
        if self.during:
            await self.during()
        return self.changed or ref

    async def read_source(self, **_kwargs):
        raise AssertionError("this reader does not grade recorded test prose")


@pytest.fixture
async def setup(claim_setup, tracker):
    claim_build, runner, git, cache, workspace, stored = claim_setup
    await tracker.update_issue(issue_key=CHILD, body=body())
    await tracker.restore_workflow_state(issue_key=CHILD, state_name="Done")
    git._ancestor_pairs.update({(PRIOR, HEAD), (HEAD, HEAD)})
    source = Source()
    claims = claim_build()

    def build(**changes):
        values = {
            "tracker": tracker,
            "records": LaneRecordReader(tracker=tracker, operation=OPERATION),
            "git": git,
            "source": source,
            "cache": cache,
            "claims": claims,
            "operation": OPERATION,
            "remote": AppConfig(git_remote="configured-remote").git_remote,
        }
        return AuditEvidenceVerifier(**{**values, **changes})

    return build, runner, git, source, cache, workspace, stored, claims


async def test_the_same_criterion_lapses_then_is_reverified_at_head(
    setup, tracker, tracker_writes
):
    build, runner, git, source, _, workspace, stored, _ = setup
    writes = tracker_writes()
    lapsed = await build().observe(REQUEST)
    assert lapsed.is_lapse and lapsed.verdict is AuditVerdict.UNVERIFIABLE
    assert lapsed.criterion.issue_key == CHILD
    assert lapsed.recorded_evidence.graded_sha == PRIOR and lapsed.head_sha == HEAD
    assert lapsed.record_ref == stored.comment_key and lapsed.current_claim is None
    assert not runner.calls and not workspace.calls
    assert {call[2] for call in git.calls if call[0] == "remote_branch_sha"} == {
        "configured-remote"
    }
    assert source.calls == [("/tmp/fake-cache", HEAD), ("/tmp/fake-cache", PRIOR)]
    assert ("is_ancestor", "/tmp/fake-cache", PRIOR, HEAD) in git.calls
    assert tracker_writes() == writes

    await tracker.restore_workflow_state(issue_key=CHILD, state_name="In Review")
    writes = tracker_writes()
    runner._events = [
        result_event(
            subtype="success",
            structured_output={
                "criterionKey": CHILD,
                "verdict": "refuted",
                "evidence": "Fresh named check failed at head.",
            },
        )
    ]
    failed = await build().observe(REQUEST)
    assert not failed.is_lapse and failed.verdict is AuditVerdict.REFUTED
    assert failed.current_claim.head_sha == HEAD
    assert failed.recorded_evidence.graded_sha == PRIOR
    assert runner.arguments["session_id"] is None
    assert CHECK in runner.arguments["prompt"]
    assert TEST not in runner.arguments["prompt"]
    assert PRIOR not in runner.arguments["prompt"]
    assert "AUTHOR_REASONING" not in runner.arguments["prompt"]
    assert tracker_writes() == writes


@pytest.mark.parametrize("verdict", list(AuditVerdict))
async def test_current_completed_claim_uses_the_same_fresh_verifier(
    setup, tracker, verdict
):
    build, runner, *_ = setup
    await tracker.update_issue(issue_key=CHILD, body=body(HEAD))
    runner._events = [
        result_event(
            subtype="success",
            structured_output={
                "criterionKey": CHILD,
                "verdict": verdict.value,
                "evidence": "Fresh result or named missing resource.",
            },
        )
    ]
    value = await build().observe(REQUEST)
    assert value.verdict is verdict and not value.is_lapse
    assert value.current_claim is not None


@pytest.mark.parametrize("state", ["Todo", "Backlog", "In Progress", "Canceled"])
async def test_other_states_do_not_enter_the_recorded_grading_reader(
    setup, tracker, state
):
    build, runner, git, source, cache, workspace, *_ = setup
    await tracker.restore_workflow_state(issue_key=CHILD, state_name=state)
    with pytest.raises(AuditEvidenceReadError, match="review claim"):
        await build().observe(REQUEST)
    assert source.calls == cache.calls == workspace.calls == runner.calls == []
    assert git.calls == []


@pytest.mark.parametrize("damage", ["source", "membership", "record", "head"])
async def test_lapse_read_refuses_a_changed_source_instead_of_returning_a_stale_claim(
    setup, tracker, monkeypatch, damage
):
    build, _, git, source, _, _, stored, _ = setup

    async def change():
        source.during = None
        if damage == "source":
            await tracker.update_issue(issue_key=CHILD, body=body(HEAD))
        elif damage == "membership":
            original = tracker.read_criteria

            async def moved(**kwargs):
                rows = list(await original(**kwargs))
                rows[0] = rows[0].model_copy(update={"parent_key": "another"})
                return rows

            monkeypatch.setattr(tracker, "read_criteria", moved)
        elif damage == "record":
            changed = await tracker.upsert_comment(
                target=ROOT,
                marker=stored.body.splitlines()[0],
                body="record disappeared",
            )
            assert changed.comment_key == stored.comment_key
            assert changed.body != stored.body
        else:
            monkeypatch.setattr(git, "remote_branch_sha", AsyncMock(return_value=PRIOR))

    source.during = change
    with pytest.raises(AuditEvidenceReadError):
        await build().observe(REQUEST)


@pytest.mark.parametrize("damage", ["off-branch", "wrong-object", "absent-head"])
async def test_unreadable_git_identity_is_never_a_lapse(setup, monkeypatch, damage):
    build, runner, git, source, _, workspace, *_ = setup
    if damage == "off-branch":
        git._ancestor_pairs.clear()
    elif damage == "wrong-object":
        source.changed = "c" * 40
    else:
        monkeypatch.setattr(git, "remote_branch_sha", AsyncMock(return_value=None))
    with pytest.raises(AuditEvidenceReadError) as raised:
        await build().observe(REQUEST)
    assert raised.value.criterion_key == CHILD
    assert not runner.calls and not workspace.calls


async def test_a_valid_replacement_record_cannot_validate_the_earlier_snapshot(
    setup, tracker
):
    build, _, _, source, _, _, stored, _ = setup
    replacement = LaneRunState.model_validate({**record_data(), "filesChanged": 17})
    marker, payload = render_lane_record(
        record=replacement, marker_prefixes=PREFIXES
    ).split("\n", 1)

    async def change():
        source.during = None
        changed = await tracker.upsert_comment(target=ROOT, marker=marker, body=payload)
        assert changed.comment_key == stored.comment_key
        _, parsed = await LaneRecordReader(tracker=tracker, operation=OPERATION).read(
            issue_key=ROOT, lane_key=REQUEST.lane_key, record_ref=stored.comment_key
        )
        assert parsed == replacement

    source.during = change
    with pytest.raises(AuditEvidenceReadError, match="lane record changed"):
        await build().observe(REQUEST)


async def test_review_claim_does_not_require_old_grade_to_survive_history_rewrite(
    setup, tracker, monkeypatch
):
    build, _, git, source, *_ = setup
    await tracker.restore_workflow_state(issue_key=CHILD, state_name="In Review")
    git._ancestor_pairs.clear()
    value = await build().observe(REQUEST)
    assert value.verdict is AuditVerdict.HOLDS
    assert source.calls == [("/tmp/fake-cache", HEAD)]


@pytest.mark.parametrize("selected", ["Prüfen", "In Review"])
async def test_review_state_is_resolved_from_configuration(setup, tracker, selected):
    build, *_ = setup
    operation = OPERATION.model_copy(
        update={"workflow_states": {**WORKFLOW_STATE_NAMES, "in_review": "Prüfen"}}
    )
    await tracker.restore_workflow_state(issue_key=CHILD, state_name=selected)
    if selected == "Prüfen":
        assert (await build(operation=operation).observe(REQUEST)).current_claim
    else:
        with pytest.raises(AuditEvidenceReadError, match="review claim"):
            await build(operation=operation).observe(REQUEST)


@pytest.mark.parametrize("damage", ["missing", "duplicate", "parent", "label"])
async def test_ambiguous_initial_membership_is_refused_before_git(
    setup, tracker, monkeypatch, damage
):
    build, runner, git, source, cache, workspace, *_ = setup
    rows = list(await tracker.read_criteria(issue_key=ROOT))
    if damage == "missing":
        rows.clear()
    elif damage == "duplicate":
        rows.extend(rows)
    else:
        field, value = (
            ("parent_key", "foreign") if damage == "parent" else ("issue_labels", ())
        )
        rows[0] = rows[0].model_copy(update={field: value})
    monkeypatch.setattr(tracker, "read_criteria", AsyncMock(return_value=rows))
    with pytest.raises(AuditEvidenceReadError):
        await build().observe(REQUEST)
    assert source.calls == cache.calls == workspace.calls == runner.calls == []
    assert git.calls == []


async def test_legacy_prose_does_not_supply_a_convenient_sha(setup, tracker):
    build, runner, git, source, cache, workspace, *_ = setup
    legacy = f"**Check:** {CHECK}\n**Evidence:** {PRIOR} passed; {HEAD} also green"
    await tracker.update_issue(issue_key=CHILD, body=legacy)
    with pytest.raises(AuditEvidenceReadError, match="explicit fenced JSON"):
        await build().observe(REQUEST)
    assert (await tracker.read_issue(issue_key=CHILD)).body == legacy
    assert source.calls == cache.calls == workspace.calls == runner.calls == []
    assert git.calls == []


@pytest.mark.parametrize("field,value", [("check", "Other Check"), ("head_sha", PRIOR)])
async def test_current_claim_cannot_be_attached_to_another_source(
    setup, tracker, monkeypatch, field, value
):
    build, _, _, _, _, _, _, claims = setup
    await tracker.update_issue(issue_key=CHILD, body=body(HEAD))
    original = claims.verify

    async def foreign(request):
        observed = await original(request)
        return observed.model_copy(update={field: value})

    monkeypatch.setattr(claims, "verify", foreign)
    with pytest.raises(AuditEvidenceReadError):
        await build().observe(REQUEST)


async def test_lapse_payload_is_frozen_and_cannot_be_restamped_as_holds(setup):
    build, *_ = setup
    observed = await build().observe(REQUEST)
    assert type(observed).model_validate_json(observed.model_dump_json()) == observed
    with pytest.raises(ValidationError, match="frozen_instance"):
        observed.head_sha = PRIOR
    with pytest.raises(ValidationError, match="unverifiable"):
        type(observed).model_validate({**observed.model_dump(), "verdict": "holds"})


@pytest.mark.parametrize("phase", ["fetch", "is_ancestor", "remote_branch_sha"])
async def test_cancelled_repository_observation_settles_before_returning(
    setup, monkeypatch, phase
):
    build, _, git, _, *_ = setup
    started, finish = asyncio.Event(), asyncio.Event()
    original = getattr(git, phase)
    settled = []

    async def blocked(*args):
        started.set()
        await finish.wait()
        settled.append(True)
        return await original(*args)

    monkeypatch.setattr(git, phase, blocked)
    task = asyncio.create_task(build().observe(REQUEST))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and settled == []
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert settled
