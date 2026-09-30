"""Actual precommit graph, canonical write-back and Git publication ordering."""

import json
from pathlib import Path

import pytest

from kodezart.core.errors import TrackerUnavailableError
from kodezart.domain import gap
from kodezart.domain.amendment import (
    AmendmentWriteBackRefusalError,
    AssertionWeakenedError,
)
from kodezart.domain.assertion_drift import protected_assertions, weakening_mark
from kodezart.domain.criterion_cross_off import (
    evaluation_observation,
    lapse_observation,
)
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import SurfaceLeaseError
from kodezart.domain.fire_spec import criterion_field_bodies
from kodezart.domain.git_url import resolve_repo_url
from kodezart.domain.issue_tree import SubtreeClosure
from kodezart.domain.model_surfaces import MODEL_CLASSIFICATION
from kodezart.domain.rulings import render_ruling
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.types.domain.agent import (
    NativeAmendmentEvent,
    ResultEvent,
    Ruling,
    RulingProtectedTestRef,
)
from kodezart.types.domain.amendment import AmendmentGround, UpheldReason
from kodezart.types.domain.amendment_write import AmendmentRecord
from kodezart.types.domain.assertion_drift import AssertionDeviationClaim
from kodezart.types.domain.operation import CheckPrerequisite, OperationConfig
from kodezart.types.domain.run_state import LaneEscalation
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import WorkflowStateKind
from kodezart.types.domain.tracker_writes import DescriptionEditResult
from tests.chains.test_native_fire import (
    DIRECT_DONE,
    DIRECT_OWED,
    NESTED_DONE,
    SUBJECT,
    tracker,
)
from tests.domain.test_rulings import ruling_data
from tests.services.test_lane_state_writer import lane_repo, lapse, writer
from tests.services.test_native_amendments import (
    AMENDED_CHECK,
    GIT_BASE_URL,
    PROTECTED_BODY,
    PROTECTED_NAME,
    PROTECTED_PATH,
    REPO_URL,
    UNVERIFIABLE_HERE,
    WEAKENED_BODY,
    Executor,
    build,
    cleanup,
    drive,
    git,
    pinned_designation,
    repository,
)

__all__ = ["repository"]

#: A criterion title carrying the AC token an amendment must not disturb.
TITLE = "KOD-97-AC-3 the amended criterion keeps its identity"


async def test_path_only_native_amendment_uses_real_separate_base_worktrees(repository):
    executor = Executor(reproduced=True)
    service, guard, workspace, _ = await build(repository, executor, repo_url=None)
    try:
        events = await drive(service, guard, repository)
        assert any(isinstance(e, ResultEvent) and e.commit_sha for e in events)
        writer_path = workspace.acquired[0][0]
        assert len(workspace.acquired) == 5
        assert len({path for path, _ in workspace.acquired}) == 5
        for path, arguments in workspace.acquired[1:]:
            assert path != writer_path
            assert arguments["repo_path"] == writer_path
            assert arguments["repo_url"] is None
            assert arguments["ref"] == repository[1]
            assert arguments["create_branch"] is False
        assert {path for path, _ in workspace.acquired} == set(workspace.released)
    finally:
        await cleanup(workspace)


async def test_done_criterion_archives_exact_evidence_then_resets_before_new_check(
    repository, monkeypatch
):
    port = tracker()
    original = port.issues[DIRECT_DONE]
    original = original.model_copy(
        update={"body": original.body + "\n**Class:** observed\n"}
    )
    port.issues[DIRECT_DONE] = original
    order = []
    edit = port.edit_description
    reset = port.reset_criterion_pending

    async def edited(**kwargs):
        stored = port.issues[DIRECT_DONE]
        records = [c for c in port.comments if c.body.startswith("[fixture-amendment:")]
        assert len(records) == 1
        archived = AmendmentRecord.model_validate_json(
            records[0].body.partition("\n")[2]
        )
        assert json.loads(archived.prior.content)[0]["body"] == original.body
        if "the amended observable Check" in kwargs["replacement"]:
            assert stored.state_kind is WorkflowStateKind.UNSTARTED
            order.append("new_check")
        else:
            assert stored.state_kind is WorkflowStateKind.COMPLETED
            order.append("clear_evidence")
        return await edit(**kwargs)

    async def reset_observed(**kwargs):
        stored = port.issues[DIRECT_DONE]
        assert criterion_field_bodies(stored.body, field="Evidence") == ("",)
        assert criterion_field_bodies(stored.body, field="Class") == ("",)
        assert criterion_field_bodies(
            stored.body, field="Check"
        ) == criterion_field_bodies(original.body, field="Check")
        order.append("reset")
        return await reset(**kwargs)

    monkeypatch.setattr(port, "edit_description", edited)
    monkeypatch.setattr(port, "reset_criterion_pending", reset_observed)

    async def observe(title, payload, kwargs):
        if title == "CommitMessageOutput":
            assert order == ["clear_evidence", "reset", "new_check"]
            assert port.issues[DIRECT_DONE].state_kind is WorkflowStateKind.UNSTARTED
            order.append("commit_message")

    executor = Executor(
        reproduced=True,
        subject={"kind": "criterion", "id": DIRECT_DONE},
        mutate=observe,
    )
    service, guard, workspace, _ = await build(repository, executor, port=port)
    try:
        events = await drive(service, guard, repository)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        assert report.verdicts[0].subject.id == DIRECT_DONE
        assert report.verdicts[0].verdict == "amended"
        assert order[-1] == "commit_message"
        assert any(isinstance(e, ResultEvent) and e.commit_sha for e in events)
    finally:
        await cleanup(workspace)


async def test_the_criterion_state_move_takes_one_lease_on_its_own_sub_issue(
    repository, monkeypatch
):
    """What the read declined to take, the move that writes it takes once.

    The criterion sub-issue is the leased surface here, so the state move
    back to pending is covered by exactly one grant over that criterion,
    and the move presents that grant's holder: a move made with no holder
    would pass a board that checks grants only when one is presented. On
    this unmarked board no other criterion is held while it runs; a marked
    model holds every member's surface, which is another board's reading.
    """
    port = tracker()
    presented: list[str | None] = []
    reset = port.reset_criterion_pending

    async def reset_presenting(**kwargs):
        presented.append(kwargs["holder"])
        return await reset(**kwargs)

    monkeypatch.setattr(port, "reset_criterion_pending", reset_presenting)
    executor = Executor(
        reproduced=True, subject={"kind": "criterion", "id": DIRECT_DONE}
    )
    service, guard, workspace, _ = await build(repository, executor, port=port)
    try:
        events = await drive(service, guard, repository)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        assert report.verdicts[0].verdict == "amended"
        assert port.issues[DIRECT_DONE].state_kind is WorkflowStateKind.UNSTARTED
        assert [
            surface.ref.key
            for lease in port.lease_acquisitions
            for surface in lease.surfaces
            if surface.kind is SurfaceKind.CRITERION_SUB_ISSUE
        ] == [DIRECT_DONE]
        assert presented == [port.lease_acquisitions[0].holder]
    finally:
        await cleanup(workspace)


async def test_an_amended_criterion_keeps_its_key_and_title_token_before_and_after(
    repository,
):
    """Identity is the sub-issue key, and the AC token in the title survives with it.

    Editing a description cannot re-key a sub-issue, so the key and the title are
    read back from the independently verified prior and applied artifacts and from
    the board, identical, while the body is asserted to have changed.
    """
    port = tracker()
    port.issues[DIRECT_OWED] = port.issues[DIRECT_OWED].model_copy(
        update={"title": TITLE}
    )
    keys_before = set(port.issues)
    executor = Executor(reproduced=True)
    service, guard, workspace, _ = await build(repository, executor, port=port)
    try:
        events = await drive(service, guard, repository)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        amended = report.verdicts[0]
        assert amended.verdict == "amended"
        prior_row = json.loads(amended.prior.content)[0]
        applied_row = json.loads(amended.applied.artifact.content)[0]
        identity = (DIRECT_OWED, TITLE)
        assert (prior_row["issue_key"], prior_row["title"]) == identity
        assert (applied_row["issue_key"], applied_row["title"]) == identity
        assert amended.prior.native_ref == amended.applied.artifact.native_ref
        assert amended.prior.native_ref == DIRECT_OWED
        assert prior_row["body"] != applied_row["body"]
        assert AMENDED_CHECK in applied_row["body"]
        settled = port.issues[DIRECT_OWED]
        assert (settled.issue_key, settled.title) == identity
        assert set(port.issues) == keys_before
    finally:
        await cleanup(workspace)


async def test_a_replayed_amendment_leaves_the_criterion_sub_issue_byte_identical(
    repository, monkeypatch
):
    """The same claim, judged again, edits the sub-issue to the bytes it already has.

    A second historical record of the refusal is accepted: this is recovery, not an
    exactly-once transaction, so the archive count is pinned rather than forbidden.
    """
    port = tracker()
    service, guard, workspace, _ = await build(
        repository, Executor(reproduced=True), port=port
    )
    try:
        await drive(service, guard, repository)
    finally:
        await cleanup(workspace)
    settled = port.issues[DIRECT_OWED]
    first_archives = [
        c for c in port.comments if c.body.startswith("[fixture-amendment:")
    ]
    assert AMENDED_CHECK in settled.body
    assert len(first_archives) == 1

    results = []
    edit = port.edit_description

    async def recorded(**kwargs):
        results.append(await edit(**kwargs))
        return results[-1]

    monkeypatch.setattr(port, "edit_description", recorded)
    service, replay_guard, workspace, _ = await build(
        repository,
        Executor(reproduced=True),
        port=port,
        frozen_spec=guard._spec,
    )
    try:
        events = await drive(service, replay_guard, repository, resume=True)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        replay = report.verdicts[0]
        assert replay.verdict == "amended"
        assert json.loads(replay.prior.content)[0]["body"] == settled.body
        assert port.issues[DIRECT_OWED].body == settled.body
        assert port.issues[DIRECT_OWED].model_dump(
            exclude={"updated_at"}
        ) == settled.model_dump(exclude={"updated_at"})
        assert results == [
            DescriptionEditResult.UNCHANGED,
            DescriptionEditResult.UNCHANGED,
        ]
        archives = [
            c for c in port.comments if c.body.startswith("[fixture-amendment:")
        ]
        assert archives[0] == first_archives[0]
        assert len(archives) == 2
        # "The same claim" read back off the records, not built by the fixture:
        # a replay of some other claim converging on the same text is not this.
        first_record, replay_record = [
            AmendmentRecord.model_validate_json(c.body.partition("\n")[2])
            for c in archives
        ]
        assert (replay_record.claim, replay_record.judgment) == (
            first_record.claim,
            first_record.judgment,
        )
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize("exhaust", [False, True])
async def test_applied_writeback_repairs_within_bound_without_rejudging_ground(
    repository, exhaust
):
    rounds = 0
    authors = 0

    async def observe(title, payload, kwargs):
        nonlocal rounds, authors
        if title == "WriteBackFinding":
            rounds += 1
            if rounds == 2 or (exhaust and rounds > 2):
                payload.update(
                    verdict="refuted",
                    evidence="The landed Check still omits the required boundary.",
                    cited_refs=["policy.py"],
                )
        elif title == "AmendmentTextOutput":
            authors += 1
            if authors == 2:
                assert (
                    "The landed Check still omits the required boundary."
                    in kwargs["prompt"]
                )
                payload["replacement"]["check"] = "the repaired observable Check"

    executor = Executor(reproduced=True, mutate=observe)
    service, guard, workspace, port = await build(repository, executor)
    prior = port.issues[DIRECT_OWED].body
    try:
        if exhaust:
            with pytest.raises(AmendmentWriteBackRefusalError) as failure:
                await drive(service, guard, repository)
            assert len(failure.value.result.rounds) == 2
            assert (
                failure.value.result.rounds[-1].evidence
                == "The landed Check still omits the required boundary."
            )
            assert (
                await git(
                    repository[0], "ls-remote", "origin", "refs/heads/native-test"
                )
                == ""
            )
        else:
            events = await drive(service, guard, repository)
            report = next(
                e.report for e in events if isinstance(e, NativeAmendmentEvent)
            )
            applied = report.verdicts[0]
            assert applied.verdict == "amended"
            assert [r.verdict.value for r in applied.applied.rounds] == [
                "refuted",
                "holds",
            ]
            assert json.loads(applied.prior.content)[0]["body"] == prior
        assert authors == 2
        assert rounds == 3
        assert (
            sum(
                c["output_format"]["schema"]["title"] == "AmendmentJudgment"
                for c in executor.calls
            )
            == 1
        )
    finally:
        await cleanup(workspace)


#: The occurrence key is the last component of an amendment or escalation marker.
def occurrence_of(marker):
    return marker[1:-1].rsplit(":", 1)[1]


def escalations_on(port, key):
    """Every escalation comment this operation's prefix owns on *key*."""
    return [
        comment
        for comment in port.comments
        if comment.issue_key == key and comment.body.startswith("[fixture-escalation:")
    ]


async def test_undemonstrable_refusal_escalates_once_and_a_replay_writes_nothing_new(
    repository,
):
    """One escalation per refusal occurrence, on the criterion's own sub-issue.

    The escalation takes the write the measured uneconomic refusal already takes:
    the same marker keyed on the refusal occurrence, on the same sub-issue the
    archive lands on, and the same `decision` classification after it. What it
    carries is its own question, composed from the claim and the judgment.

    The replay is a replay of the same refusal occurrence: the crash window
    between the escalation comment and the classification. The occurrence is
    computed over the sub-issue row, and the classification enters that row, so
    restore the row to what it was when the occurrence was computed, judge the
    same claim again, and the escalation resolves to the comment already posted
    while the classification completes.
    """
    port = tracker()
    executor = Executor(
        reproduced=True,
        claimed_capability="network",
        finding=UNVERIFIABLE_HERE,
    )
    service, guard, workspace, _ = await build(
        repository,
        executor,
        port=port,
        runner_environment={CheckPrerequisite.NETWORK: False},
    )
    try:
        events = await drive(service, guard, repository)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        refusal = report.upheld[0]
        assert refusal.reason is UpheldReason.ENVIRONMENT_LACKS_CAPABILITY
        assert refusal.publication.kind == "escalated"
        escalation_artifact = refusal.publication.escalation.artifact
        assert escalation_artifact.surface.ref.key == DIRECT_OWED
        # The escalation's evidence is the escalation write itself, not the
        # archive record standing in for it.
        assert escalation_artifact != refusal.publication.record.artifact
        assert escalation_artifact.content.startswith("[fixture-escalation:")
        # One escalation for this occurrence, keyed on it: the archive marker
        # and the escalation marker name the same refusal.
        raised = escalations_on(port, DIRECT_OWED)
        assert len(raised) == 1
        # Exactly one across the board, not only on the refused criterion.
        assert [
            c for c in port.comments if c.body.startswith("[fixture-escalation:")
        ] == raised
        archives = [
            c
            for c in port.comments
            if c.issue_key == DIRECT_OWED and c.body.startswith("[fixture-amendment:")
        ]
        assert len(archives) == 1
        occurrence = occurrence_of(archives[0].body.partition("\n")[0])
        posted = LaneEscalation.model_validate_json(raised[0].body.partition("\n")[2])
        assert posted.escalation_key == occurrence
        assert occurrence_of(raised[0].body.partition("\n")[0]) == occurrence
        assert posted.issue_id == DIRECT_OWED
        assert posted.raised_at_sha == repository[1]
        assert "decision" in port.issues[DIRECT_OWED].issue_labels
        assert port.classification_writes == [(DIRECT_OWED, "decision")]
        # The question, read off the board rather than off the verdict: the
        # capability, what the demonstration lacks, what would revive the
        # criterion, and the alternative open to a person.
        assert (
            f"missing capability {CheckPrerequisite.NETWORK.value} for {DIRECT_OWED}"
            in posted.question
        )
        assert UNVERIFIABLE_HERE["missing_resource"] in posted.question
        assert "runner environment" in posted.question
        assert "removing the decision classification" in posted.question
        assert "revives" in posted.question
        assert "supersession" in posted.question
        assert [c["output_format"]["schema"]["title"] for c in executor.calls] == [
            "NativeWriterOutput",
            "AmendmentJudgment",
            "WriteBackFinding",
            "WriteBackFinding",
        ]
        assert not any(isinstance(e, ResultEvent) for e in events)
    finally:
        await cleanup(workspace)

    # The crash window: the escalation comment landed, the classification had
    # not. Restoring the row restores the occurrence the next fire computes.
    escalated = port.issues[DIRECT_OWED]
    port.issues[DIRECT_OWED] = escalated.model_copy(
        update={"issue_labels": escalated.issue_labels - {"decision"}}
    )
    comments_before = [(c.comment_key, c.body) for c in port.comments]
    writes_before = list(port.comment_writes)
    service, replay_guard, workspace, _ = await build(
        repository,
        Executor(
            reproduced=True,
            claimed_capability="network",
            finding=UNVERIFIABLE_HERE,
        ),
        port=port,
        frozen_spec=guard._spec,
        runner_environment={CheckPrerequisite.NETWORK: False},
    )
    try:
        events = await drive(service, replay_guard, repository, resume=True)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        replay = report.upheld[0]
        assert replay.publication.kind == "escalated"
        assert [(c.comment_key, c.body) for c in port.comments] == comments_before
        assert port.comment_writes == writes_before
        assert escalations_on(port, DIRECT_OWED) == [
            c for c in port.comments if c.comment_key == raised[0].comment_key
        ]
        # The one write the replay completes is the one the crash left undone.
        assert port.classification_writes == [
            (DIRECT_OWED, "decision"),
            (DIRECT_OWED, "decision"),
        ]
        assert not any(isinstance(e, ResultEvent) for e in events)
    finally:
        await cleanup(workspace)


async def test_undemonstrable_here_upholds_at_the_environment_reason_touching_nothing(
    repository,
):
    """Undemonstrable here is a non-ground: the claim is refused, not actioned.

    The refusal escalates on the criterion's own sub-issue and classifies it
    `decision`, which is the one field of the claimed record that moves; the
    capability is named on the report, inside the recorded refusal and in the
    escalation's question.
    """
    port = tracker()
    before = port.issues[DIRECT_DONE]
    executor = Executor(
        reproduced=True,
        subject={"kind": "criterion", "id": DIRECT_DONE},
        claimed_capability="network",
        finding=UNVERIFIABLE_HERE,
    )
    service, guard, workspace, _ = await build(
        repository,
        executor,
        port=port,
        runner_environment={CheckPrerequisite.NETWORK: False},
    )
    try:
        events = await drive(service, guard, repository)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        refusal = report.upheld[0]
        assert refusal.reason is UpheldReason.ENVIRONMENT_LACKS_CAPABILITY
        assert refusal.claim.claimed_capability is CheckPrerequisite.NETWORK
        assert refusal.claim.ground in AmendmentGround
        assert refusal.publication.kind == "escalated"
        record = refusal.publication.record.artifact
        assert '"claimedCapability":"network"' in record.content
        assert record.surface.ref.key == DIRECT_DONE
        settled = port.issues[DIRECT_DONE]
        # Every field but the classification the escalation adds, and the stamp
        # that write carries, is the record this run entered with.
        assert settled.model_dump(
            exclude={"updated_at", "issue_labels"}
        ) == before.model_dump(exclude={"updated_at", "issue_labels"})
        assert settled.issue_labels == before.issue_labels | {"decision"}
        assert settled.state_kind is WorkflowStateKind.COMPLETED
        assert [c["output_format"]["schema"]["title"] for c in executor.calls] == [
            "NativeWriterOutput",
            "AmendmentJudgment",
            "WriteBackFinding",
            "WriteBackFinding",
        ]
        assert not any(isinstance(e, ResultEvent) for e in events)
        assert (
            await git(repository[0], "ls-remote", "origin", "refs/heads/native-test")
            == ""
        )
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize(
    "claimed,environment,repo_url,expected",
    [
        pytest.param(
            None,
            {CheckPrerequisite.NETWORK: False},
            REPO_URL,
            UpheldReason.GROUND_NOT_REPRODUCED,
            id="no_declared_capability_is_not_undemonstrability",
        ),
        pytest.param(
            "network",
            {CheckPrerequisite.NETWORK: False},
            REPO_URL,
            UpheldReason.ENVIRONMENT_LACKS_CAPABILITY,
            id="the_typed_claim_meets_the_declared_absence",
        ),
        pytest.param(
            "network",
            {CheckPrerequisite.NETWORK: False},
            resolve_repo_url(REPO_URL, GIT_BASE_URL),
            UpheldReason.ENVIRONMENT_LACKS_CAPABILITY,
            id="the_resolved_clone_url_matches_its_declared_repository",
        ),
        pytest.param(
            "network",
            {CheckPrerequisite.NETWORK: True},
            REPO_URL,
            UpheldReason.GROUND_NOT_REPRODUCED,
            id="the_capability_declared_present_is_not_undemonstrability",
        ),
        pytest.param(
            "network",
            {CheckPrerequisite.NETWORK: False},
            None,
            UpheldReason.GROUND_NOT_REPRODUCED,
            id="no_matched_repository_declares_no_environment",
        ),
        pytest.param(
            "network",
            None,
            REPO_URL,
            UpheldReason.GROUND_NOT_REPRODUCED,
            id="a_matched_repository_at_its_default_environment_leaves_it_unknown",
        ),
        pytest.param(
            "credentials",
            {CheckPrerequisite.NETWORK: False},
            REPO_URL,
            UpheldReason.GROUND_NOT_REPRODUCED,
            id="another_capability_declared_absent_is_not_undemonstrability",
        ),
        pytest.param(
            "network",
            {CheckPrerequisite.NETWORK: False},
            "https://example.invalid/owner/other",
            UpheldReason.GROUND_NOT_REPRODUCED,
            id="an_undeclared_repository_borrows_no_other_declaration",
        ),
    ],
)
async def test_undemonstrability_conjoins_the_typed_claim_and_the_declared_environment(
    repository, claimed, environment, repo_url, expected
):
    """Neither party can force the recording, through the wiring that supplies it.

    The rows run the whole writer, so the environment under test is the one
    `for_writer` resolves off the matched repository rather than one a fixture
    hands the resolver. One row matches no repository at all: the guard is then
    handed no declared environment and the refusal falls back to the ground,
    which is the fail-closed arm the resolver alone cannot show. Another matches
    a repository that leaves `runner_environment` at its default: an omitted
    capability is unknown, not absent, so that refusal falls back to the ground
    as well. A claim of one capability against another's declared absence is
    matched to its own entry and falls back the same way, and a running
    repository no declaration covers borrows no other repository's environment.

    In every row the departure is refused and the criterion stands: its text and
    its state are what they were, and only the escalated row's classification
    moves.
    """
    port = tracker()
    before = port.issues[DIRECT_OWED]
    executor = Executor(
        reproduced=True, claimed_capability=claimed, finding=UNVERIFIABLE_HERE
    )
    service, guard, workspace, _ = await build(
        repository,
        executor,
        port=port,
        repo_url=repo_url,
        runner_environment=environment,
    )
    escalated = expected is UpheldReason.ENVIRONMENT_LACKS_CAPABILITY
    try:
        events = await drive(service, guard, repository)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        refusal = report.upheld[0]
        assert refusal.reason is expected
        assert refusal.publication.kind == ("escalated" if escalated else "recorded")
        settled = port.issues[DIRECT_OWED]
        assert settled.body == before.body
        assert settled.state_kind is before.state_kind
        assert settled.issue_labels == (
            before.issue_labels | {"decision"} if escalated else before.issue_labels
        )
        assert len(escalations_on(port, DIRECT_OWED)) == (1 if escalated else 0)
        assert not any(isinstance(e, ResultEvent) for e in events)
    finally:
        await cleanup(workspace)


async def test_an_undemonstrable_criterion_stays_owed_while_a_lapsed_one_is_owed_again(
    repository,
):
    """Two arms over one board, so collapsing either into the other fails here.

    The undemonstrable arm is recorded, not moved: its refusal is the marker
    comment on the criterion's own sub-issue naming the missing capability, the
    machine writes no state, and the criterion is still in the gap its subtree
    owes until a person cancels it with a supersession. The lapsed arm is moved:
    a criterion finished at one sha whose grading lapsed at the next goes back
    to the unstarted state, keeping the sha it was graded at, and is in the same
    gap again.

    The undemonstrable arm is driven twice: once on a criterion that is still
    unstarted, and once on a finished one. A lapse leaves an unstarted criterion
    where it is, so only the finished one can show a refusal treated as a lapse:
    it stays finished, its record unmoved but for its classification, and it
    stays out of the gap. A lapse treated as a refusal leaves the lapsed
    criterion finished and out of the gap. Beside both, the finished criterion
    is also the control that the gap read is the open reading and not the
    roster.
    """
    port = tracker()
    before = port.issues[DIRECT_OWED]
    before_done = port.issues[DIRECT_DONE]
    for subject in (DIRECT_OWED, DIRECT_DONE):
        executor = Executor(
            reproduced=True,
            subject={"kind": "criterion", "id": subject},
            claimed_capability="network",
            finding=UNVERIFIABLE_HERE,
        )
        service, guard, workspace, _ = await build(
            repository,
            executor,
            port=port,
            runner_environment={CheckPrerequisite.NETWORK: False},
        )
        try:
            events = await drive(service, guard, repository)
            report = next(
                e.report for e in events if isinstance(e, NativeAmendmentEvent)
            )
            refusal = report.upheld[0]
            assert refusal.claim.subject.id == subject
            assert refusal.reason is UpheldReason.ENVIRONMENT_LACKS_CAPABILITY
            assert not any(isinstance(e, ResultEvent) for e in events)
        finally:
            await cleanup(workspace)

    # The lapsed arm, over the same board, through the landed take-back.
    await lapse(
        writer(port, lane_repo()),
        key=NESTED_DONE,
        standing_sha="1" * 40,
        head_sha="2" * 40,
    )

    # Undemonstrable: recorded where every refusal is, naming the capability.
    archives = [
        c
        for c in port.comments
        if c.issue_key == DIRECT_OWED and c.body.startswith("[fixture-amendment:")
    ]
    assert len(archives) == 1
    assert '"claimedCapability":"network"' in archives[0].body
    # ... and nothing about the criterion itself moved but its classification.
    recorded = port.issues[DIRECT_OWED]
    unmoved = {"issue_labels", "updated_at"}
    assert recorded.model_dump(exclude=unmoved) == before.model_dump(exclude=unmoved)
    assert recorded.state_kind is WorkflowStateKind.UNSTARTED
    assert recorded.issue_labels == before.issue_labels | {"decision"}
    assert all(key != DIRECT_OWED for key, _ in port.workflow_writes)
    assert all(key != DIRECT_OWED for key, _ in port.restored_states)

    # Undemonstrable on a finished criterion: recorded the same way, and not
    # taken back as a lapse would take it.
    done_archives = [
        c
        for c in port.comments
        if c.issue_key == DIRECT_DONE and c.body.startswith("[fixture-amendment:")
    ]
    assert len(done_archives) == 1
    assert '"claimedCapability":"network"' in done_archives[0].body
    refused_done = port.issues[DIRECT_DONE]
    assert refused_done.model_dump(exclude=unmoved) == before_done.model_dump(
        exclude=unmoved
    )
    assert refused_done.state_kind is WorkflowStateKind.COMPLETED

    # Lapsed: moved back, keeping the sha its grading was taken at.
    lapsed = port.issues[NESTED_DONE]
    assert lapsed.state_kind is WorkflowStateKind.UNSTARTED
    evidence = parse_criterion_evidence(lapsed.body)
    assert evidence.graded_sha == "1" * 40
    assert evidence.test == lapse_observation(
        observation=evaluation_observation(session_id="eval-session", iteration=1)
    )
    # The pointer read as the literal it is, not only through the composer above.
    assert evidence.test.endswith(" — that grading lapsed")

    # Neither arm is a cancellation: nothing on the board is closed as canceled
    # or duplicate, so the gap below is not the product of one.
    assert not any(
        issue.state_kind in {WorkflowStateKind.CANCELED, WorkflowStateKind.DUPLICATE}
        for issue in port.issues.values()
    )

    # Both are owed, read off one subtree that still reads; the finished
    # criterion refused as undemonstrable is not, and neither is it moved.
    owed = {
        issue.issue_key
        for issue in SubtreeClosure(
            facts=dict(port.issues), ref=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT)
        ).gap(SUBJECT)
    }
    assert {DIRECT_OWED, NESTED_DONE} <= owed
    assert DIRECT_DONE not in owed
    assert port.issues[DIRECT_DONE].state_kind is WorkflowStateKind.COMPLETED


@pytest.mark.parametrize("fault", ["criterion", "environment"])
async def test_the_fault_line_separates_a_fault_in_the_criterion_from_one_outside_it(
    repository, fault
):
    """Paired walks differing only in where the judgment puts the fault.

    An implementation that amends both arms fails the environment arm, and one
    that upholds both fails the criterion arm. Cost is not one of the four
    grounds, so the landed order still asks the fault line before any ground.
    """
    executor = Executor(
        reproduced=True,
        finding=UNVERIFIABLE_HERE if fault == "environment" else None,
    )
    service, guard, workspace, port = await build(repository, executor)
    prior = port.issues[DIRECT_OWED]
    try:
        events = await drive(service, guard, repository)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        verdict = report.verdicts[0]
        titles = [c["output_format"]["schema"]["title"] for c in executor.calls]
        if fault == "criterion":
            assert verdict.verdict == "amended"
            assert AMENDED_CHECK in port.issues[DIRECT_OWED].body
            assert "AmendmentTextOutput" in titles
            assert any(isinstance(e, ResultEvent) and e.commit_sha for e in events)
            assert await git(
                repository[0], "ls-remote", "origin", "refs/heads/native-test"
            )
        else:
            assert verdict.verdict == "upheld"
            assert verdict.reason is UpheldReason.GROUND_NOT_REPRODUCED
            assert port.issues[DIRECT_OWED].body == prior.body
            assert port.issues[DIRECT_OWED].state_kind is prior.state_kind
            assert "AmendmentTextOutput" not in titles
            assert not any(isinstance(e, ResultEvent) for e in events)
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize("affordable", [None, True, False])
async def test_cost_departure_is_recorded_not_actioned_and_uneconomic_is_escalated(
    repository, affordable
):
    """One case per arm, keyed on the measurement, and no arm amends.

    No recorded measurement is the unreproduced-ground arm. A measurement
    showing the expense is not incurred is the affordable arm, and its discharge
    is that the claimed subject's whole record is byte-identical afterwards — the
    historical 2026-08 measurement is not reproduced here. A measurement showing
    the expense is incurred is the uneconomic arm, which escalates on the owning
    issue carrying that same measurement. The session census is the literal that
    says no arm reaches the amendment-text stage.
    """

    async def observe(title, payload, kwargs):
        if title == "AmendmentJudgment":
            measurement = None
            if affordable is not None:
                # The instrument is a real act in the judge's own worktree: it
                # reads the repository at base and leaves nothing behind.
                read = Path(kwargs["cwd"], "policy.py").read_bytes()
                measurement = {
                    "observed": f"measured 12 minutes over {len(read)} bytes at base",
                    "affordable": affordable,
                }
            payload["finding"] = {
                "verdict": "feasible",
                "smallest_repair": "none",
                "cost_claim": {
                    "assertion": "The demonstration costs too much.",
                    "measurement": measurement,
                },
            }
            payload["measured_by"] = (
                None if affordable is None else "timed the actual base demonstration"
            )

    executor = Executor(mutate=observe)
    service, guard, workspace, port = await build(repository, executor)
    prior = port.issues[DIRECT_OWED]
    repo = repository[0]
    tip = await git(repo, "rev-parse", "main")
    files = (await git(repo, "ls-tree", "-r", "--name-only", "main")).splitlines()
    assert files == ["newer.py", "policy.py"]
    base_size = int(await git(repo, "cat-file", "-s", f"{repository[1]}:policy.py"))
    try:
        events = await drive(service, guard, repository)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        refusal = report.upheld[0]
        titles = [c["output_format"]["schema"]["title"] for c in executor.calls]
        # No arm amends: no amendment-text session on any arm, and only the
        # uneconomic arm runs a second verified write for its escalation.
        assert titles == [
            "NativeWriterOutput",
            "AmendmentJudgment",
            "WriteBackFinding",
        ] + (["WriteBackFinding"] if affordable is False else [])
        assert len(report.verdicts) == 1 and report.verdicts[0].verdict == "upheld"
        assert refusal.reason is (
            UpheldReason.GROUND_NOT_REPRODUCED
            if affordable is None
            else UpheldReason.COST_MEASURED_AFFORDABLE
            if affordable
            else UpheldReason.COST_MEASURED_UNECONOMIC
        )
        record = AmendmentRecord.model_validate_json(
            refusal.publication.record.artifact.content.partition("\n")[2]
        )
        assert record.disposition == "accepted_and_not_actioned"
        assert json.loads(record.prior.content)[0]["body"] == prior.body
        assert port.issues[DIRECT_OWED].body == prior.body
        assert port.issues[DIRECT_OWED].state_kind is prior.state_kind
        assert ("decision" in port.issues[DIRECT_OWED].issue_labels) is (
            affordable is False
        )
        # The recorded measurement, in the verdict's own evidence: what was
        # observed is tied to the repository at the resolved base, and how it was
        # produced is the separate field. Both reach the archived record.
        cost = refusal.judgment.finding.cost_claim
        content = refusal.publication.record.artifact.content
        assert (cost.measurement is None) is (affordable is None)
        assert refusal.judgment.base_sha == repository[1]
        if affordable is None:
            assert refusal.judgment.measured_by is None
        else:
            assert cost.measurement.observed == (
                f"measured 12 minutes over {base_size} bytes at base"
            )
            assert refusal.judgment.measured_by == "timed the actual base demonstration"
            assert cost.measurement.observed in content
            assert refusal.judgment.measured_by in content
        if affordable:
            # The discharge of the affordable arm: the whole claimed record, not
            # only its body, is what it was before the run.
            settled = port.issues[DIRECT_OWED]
            assert settled.model_dump(exclude={"updated_at"}) == prior.model_dump(
                exclude={"updated_at"}
            )
        if affordable is False:
            assert refusal.publication.kind == "escalated"
            assert (
                "measured 12 minutes" in refusal.publication.escalation.artifact.content
            )
            assert (
                refusal.publication.escalation.artifact.surface.ref.key == DIRECT_OWED
            )
            assert (
                refusal.judgment.measured_by
                in refusal.publication.escalation.artifact.content
            )
        assert not any(isinstance(e, ResultEvent) for e in events)
        assert (
            await git(repository[0], "ls-remote", "origin", "refs/heads/native-test")
            == ""
        )
        # The measurement reached no branch: the same head sha and the same
        # tracked files, and the worktree it ran in is gone.
        assert await git(repo, "rev-parse", "main") == tip
        assert await git(repo, "rev-parse", "native-test") == tip
        assert (
            await git(repo, "ls-tree", "-r", "--name-only", "native-test")
        ).splitlines() == files
        judge = executor.calls[1]
        assert judge["output_format"]["schema"]["title"] == "AmendmentJudgment"
        assert judge["cwd"] in workspace.released
        assert not Path(judge["cwd"]).exists()
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize("ground", list(AmendmentGround))
async def test_ruling_amendment_preserves_native_occurrence_question_and_prior_bytes(
    repository, ground
):
    from kodezart.domain.agent import mint_ruling_id
    from kodezart.domain.rulings import render_ruling
    from kodezart.services.ruling_records import RulingRecordReader
    from kodezart.types.domain.agent import Ruling
    from kodezart.types.domain.operation import OperationConfig
    from tests.chains.test_native_fire import SUBJECT
    from tests.domain.test_rulings import ruling_data

    port = tracker()
    # The record amended here replaces an earlier one, so the amendment has a
    # pointer to lose: rebuilding the record from its own dump has to carry
    # ``supersedes`` across, or the replaced record stops being reachable
    # from the record that replaced it (KOD-635).
    superseded = mint_ruling_id(
        issue_ref=SUBJECT, question="Which interpretation applied before?"
    )
    ruling = Ruling.model_validate(
        ruling_data(issue_ref=SUBJECT, authored_by="principal", supersedes=superseded)
    )
    assert ruling.supersedes == superseded
    body = render_ruling(
        ruling=ruling,
        lane_key="historical:café/lane",
        marker_prefixes={"ruling": "fixture-pinned"},
    )
    original = await port.post_comment(issue_key=SUBJECT, body=body)

    async def answers(title, payload, kwargs):
        if title == "AmendmentTextOutput":
            payload["replacement"] = {
                "kind": "ruling",
                "subject": {"kind": "ruling", "id": ruling.ruling_id},
                "resolution": (
                    "The corrected answer follows the reproduced base evidence."
                ),
                "rejected_alternative": "The independently refuted prior reading.",
                "repo_evidence": ["policy.py"],
            }

    executor = Executor(
        reproduced=True,
        ground=ground,
        subject={"kind": "ruling", "id": ruling.ruling_id},
        mutate=answers,
    )
    service, guard, workspace, _ = await build(repository, executor, port=port)
    try:
        events = await drive(service, guard, repository)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        amended = report.verdicts[0]
        assert amended.verdict == "amended"
        assert amended.prior.content == body
        assert amended.prior.native_ref == original.comment_key
        records = await RulingRecordReader(
            tracker=port,
            operation=OperationConfig(
                operation_name="fixture",
                workspace="fixture",
                marker_prefixes={"ruling": "fixture-pinned"},
            ),
        ).read_issue(issue_key=SUBJECT)
        assert len(records) == 1
        observed_comment, observed_ruling = records[0]
        assert observed_comment.comment_key == original.comment_key
        assert observed_comment.author_key == original.author_key
        assert observed_comment.created_at == original.created_at
        assert observed_comment.body.partition("\n")[0] == body.partition("\n")[0]
        assert observed_ruling.ruling_id == ruling.ruling_id
        assert observed_ruling.question == ruling.question
        assert observed_ruling.ruling_class is ruling.ruling_class
        assert observed_ruling.resolution != ruling.resolution
        assert observed_ruling.authored_by.value == "machine"
        # The amended record keeps the identity it replaces, so the record it
        # replaced is still reachable from it afterwards.
        assert observed_ruling.supersedes == superseded
        assert any(isinstance(e, ResultEvent) and e.commit_sha for e in events)
    finally:
        await cleanup(workspace)


@pytest.mark.parametrize("boundary", ["author", "verification"])
@pytest.mark.parametrize("change", ["check", "outage"])
async def test_amendment_refuses_external_authority_drift_across_fresh_sessions(
    repository, monkeypatch, boundary, change
):
    from kodezart.domain.amendment import NativeWriteRefusalError
    from kodezart.domain.errors import FireSpecEntryError
    from tests.chains.test_native_fire import NESTED_OWED

    port = tracker()
    verifies = 0

    async def answers(title, payload, kwargs):
        nonlocal verifies
        if title == "WriteBackFinding":
            verifies += 1
        if (boundary == "author" and title == "AmendmentTextOutput") or (
            boundary == "verification" and title == "WriteBackFinding" and verifies == 2
        ):
            if change == "check":
                current = port.issues[NESTED_OWED]
                port.issues[NESTED_OWED] = current.model_copy(
                    update={
                        "body": current.body.replace(
                            "the check", "a concurrent different check"
                        )
                    }
                )
            else:

                async def unavailable(**kwargs):
                    raise ConnectionError("tracker unavailable after awaited judgment")

                monkeypatch.setattr(port, "scope_issues", unavailable)

    executor = Executor(reproduced=True, mutate=answers)
    service, guard, workspace, _ = await build(repository, executor, port=port)
    try:
        with pytest.raises((NativeWriteRefusalError, FireSpecEntryError)):
            await drive(service, guard, repository)
        assert not await git(
            repository[0], "ls-remote", "origin", "refs/heads/native-test"
        )
        assert (
            await git(repository[0], "log", "native-test", "--format=%s", "-1")
            == "newer writer starting point"
        )
        if boundary == "author":
            assert "the amended observable Check" not in port.issues[DIRECT_OWED].body
    finally:
        await cleanup(workspace)


async def test_designated_protected_test_change_is_upheld_and_never_reaches_the_branch(
    repository,
):
    """A weakening edit to a designated protected test is claimed, not committed.

    The claim's subject is the pinned record that designates the test, addressed
    by that record's own identity — no subject kind of its own is involved. The
    writer really performs the edit in its own workspace, and on the default
    upheld arm the test's bytes on the loop branch are the same object as before
    the run, the branch head is unmoved, nothing is pushed, and the refusal is
    recorded against the record's own marker-comment surface, which still carries
    the designation.
    """
    port = tracker()
    pinned = pinned_designation(issue_ref=SUBJECT)
    body = render_ruling(
        ruling=pinned,
        lane_key=SUBJECT,
        marker_prefixes={"ruling": "fixture-pinned"},
    )
    await port.post_comment(issue_key=SUBJECT, body=body)
    repo = repository[0]
    designated = Path(repo, PROTECTED_PATH)
    designated.parent.mkdir(parents=True, exist_ok=True)
    designated.write_text(PROTECTED_BODY)
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "the designated boundary test")
    tip = await git(repo, "rev-parse", "main")
    # The blob id is the byte-exact handle; reading the file back through a
    # command would strip trailing bytes.
    blob = await git(repo, "rev-parse", f"main:{PROTECTED_PATH}")
    weakened = []

    async def weaken(title, payload, kwargs):
        if title != "NativeWriterOutput":
            return
        target = Path(kwargs["cwd"], PROTECTED_PATH)
        assert target.read_text() == PROTECTED_BODY
        target.write_text(WEAKENED_BODY)
        weakened.append(target.read_text())
        payload["claims"][0]["departure"] = (
            "Weaken the designated boundary test's assertion."
        )

    executor = Executor(
        subject={"kind": "ruling", "id": pinned.ruling_id}, mutate=weaken
    )
    service, guard, workspace, port = await build(repository, executor, port=port)
    try:
        events = await drive(service, guard, repository)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))
        refusal = report.upheld[0]
        assert refusal.subject.kind == "ruling"
        assert refusal.subject.id == pinned.ruling_id
        assert refusal.reason is UpheldReason.GROUND_NOT_REPRODUCED
        assert len(report.verdicts) == 1
        # The edit really existed in the writer's session.
        assert weakened == [WEAKENED_BODY]
        # And it reached no branch: the same object id for the designated test,
        # the same branch head, and no remote branch at all.
        assert await git(repo, "rev-parse", f"native-test:{PROTECTED_PATH}") == blob
        assert await git(repo, "rev-parse", "native-test") == tip
        assert await git(repo, "ls-remote", "origin", "refs/heads/native-test") == ""
        assert not any(isinstance(e, ResultEvent) for e in events)
        assert refusal.publication.kind == "recorded"
        archived = AmendmentRecord.model_validate_json(
            refusal.publication.record.artifact.content.partition("\n")[2]
        )
        assert archived.disposition == "accepted_and_not_actioned"
        # The prior bytes of the record's own surface, designation included.
        assert PROTECTED_PATH in archived.prior.content
        assert PROTECTED_NAME in archived.prior.content
        assert port.comments[0].body == body
        assert [c["output_format"]["schema"]["title"] for c in executor.calls] == [
            "NativeWriterOutput",
            "AmendmentJudgment",
            "WriteBackFinding",
        ]
        # The designation and the convention that addresses it reached both
        # sessions that read the roster.
        assert PROTECTED_PATH in executor.calls[0]["prompt"]
        assert PROTECTED_PATH in executor.calls[1]["prompt"]
        assert "designates it" in executor.calls[0]["prompt"]
    finally:
        await cleanup(workspace)


async def test_a_held_marked_model_refuses_the_write_backs_own_acquisition(repository):
    """The write-back's lease is the model's, wherever the member belongs to one.

    The subject carries the model's classification here, so its criterion
    sub-issues and its own body are one thing to write. A job already
    holding the subject's body therefore meets the write-back's own
    acquisition, and what that acquisition raises is the landed surface
    lease error — unchanged, not caught and not translated.
    """
    port = tracker()
    subject = port.issues[SUBJECT]
    port.issues[SUBJECT] = subject.model_copy(
        update={"issue_labels": subject.issue_labels | {MODEL_CLASSIFICATION}}
    )
    await port.acquire_surfaces(
        surfaces=frozenset(
            {
                WritableSurface(
                    kind=SurfaceKind.ISSUE_DESCRIPTION,
                    ref=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT),
                )
            }
        ),
        holder="another-writing-job",
        lease_seconds=900,
    )
    executor = Executor(reproduced=True)
    service, guard, workspace, _ = await build(
        repository,
        executor,
        port=port,
        issue_labels={
            "decision": "decision",
            MODEL_CLASSIFICATION: "model:criterion-lifecycle",
        },
    )
    try:
        with pytest.raises(SurfaceLeaseError):
            await drive(service, guard, repository)
        assert AMENDED_CHECK not in port.issues[DIRECT_OWED].body
    finally:
        await cleanup(workspace)


async def designated_repository(repo, port, *, issue_ref=SUBJECT):
    """Pin a record designating the boundary test and commit it on ``main``.

    *issue_ref* is the subtree member the record is pinned on; the lane reads
    the records of every member of its subtree, not only its own.
    """
    pinned = pinned_designation(issue_ref=issue_ref)
    await port.post_comment(
        issue_key=issue_ref,
        body=render_ruling(
            ruling=pinned,
            lane_key=SUBJECT,
            marker_prefixes={"ruling": "fixture-pinned"},
        ),
    )
    designated = Path(repo, PROTECTED_PATH)
    designated.parent.mkdir(parents=True, exist_ok=True)
    designated.write_text(PROTECTED_BODY)
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "the designated boundary test")
    return pinned


def criterion_children(port):
    """The lane's criterion sub-issues, by key."""
    return {
        key: issue
        for key, issue in port.issues.items()
        if issue.parent_key == SUBJECT and "criterion" in issue.issue_labels
    }


def new_issues(port, before):
    """Every issue on the board whose key is not in *before*, whatever its parent."""
    return {key: issue for key, issue in port.issues.items() if key not in before}


async def weaken(title, payload, kwargs):
    """Rewrite the designated test's assertion, claiming no departure for it."""
    if title != "NativeWriterOutput":
        return
    target = Path(kwargs["cwd"], PROTECTED_PATH)
    assert target.read_text() == PROTECTED_BODY
    target.write_text(WEAKENED_BODY)


#: The job the harness hands the writer's guard, and so the holder every lease
#: the guard's writes take is recorded under.
WRITER_JOB = "actual-parent-job"


def sub_issue_leases(port, key):
    """The holders of every lease the board granted over criterion *key* itself."""
    surface = WritableSurface(
        kind=SurfaceKind.CRITERION_SUB_ISSUE,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=key),
    )
    return {lease.holder for lease in port.lease_writes if surface in lease.surfaces}


def rendered_mark(pinned):
    """The mark the loss of this designation's only assertion renders."""
    lost = protected_assertions(
        source=PROTECTED_BODY.encode(),
        path=PROTECTED_PATH,
        qualified_name=PROTECTED_NAME,
    )
    return weakening_mark(
        claim=AssertionDeviationClaim(
            protected_test=pinned.protected_tests[0],
            graded_sha="a" * 40,
            head_sha="b" * 40,
            graded_blob_sha="c" * 40,
            head_blob_sha="d" * 40,
            before=lost,
            after=(),
        ),
        lost=lost,
    )


async def test_a_weakened_designated_assertion_marks_the_lane_and_is_never_pushed(
    repository,
):
    """The commit exists in the workspace; the obligation exists on the lane.

    No departure was claimed for the edit, so no judgment session ran and the
    amendment arm had nothing to uphold: what refuses the publication is the
    comparison between the writer's own starting HEAD and the harness commit.
    The mark is one unstarted criterion sub-issue on the lane whose Check is
    the rendered text byte for byte, and it sits in the gap read the Check
    names, ``kodezart.domain.gap.compute_gap``.
    """
    repo = repository[0]
    port = tracker()
    pinned = await designated_repository(repo, port)
    start = await git(repo, "rev-parse", "main")
    before = set(port.issues)
    executor = Executor(claim=False, mutate=weaken)
    service, guard, workspace, port = await build(repository, executor, port=port)
    try:
        with pytest.raises(AssertionWeakenedError) as caught:
            await drive(service, guard, repository)
        # The commit really was made, in the writer's own workspace, and it
        # carries the weakened bytes.
        path = workspace.acquired[0][0]
        assert await git(path, "rev-parse", "HEAD") != await git(
            repo, "rev-parse", "main"
        )
        assert await git(path, "show", f"HEAD:{PROTECTED_PATH}") == (
            WEAKENED_BODY.strip()
        )
        # Every issue the run added, wherever it was parented, so the parent
        # and the label below are read rather than selected for.
        minted = new_issues(port, before)
        assert len(minted) == 1
        (key,) = minted
        assert minted[key].parent_key == SUBJECT
        assert "criterion" in minted[key].issue_labels
        assert minted[key].state_kind is WorkflowStateKind.UNSTARTED
        assert caught.value.marks == (key,)
        assert caught.value.lane_key == SUBJECT
        # The Check is the rendered mark, byte for byte, and it names the test
        # and the record, and neither a commit, nor the assertion that went,
        # nor the one that replaced it.
        check = criterion_field_bodies(minted[key].body, field="Check")[0]
        assert check == rendered_mark(pinned).check
        assert start not in check
        assert PROTECTED_PATH in check
        assert PROTECTED_NAME in check
        assert pinned.ruling_id in check
        assert "answer() == 42" not in check
        assert "answer() is not None" not in check
        # The mark is in the gap read over the lane's own criterion sub-issues.
        assert key in {
            issue.issue_key
            for issue in gap.compute_gap(
                criteria=await port.read_criteria(issue_key=SUBJECT),
            ).owed
        }
        # And nothing was published: no remote branch and no judgment session,
        # because no departure was claimed for the edit.
        assert await git(repo, "ls-remote", "origin", "refs/heads/native-test") == ""
        assert [c["output_format"]["schema"]["title"] for c in executor.calls] == [
            "NativeWriterOutput",
            "CommitMessageOutput",
        ]
    finally:
        await cleanup(workspace)


async def test_a_record_pinned_on_a_subtree_member_still_marks_the_lane_itself(
    repository,
):
    """The lane reads the records of its whole subtree, and the mark is the lane's.

    The designating record here is pinned on one of the lane's own criterion
    sub-issues rather than on the lane, so the record's owning issue and the
    lane differ: the mark still goes on the lane, where its gap is read.
    """
    repo = repository[0]
    port = tracker()
    await designated_repository(repo, port, issue_ref=DIRECT_OWED)
    before = set(port.issues)
    executor = Executor(claim=False, mutate=weaken)
    service, guard, workspace, port = await build(repository, executor, port=port)
    try:
        with pytest.raises(AssertionWeakenedError) as caught:
            await drive(service, guard, repository)

        minted = new_issues(port, before)
        assert len(minted) == 1
        (key,) = minted
        assert minted[key].parent_key == SUBJECT
        assert caught.value.marks == (key,)
        assert key in {
            issue.issue_key
            for issue in gap.compute_gap(
                criteria=await port.read_criteria(issue_key=SUBJECT),
            ).owed
        }
        assert await git(repo, "ls-remote", "origin", "refs/heads/native-test") == ""
    finally:
        await cleanup(workspace)


async def test_the_mark_keeps_the_lane_out_of_convergence_until_it_is_done(repository):
    """One arithmetic: with every other criterion Done the mark is the whole gap.

    The subtree rollup is the reading the walk's ready set, the lane roster's
    ``done`` and the terminal's outcome are all made of, so a lane carrying
    this mark cannot converge, and crossing the mark off is what closes it.
    """
    repo = repository[0]
    port = tracker()
    await designated_repository(repo, port)
    before = criterion_children(port)
    executor = Executor(claim=False, mutate=weaken)
    service, guard, workspace, port = await build(repository, executor, port=port)
    try:
        with pytest.raises(AssertionWeakenedError) as caught:
            await drive(service, guard, repository)
        (key,) = caught.value.marks
        for issue_key, issue in list(port.issues.items()):
            if "criterion" in issue.issue_labels and issue_key != key:
                port.issues[issue_key] = issue.model_copy(
                    update={
                        "state_name": "Done",
                        "state_kind": WorkflowStateKind.COMPLETED,
                    }
                )
        standing = SubtreeClosure(
            facts=port.issues, ref=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT)
        )

        assert [issue.issue_key for issue in standing.gap(SUBJECT)] == [key]
        assert standing.is_closed(SUBJECT) is False
        assert key not in before

        port.issues[key] = port.issues[key].model_copy(
            update={"state_name": "Done", "state_kind": WorkflowStateKind.COMPLETED}
        )
        crossed = SubtreeClosure(
            facts=port.issues, ref=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT)
        )

        assert crossed.gap(SUBJECT) == ()
        assert crossed.is_closed(SUBJECT) is True
    finally:
        await cleanup(workspace)


async def test_a_second_weakening_of_the_same_assertion_finds_the_same_mark(repository):
    """The mark names no sha, so the same loss twice is the same obligation.

    The second run cuts its branch afresh from the same starting point and
    makes the same edit, so the rendered Check is the same bytes and the
    mint's own identity — exact parent plus current Check — answers with the
    child that already stands rather than a second one. That child is still
    open, so nothing moves it: no lease is taken on its own surface.
    """
    repo = repository[0]
    port = tracker()
    await designated_repository(repo, port)
    first_executor = Executor(claim=False, mutate=weaken)
    service, guard, workspace, port = await build(repository, first_executor, port=port)
    try:
        with pytest.raises(AssertionWeakenedError) as first:
            await drive(service, guard, repository)
        after_first = criterion_children(port)
    finally:
        await cleanup(workspace)
    await git(repo, "branch", "-D", "native-test")
    second_executor = Executor(claim=False, mutate=weaken)
    service, guard, workspace, port = await build(
        repository, second_executor, port=port
    )
    try:
        with pytest.raises(AssertionWeakenedError) as second:
            await drive(service, guard, repository)

        assert criterion_children(port) == after_first
        assert second.value.marks == first.value.marks
        assert len(second.value.marks) == 1
        assert sub_issue_leases(port, second.value.marks[0]) == set()
    finally:
        await cleanup(workspace)


async def test_a_weakening_after_the_mark_was_crossed_off_is_marked_again(repository):
    """A crossed-off mark is reopened by a weakening from a later head.

    The first weakening is refused and marked, and the mark is crossed off,
    which is the normal course because the weakened commit never reached the
    remote. The lane then moves on to a later head, and a later writer
    starting there weakens the same test again. The Check is the same bytes,
    so the mint answers with the crossed-off child, and the writer moves it
    back to unstarted: the lane carries the open mark in its gap again while
    the push is still refused.
    """
    repo = repository[0]
    port = tracker()
    pinned = await designated_repository(repo, port)
    service, guard, workspace, port = await build(
        repository, Executor(claim=False, mutate=weaken), port=port
    )
    try:
        with pytest.raises(AssertionWeakenedError) as first:
            await drive(service, guard, repository)
    finally:
        await cleanup(workspace)
    (crossed,) = first.value.marks
    port.issues[crossed] = port.issues[crossed].model_copy(
        update={"state_name": "Done", "state_kind": WorkflowStateKind.COMPLETED}
    )
    await git(repo, "branch", "-D", "native-test")
    Path(repo, "later.py").write_text("Work the lane published after the mark.\n")
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "later work on the lane")
    later = await git(repo, "rev-parse", "main")
    between = set(port.issues)
    service, guard, workspace, port = await build(
        repository, Executor(claim=False, mutate=weaken), port=port
    )
    try:
        with pytest.raises(AssertionWeakenedError) as second:
            await drive(service, guard, repository)

        assert new_issues(port, between) == {}
        assert second.value.marks == (crossed,)
        reopened = port.issues[crossed]
        assert reopened.parent_key == SUBJECT
        assert "criterion" in reopened.issue_labels
        assert reopened.state_kind is WorkflowStateKind.UNSTARTED
        check = criterion_field_bodies(reopened.body, field="Check")[0]
        assert check == rendered_mark(pinned).check
        assert later not in check
        standing = {
            issue.issue_key
            for issue in gap.compute_gap(
                criteria=await port.read_criteria(issue_key=SUBJECT),
            ).owed
        }
        assert crossed in standing
        assert sub_issue_leases(port, crossed) == {WRITER_JOB}
        assert await git(repo, "ls-remote", "origin", "refs/heads/native-test") == ""
    finally:
        await cleanup(workspace)


async def remove_assertion(title, payload, kwargs):
    """Another loss in the same designated test: its assertion is removed."""
    if title != "NativeWriterOutput":
        return
    target = Path(kwargs["cwd"], PROTECTED_PATH)
    assert target.read_text() == PROTECTED_BODY
    target.write_text(f"def {PROTECTED_NAME}():\n    pass\n")


@pytest.mark.parametrize(
    "second_mutate", [weaken, remove_assertion], ids=["same loss", "other loss"]
)
async def test_a_weakening_from_the_same_head_after_the_mark_was_crossed_off_reopens_it(
    repository, second_mutate
):
    """The ordinary course: the head never moved, and the mark is open again.

    The first weakening is refused and marked, and the mark is crossed off,
    because the weakened commit never reached the remote. Nothing is
    committed between the two runs, so the second writer starts from the
    same head and weakens the same test again, with the same loss or with
    another one. The Check is the same bytes, so the mint answers with the
    crossed-off child, and the writer moves it back to unstarted under a
    lease on that child: the lane carries an open mark for the test in its
    gap, and the push is still refused.
    """
    repo = repository[0]
    port = tracker()
    pinned = await designated_repository(repo, port)
    start = await git(repo, "rev-parse", "main")
    service, guard, workspace, port = await build(
        repository, Executor(claim=False, mutate=weaken), port=port
    )
    try:
        with pytest.raises(AssertionWeakenedError) as first:
            await drive(service, guard, repository)
    finally:
        await cleanup(workspace)
    (crossed,) = first.value.marks
    port.issues[crossed] = port.issues[crossed].model_copy(
        update={"state_name": "Done", "state_kind": WorkflowStateKind.COMPLETED}
    )
    await git(repo, "branch", "-D", "native-test")
    between = set(port.issues)
    service, guard, workspace, port = await build(
        repository, Executor(claim=False, mutate=second_mutate), port=port
    )
    try:
        with pytest.raises(AssertionWeakenedError) as second:
            await drive(service, guard, repository)

        assert await git(repo, "rev-parse", "main") == start
        assert new_issues(port, between) == {}
        assert second.value.marks == (crossed,)
        reopened = port.issues[crossed]
        assert reopened.state_kind is WorkflowStateKind.UNSTARTED
        assert criterion_field_bodies(reopened.body, field="Check") == (
            rendered_mark(pinned).check,
        )
        standing = {
            issue.issue_key
            for issue in gap.compute_gap(
                criteria=await port.read_criteria(issue_key=SUBJECT),
            ).owed
        }
        assert crossed in standing
        assert sub_issue_leases(port, crossed) == {WRITER_JOB}
        assert await git(repo, "ls-remote", "origin", "refs/heads/native-test") == ""
    finally:
        await cleanup(workspace)


async def test_a_designated_test_left_with_no_assertion_is_marked(repository):
    """A commit that empties the designated test of every assertion is marked.

    Nothing of the definition's assertions survives into the harness commit,
    so the loss is the whole of them: the push is refused, and the lane
    carries exactly one open mark in its gap.
    """
    repo = repository[0]
    port = tracker()
    await designated_repository(repo, port)
    before = set(port.issues)

    async def empty(title, payload, kwargs):
        if title != "NativeWriterOutput":
            return
        Path(kwargs["cwd"], PROTECTED_PATH).write_text(
            f"def {PROTECTED_NAME}():\n    pass\n"
        )

    executor = Executor(claim=False, mutate=empty)
    service, guard, workspace, port = await build(repository, executor, port=port)
    try:
        with pytest.raises(AssertionWeakenedError) as caught:
            await drive(service, guard, repository)

        minted = new_issues(port, before)
        assert len(minted) == 1
        (key,) = minted
        assert minted[key].parent_key == SUBJECT
        assert caught.value.marks == (key,)
        standing = [
            issue.issue_key
            for issue in gap.compute_gap(
                criteria=await port.read_criteria(issue_key=SUBJECT),
            ).owed
            if issue.issue_key not in before
        ]
        assert standing == [key]
        assert await git(repo, "ls-remote", "origin", "refs/heads/native-test") == ""
    finally:
        await cleanup(workspace)


#: A second test the same pinned record designates, beside the boundary test.
SECOND_PATH = "tests/test_second_boundary.py"
SECOND_NAME = "test_the_second_boundary_holds"
SECOND_BODY = f"def {SECOND_NAME}():\n    assert answer() > 0\n"


async def test_a_loss_in_a_records_second_designation_is_marked(repository):
    """Every test a record designates is protected, not only its first.

    One pinned record designates the boundary test and a second test, and
    the commit weakens only the second. The loss is marked once, the mark
    names the second test, and the push is refused.
    """
    repo = repository[0]
    port = tracker()
    data = ruling_data(issue_ref=SUBJECT)
    data["protected_tests"] = (
        RulingProtectedTestRef(
            source_ref=data["ruling_id"],
            path=PROTECTED_PATH,
            qualified_name=PROTECTED_NAME,
        ),
        RulingProtectedTestRef(
            source_ref=data["ruling_id"],
            path=SECOND_PATH,
            qualified_name=SECOND_NAME,
        ),
    )
    pinned = Ruling.model_validate(data)
    await port.post_comment(
        issue_key=SUBJECT,
        body=render_ruling(
            ruling=pinned,
            lane_key=SUBJECT,
            marker_prefixes={"ruling": "fixture-pinned"},
        ),
    )
    for path, body in ((PROTECTED_PATH, PROTECTED_BODY), (SECOND_PATH, SECOND_BODY)):
        target = Path(repo, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "the two designated boundary tests")
    before = set(port.issues)

    async def weaken_second(title, payload, kwargs):
        if title != "NativeWriterOutput":
            return
        target = Path(kwargs["cwd"], SECOND_PATH)
        assert target.read_text() == SECOND_BODY
        target.write_text(f"def {SECOND_NAME}():\n    assert answer() is not None\n")

    executor = Executor(claim=False, mutate=weaken_second)
    service, guard, workspace, port = await build(repository, executor, port=port)
    try:
        with pytest.raises(AssertionWeakenedError) as caught:
            await drive(service, guard, repository)

        minted = new_issues(port, before)
        assert len(minted) == 1
        (key,) = minted
        assert minted[key].parent_key == SUBJECT
        assert caught.value.marks == (key,)
        check = criterion_field_bodies(minted[key].body, field="Check")[0]
        assert f"`{SECOND_PATH}::{SECOND_NAME}`" in check
        assert PROTECTED_PATH not in check
        assert await git(repo, "ls-remote", "origin", "refs/heads/native-test") == ""
    finally:
        await cleanup(workspace)


async def test_an_added_assertion_in_a_designated_test_publishes_with_no_mark(
    repository,
):
    """Adding a condition loses none, so the branch is published unmarked."""
    repo = repository[0]
    port = tracker()
    await designated_repository(repo, port)
    before = criterion_children(port)

    async def strengthen(title, payload, kwargs):
        if title != "NativeWriterOutput":
            return
        target = Path(kwargs["cwd"], PROTECTED_PATH)
        target.write_text(PROTECTED_BODY + "    assert answer() > 0\n")

    executor = Executor(claim=False, mutate=strengthen)
    service, guard, workspace, port = await build(repository, executor, port=port)
    try:
        events = await drive(service, guard, repository)

        assert any(isinstance(e, ResultEvent) and e.commit_sha for e in events)
        assert await git(repo, "ls-remote", "origin", "refs/heads/native-test")
        assert criterion_children(port) == before
    finally:
        await cleanup(workspace)


async def test_an_amended_designation_lets_its_test_change_without_a_mark(repository):
    """A record this run amended designates nothing for the rest of the run.

    The departure from the record was claimed and independently reproduced,
    and the canonical writer applied it; the designation survives that
    amendment unchanged, so only the report can say the change was claimed.
    """
    repo = repository[0]
    port = tracker()
    pinned = await designated_repository(repo, port)
    before = criterion_children(port)

    async def answers(title, payload, kwargs):
        await weaken(title, payload, kwargs)
        if title == "NativeWriterOutput":
            payload["claims"][0]["departure"] = (
                "Change the designated boundary test's assertion."
            )
        if title == "AmendmentTextOutput":
            payload["replacement"] = {
                "kind": "ruling",
                "subject": {"kind": "ruling", "id": pinned.ruling_id},
                "resolution": (
                    "The corrected answer follows the reproduced base evidence."
                ),
                "rejected_alternative": "The independently refuted prior reading.",
                "repo_evidence": ["policy.py"],
            }

    executor = Executor(
        reproduced=True,
        subject={"kind": "ruling", "id": pinned.ruling_id},
        mutate=answers,
    )
    service, guard, workspace, port = await build(repository, executor, port=port)
    try:
        events = await drive(service, guard, repository)
        report = next(e.report for e in events if isinstance(e, NativeAmendmentEvent))

        assert report.verdicts[0].verdict == "amended"
        assert report.verdicts[0].subject.id == pinned.ruling_id
        assert any(isinstance(e, ResultEvent) and e.commit_sha for e in events)
        assert await git(repo, "ls-remote", "origin", "refs/heads/native-test")
        assert criterion_children(port) == before
    finally:
        await cleanup(workspace)


async def test_a_weakening_beside_an_amended_criterion_is_still_marked(repository):
    """An amendment the run made elsewhere exempts nothing it did not amend.

    The writer claims a departure from one of the lane's criteria, the claim
    is reproduced and the criterion's text is amended, and in the same commit
    the designated test loses its assertion. The report is not empty, but it
    amended no pinned record, so the designation stands and the loss is marked.
    """
    repo = repository[0]
    port = tracker()
    await designated_repository(repo, port)
    before = criterion_children(port)
    executor = Executor(reproduced=True, mutate=weaken)
    service, guard, workspace, port = await build(repository, executor, port=port)
    try:
        with pytest.raises(AssertionWeakenedError) as caught:
            await drive(service, guard, repository)

        # The criterion amendment landed: the claimed criterion carries the
        # amended Check on the board.
        assert AMENDED_CHECK in port.issues[DIRECT_OWED].body
        minted = {
            key: issue
            for key, issue in criterion_children(port).items()
            if key not in before
        }
        assert len(minted) == 1
        assert caught.value.marks == tuple(minted)
        assert await git(repo, "ls-remote", "origin", "refs/heads/native-test") == ""
    finally:
        await cleanup(workspace)


async def test_amending_one_record_leaves_another_records_designation_standing(
    repository,
):
    """Only the record the run amended is exempt; the other one still marks.

    Two records are pinned on the lane: one designates nothing and is the
    subject of a reproduced, amended claim; the other designates the boundary
    test, which the same commit weakens without a claim. The loss is marked,
    and the mark names the designating record, not the amended one.
    """
    repo = repository[0]
    port = tracker()
    designating = await designated_repository(repo, port)
    amended = Ruling.model_validate(
        ruling_data(
            issue_ref=SUBJECT,
            question="Which reading does the other record pin?",
        )
    )
    await port.post_comment(
        issue_key=SUBJECT,
        body=render_ruling(
            ruling=amended,
            lane_key=SUBJECT,
            marker_prefixes={"ruling": "fixture-pinned"},
        ),
    )
    before = criterion_children(port)
    resolution = "The corrected answer follows the reproduced base evidence."

    async def answers(title, payload, kwargs):
        await weaken(title, payload, kwargs)
        if title == "AmendmentTextOutput":
            payload["replacement"] = {
                "kind": "ruling",
                "subject": {"kind": "ruling", "id": amended.ruling_id},
                "resolution": resolution,
                "rejected_alternative": "The independently refuted prior reading.",
                "repo_evidence": ["policy.py"],
            }

    executor = Executor(
        reproduced=True,
        subject={"kind": "ruling", "id": amended.ruling_id},
        mutate=answers,
    )
    service, guard, workspace, port = await build(repository, executor, port=port)
    try:
        with pytest.raises(AssertionWeakenedError) as caught:
            await drive(service, guard, repository)

        # The claimed record was amended on the board; the designating one
        # was not.
        records = {
            record.ruling_id: record
            for _, record in await RulingRecordReader(
                tracker=port,
                operation=OperationConfig(
                    operation_name="fixture",
                    workspace="fixture",
                    marker_prefixes={"ruling": "fixture-pinned"},
                ),
            ).read_issue(issue_key=SUBJECT)
        }
        assert records[amended.ruling_id].resolution == resolution
        assert records[designating.ruling_id].resolution == designating.resolution
        minted = {
            key: issue
            for key, issue in criterion_children(port).items()
            if key not in before
        }
        assert len(minted) == 1
        (key,) = minted
        assert caught.value.marks == (key,)
        check = criterion_field_bodies(minted[key].body, field="Check")[0]
        assert designating.ruling_id in check
        assert amended.ruling_id not in check
        assert await git(repo, "ls-remote", "origin", "refs/heads/native-test") == ""
    finally:
        await cleanup(workspace)


async def test_a_mint_the_tracker_refuses_still_refuses_the_push(repository):
    """The board would not take the mark, so the commit is still not published."""
    repo = repository[0]
    port = tracker()
    await designated_repository(repo, port)
    before = criterion_children(port)

    async def unavailable(**kwargs):
        raise TrackerUnavailableError("the board will not take the mark")

    port.create_criterion_if_absent = unavailable
    executor = Executor(claim=False, mutate=weaken)
    service, guard, workspace, port = await build(repository, executor, port=port)
    try:
        with pytest.raises(TrackerUnavailableError):
            await drive(service, guard, repository)

        assert await git(repo, "ls-remote", "origin", "refs/heads/native-test") == ""
        assert criterion_children(port) == before
    finally:
        await cleanup(workspace)
