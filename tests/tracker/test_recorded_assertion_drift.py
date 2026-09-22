"""Native ruling designations drive immutable Git comparison without judgments."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import structlog.testing

from kodezart.adapters.git.bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.git.service import SubprocessGitService
from kodezart.adapters.git.source_reader import SubprocessGitSourceReader
from kodezart.config.app import AppConfig
from kodezart.domain.errors import (
    AssertionComparisonError,
    AuditEvidenceReadError,
    RulingRecordReadError,
)
from kodezart.domain.rulings import render_ruling
from kodezart.services.assertion_drift import AssertionDriftDetector
from kodezart.services.audit_sources import AuditSourceReader
from kodezart.services.criterion_sources import NativeCriterionResolver
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.recorded_assertion_drift import (
    PROTECTION_LAPSED,
    RecordedAssertionDriftDetector,
)
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.agent import Ruling, RulingProtectedTestRef
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import TrackerComment, WorkflowStateKind
from tests.domain.test_rulings import ruling_data
from tests.fakes import FakeTrackerPort
from tests.services.test_assertion_drift import (
    PATH,
    commit,
    git,
    run_protected_test,
    source,
)
from tests.services.test_assertion_drift import (
    repo as repo,
)
from tests.tracker import test_audit_evidence as fixtures
from tests.tracker.conftest import STATE_TYPES, linear_over_fake_mcp
from tests.tracker.lease_fixtures import leased_comment

claim_setup = fixtures.claim_setup
server = fixtures.server
ROOT, CHILD = fixtures.ROOT, fixtures.CHILD
PREFIXES = {**fixtures.PREFIXES, "ruling": "fixture-protected-ruling"}
OPERATION = OperationConfig(
    operation_name="fixture",
    workspace="fixture",
    marker_prefixes=PREFIXES,
    workflow_states=fixtures.WORKFLOW_STATE_NAMES,
)
#: The backend state name this workspace carries for the duplicate kind, read
#: off the workspace's own state table: a board closes a condition as a
#: duplicate under its own word for it, and the kind is what a consumer reads.
(DUPLICATE_STATE,) = (
    name
    for name, kind in STATE_TYPES.items()
    if kind == WorkflowStateKind.DUPLICATE.value
)


async def seed(
    tracker, *, owner=ROOT, designation="named", question="Which value is pinned?"
):
    data = ruling_data(issue_ref=owner, question=question)
    if designation == "named":
        data["protected_tests"] = (
            RulingProtectedTestRef(
                source_ref=data["ruling_id"], path=PATH, qualified_name="test_contract"
            ),
        )
    elif designation == "empty":
        data["protected_tests"] = ()
    ruling = Ruling.model_validate(data)
    body = render_ruling(
        ruling=ruling, lane_key=fixtures.REQUEST.lane_key, marker_prefixes=PREFIXES
    )
    marker, payload = body.split("\n", 1)
    comment = await leased_comment(tracker, target=owner, marker=marker, body=payload)
    return comment, ruling


@pytest.fixture
async def native(claim_setup, tracker, repo, tmp_path):
    graded = commit(repo, source(1))
    head = commit(repo, source(2))
    git(repo, "branch", "-M", "ordinary-name")
    await tracker.update_issue(issue_key=CHILD, body=fixtures.body(graded))
    await tracker.restore_workflow_state(issue_key=CHILD, state_name="Done")
    native_git = SubprocessGitService(remote="configured-remote")
    git_source = SubprocessGitSourceReader()
    cache = LocalBareRepoCache(git=native_git, base_dir=str(tmp_path / "cache"))
    request = fixtures.REQUEST.model_copy(update={"repo_url": repo.as_uri()})

    def build(port=tracker, detector=None):
        return RecordedAssertionDriftDetector(
            tracker=port,
            sources=AuditSourceReader(
                resolver=NativeCriterionResolver(tracker=port),
                records=LaneRecordReader(tracker=port, operation=OPERATION),
                git=native_git,
                source=git_source,
                cache=cache,
                operation=OPERATION,
                remote=AppConfig(git={"remote": "configured-remote"}).git.remote,
            ),
            rulings=RulingRecordReader(tracker=port, operation=OPERATION),
            detector=detector or AssertionDriftDetector(git=git_source),
        )

    return build, request, graded, head


async def absorbed(tracker, *, parent=ROOT):
    """A second criterion under *parent*, closed as a duplicate of its neighbour.

    Minted through the port and closed through the port, so its key is the
    backend's own and the case reads the same over either implementation. The
    board is what closes a condition as a duplicate of the one that absorbed
    it; nothing under test writes that state.
    """
    surfaces = frozenset(
        {
            WritableSurface(
                kind=SurfaceKind.CRITERION_CHILD_SET,
                ref=ScopeRef(kind=ScopeKind.ISSUE, key=parent),
            )
        }
    )
    holder = uuid4().hex
    async with RunSurfaceLease(
        tracker=tracker, job_id=holder, surfaces=surfaces, lease_seconds=900.0
    ):
        made = await tracker.create_criterion_if_absent(
            parent_key=parent,
            title="The condition its neighbour absorbed",
            check="The absorbed condition states this same check.",
            do="Do what the absorbed condition names.",
            holder=holder,
        )
    closed = await tracker.restore_workflow_state(
        issue_key=made.issue_key, state_name=DUPLICATE_STATE
    )
    assert closed.state_kind is WorkflowStateKind.DUPLICATE
    return closed


async def test_a_superseded_criterion_still_supplies_its_protected_designation(
    native, tracker, repo
):
    """A closed-as-duplicate identity stays resolvable, so its record still counts.

    This is the one production site that reads a protected-test designation
    back, and the family it gathers is where a superseded identity could
    quietly stop existing. Filter the duplicate out of that read and the
    designation carried by the absorbed condition vanishes from the comparison
    with nothing refused — a silent loss, which is exactly what the clause
    that a superseded identity stays resolvable forbids (KOD-622).

    The request stays keyed by the surviving condition: the source read
    requires a completed or configured-review claim, so an audit claim keyed
    by a duplicate is refused upstream of the family read and cannot express
    this. What the case pins is the family read, over the whole owner set.
    """
    build, request, graded, head = native
    superseded = await absorbed(tracker)
    comment, _ruling = await seed(tracker, owner=superseded.issue_key)
    git(repo, "checkout", "--detach", graded)
    run_protected_test(repo)
    git(repo, "checkout", "ordinary-name")
    run_protected_test(repo)

    (claim,) = await build().compare(request)

    # Non-vacuous: the designation reaching the detector is the one recorded
    # against the superseded identity, and that identity is in the family the
    # comparer read rather than only on the board.
    assert claim.protected_test.source_ref == comment.comment_key
    assert claim.protected_test.path == PATH
    assert claim.protected_test.qualified_name == "test_contract"
    assert claim.graded_sha == graded and claim.head_sha == head
    assert superseded.issue_key != request.criterion_key
    assert superseded.issue_key in {
        row.issue_key for row in await tracker.read_criteria(issue_key=ROOT)
    }


@pytest.mark.parametrize("owner", [ROOT, CHILD])
async def test_native_designation_detects_both_green_assertion_drift(
    native, tracker, tracker_writes, repo, owner
):
    build, request, graded, head = native
    comment, ruling = await seed(tracker, owner=owner)
    git(repo, "checkout", "--detach", graded)
    run_protected_test(repo)
    git(repo, "checkout", "ordinary-name")
    run_protected_test(repo)
    before = tracker_writes()
    (claim,) = await build().compare(request)
    assert claim.protected_test.source_ref == comment.comment_key
    assert claim.protected_test.source_ref != ruling.ruling_id
    assert claim.protected_test.path == PATH
    assert claim.protected_test.qualified_name == "test_contract"
    assert claim.graded_sha == graded and claim.head_sha == head
    assert claim.before[0].expression == "implementation() == 1"
    assert claim.after[0].expression == "implementation() == 2"
    assert tracker_writes() == before
    assert git(repo, "status", "--porcelain") == ""


async def test_adding_an_unrelated_test_is_quiet(native, tracker, repo):
    build, request, graded, _ = native
    await seed(tracker)
    git(repo, "reset", "--hard", graded)
    commit(repo, source(1) + "\ndef test_added():\n    assert True\n")
    assert await build().compare(request) == ()


@pytest.mark.parametrize("designation", ["unknown", "empty", "no-records"])
async def test_an_unknown_designation_lapses_and_a_read_empty_one_stays_quiet(
    native, tracker, tracker_writes, designation
):
    """An absent designation drops that record's protection and is recorded.

    The explicitly empty designation and the absent record set are separate
    facts and neither lapses: only a null designation does, and the lapse is
    named against the comment key and the identity that owns it.
    """
    build, request, *_ = native
    comment = ruling = None
    if designation != "no-records":
        comment, ruling = await seed(tracker, designation=designation)
    before = tracker_writes()

    with structlog.testing.capture_logs() as logs:
        assert await build().compare(request) == ()

    lapses = [entry for entry in logs if entry["event"] == PROTECTION_LAPSED]
    if designation == "unknown":
        assert comment is not None and ruling is not None
        assert [(entry["source_ref"], entry["ruling_id"]) for entry in lapses] == [
            (comment.comment_key, ruling.ruling_id)
        ]
    else:
        assert lapses == []
    assert tracker_writes() == before


async def test_a_duplicate_comment_key_still_refuses_when_its_designation_is_absent(
    native, tracker, monkeypatch
):
    """The comment-key refusal is decided before protection lapses.

    A record whose designation is absent is still one the reader must be able
    to address, so a shared or blank comment key refuses the comparison rather
    than lapsing quietly out of it.
    """
    build, request, *_ = native
    await seed(tracker, designation="unknown")
    await seed(tracker, owner=CHILD, designation="unknown")
    consumer = build()
    original = consumer._rulings.read_all

    async def reused(**kwargs):
        rows = await original(**kwargs)
        return tuple(
            (comment.model_copy(update={"comment_key": "shared-native-key"}), ruling)
            for comment, ruling in rows
        )

    monkeypatch.setattr(consumer._rulings, "read_all", reused)
    with pytest.raises(AssertionComparisonError, match="native comment key"):
        await consumer.compare(request)


async def test_each_owner_and_question_remains_a_separate_native_source(
    native, tracker
):
    build, request, *_ = native
    first, _ = await seed(tracker)
    second, _ = await seed(tracker, owner=CHILD)
    third, _ = await seed(tracker, question="A separate pin of this test?")
    claims = await build().compare(request)
    assert {claim.protected_test.source_ref for claim in claims} == {
        first.comment_key,
        second.comment_key,
        third.comment_key,
    }
    assert len(claims) == 3


async def test_cold_native_reader_retains_designation(native, tracker, server):
    build, request, *_ = native
    stored, _ = await seed(tracker)
    if isinstance(tracker, FakeTrackerPort):
        cold = FakeTrackerPort()
        cold.issues = tracker.issues.copy()
        cold.comments = [
            TrackerComment.model_validate_json(row.model_dump_json())
            for row in tracker.comments
        ]
    else:
        cold = linear_over_fake_mcp(server)
    (claim,) = await build(port=cold).compare(request)
    assert claim.protected_test.source_ref == stored.comment_key


async def test_prose_evidence_and_other_lane_records_never_designate_tests(
    native, tracker
):
    build, request, graded, _ = native
    await tracker.update_issue(
        issue_key=CHILD,
        body=fixtures.body(graded).replace(fixtures.TEST, f"{PATH}::test_contract"),
    )
    await tracker.post_comment(issue_key=ROOT, body=f"Protect {PATH}::test_contract")
    other = Ruling.model_validate(ruling_data(issue_ref=ROOT))
    await tracker.post_comment(
        issue_key=ROOT,
        body=render_ruling(
            ruling=other, lane_key="another-lane", marker_prefixes=PREFIXES
        ),
    )
    assert await build().compare(request) == ()


@pytest.mark.parametrize("damage", ["duplicate", "foreign-protection", "malformed"])
async def test_unreadable_designation_never_becomes_a_clean_result(
    native, tracker, damage
):
    build, request, *_ = native
    stored, ruling = await seed(tracker)
    if damage == "duplicate":
        await tracker.post_comment(issue_key=ROOT, body=stored.body)
    else:
        marker, payload = stored.body.split("\n", 1)
        if damage == "malformed":
            payload = "Unreadable record."
        else:
            payload = payload.replace(
                f'"sourceRef": "{ruling.ruling_id}"', '"sourceRef": "another-ruling"'
            )
        await leased_comment(tracker, target=ROOT, marker=marker, body=payload)
    with pytest.raises(RulingRecordReadError):
        await build().compare(request)


@pytest.mark.parametrize(
    "change", ["ruling", "add-ruling", "family", "evidence", "record", "head"]
)
async def test_changed_native_inputs_refuse_before_returning_claims(
    native, tracker, repo, monkeypatch, change
):
    build, request, graded, _ = native
    _stored, ruling = await seed(tracker)
    source = SubprocessGitSourceReader()
    original = source.read_source
    changed = False

    async def read(**kwargs):
        nonlocal changed
        value = await original(**kwargs)
        if not changed:
            changed = True
            if change == "ruling":
                amended = ruling.model_copy(
                    update={"resolution": "A new pinned answer"}
                )
                text = render_ruling(
                    ruling=amended, lane_key=request.lane_key, marker_prefixes=PREFIXES
                )
                marker, body = text.split("\n", 1)
                await leased_comment(tracker, target=ROOT, marker=marker, body=body)
            elif change == "add-ruling":
                await seed(tracker, question="A newly pinned question?")
            elif change == "family":
                listing = tracker.read_criteria

                async def amended_family(**address):
                    rows = await listing(**address)
                    return [
                        *rows,
                        rows[0].model_copy(update={"issue_key": "another-criterion"}),
                    ]

                monkeypatch.setattr(tracker, "read_criteria", amended_family)
            elif change == "evidence":
                await tracker.update_issue(
                    issue_key=CHILD, body=fixtures.body(graded) + "\nChanged source"
                )
            elif change == "record":
                comments = await tracker.list_comments(issue_key=ROOT)
                record = next(
                    row for row in comments if row.body.startswith("[audit-fixture:")
                )
                marker, body = record.body.split("\n", 1)
                amended = body.replace('"commitsAhead": 2', '"commitsAhead": 3')
                assert amended != body
                await leased_comment(tracker, target=ROOT, marker=marker, body=amended)
            else:
                commit(repo, source_text(3))
        return value

    monkeypatch.setattr(source, "read_source", read)
    with pytest.raises((AssertionComparisonError, AuditEvidenceReadError)):
        await build(detector=AssertionDriftDetector(git=source)).compare(request)


# Keep the imported source fixture separate from the per-test native reader.
source_text = source


async def test_cancelled_native_ruling_read_is_not_a_clean_comparison(
    native, tracker, monkeypatch
):
    build, request, *_ = native
    consumer = build()
    monkeypatch.setattr(
        consumer._rulings, "read_all", AsyncMock(side_effect=asyncio.CancelledError())
    )
    with pytest.raises(asyncio.CancelledError):
        await consumer.compare(request)


@pytest.mark.parametrize("damage", ["duplicate", "parent", "classification", "body"])
async def test_current_family_must_still_match_the_captured_source(
    native, tracker, monkeypatch, damage
):
    build, request, *_ = native
    await seed(tracker)
    original = tracker.read_criteria
    count = 0

    async def changed_family(**kwargs):
        nonlocal count
        rows = list(await original(**kwargs))
        count += 1
        # The actual snapshot reads its criterion twice. Change only the next
        # complete family, after that successful source snapshot was captured.
        if count == 3:
            if damage == "duplicate":
                return rows * 2
            field, value = {
                "parent": ("parent_key", "another-parent"),
                "classification": ("issue_labels", frozenset()),
                "body": ("body", rows[0].body + "\nNew wording"),
            }[damage]
            rows[0] = rows[0].model_copy(update={field: value})
        return rows

    monkeypatch.setattr(tracker, "read_criteria", changed_family)
    with pytest.raises(AssertionComparisonError):
        await build().compare(request)


@pytest.mark.parametrize("native_key", ["", "shared-native-key"])
async def test_comment_identity_is_required_across_the_complete_owner_set(
    native, tracker, monkeypatch, native_key
):
    build, request, *_ = native
    await seed(tracker)
    await seed(tracker, owner=CHILD)
    consumer = build()
    original = consumer._rulings.read_all

    async def reused(**kwargs):
        rows = await original(**kwargs)
        return tuple(
            (comment.model_copy(update={"comment_key": native_key}), ruling)
            for comment, ruling in rows
        )

    monkeypatch.setattr(consumer._rulings, "read_all", reused)
    with pytest.raises(AssertionComparisonError, match="native comment key"):
        await consumer.compare(request)


@pytest.mark.parametrize("initial", ["empty", "no-records"])
async def test_an_empty_projection_is_not_returned_after_designation_changes(
    native, tracker, monkeypatch, initial
):
    build, request, *_ = native
    if initial == "empty":
        await seed(tracker, designation="empty")
    native_source = SubprocessGitSourceReader()
    resolve = native_source.resolve_commit
    changed = False

    async def designate(**kwargs):
        nonlocal changed
        result = await resolve(**kwargs)
        if not changed:
            changed = True
            await seed(tracker)
        return result

    monkeypatch.setattr(native_source, "resolve_commit", designate)
    with pytest.raises(AssertionComparisonError, match="ruling records changed"):
        await build(detector=AssertionDriftDetector(git=native_source)).compare(request)
