"""Actual precommit graph, canonical write-back and Git publication ordering."""

import json
from pathlib import Path

import pytest

from kodezart.domain.amendment import AmendmentWriteBackRefusalError
from kodezart.domain.fire_spec import criterion_field_bodies
from kodezart.domain.rulings import render_ruling
from kodezart.types.domain.agent import NativeAmendmentEvent, ResultEvent
from kodezart.types.domain.amendment import AmendmentGround, UpheldReason
from kodezart.types.domain.amendment_write import AmendmentRecord
from kodezart.types.domain.operation import CheckPrerequisite
from kodezart.types.domain.tracker import WorkflowStateKind
from kodezart.types.domain.tracker_writes import DescriptionEditResult
from tests.chains.test_native_fire import DIRECT_DONE, DIRECT_OWED, SUBJECT, tracker
from tests.services.test_native_amendments import (
    AMENDED_CHECK,
    PROTECTED_BODY,
    PROTECTED_NAME,
    PROTECTED_PATH,
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


async def test_undemonstrable_here_upholds_at_the_environment_reason_touching_nothing(
    repository,
):
    """Undemonstrable here is a non-ground: the claim is refused, not actioned.

    The Do's further clause — routing the claim to the parent lane's state — has
    no production symbol at this head and is not built here; the capability is
    named on the report and inside the recorded refusal.
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
        assert refusal.publication.kind == "recorded"
        record = refusal.publication.record.artifact
        assert '"claimedCapability":"network"' in record.content
        assert record.surface.ref.key == DIRECT_DONE
        settled = port.issues[DIRECT_DONE]
        assert settled.model_dump(exclude={"updated_at"}) == before.model_dump(
            exclude={"updated_at"}
        )
        assert settled.state_kind is WorkflowStateKind.COMPLETED
        assert [c["output_format"]["schema"]["title"] for c in executor.calls] == [
            "NativeWriterOutput",
            "AmendmentJudgment",
            "WriteBackFinding",
        ]
        assert not any(isinstance(e, ResultEvent) for e in events)
        assert (
            await git(repository[0], "ls-remote", "origin", "refs/heads/native-test")
            == ""
        )
    finally:
        await cleanup(workspace)


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
