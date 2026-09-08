"""Native criterion records retain state and Evidence across port reads and edits."""

import pytest

from kodezart.domain.criterion_evidence import (
    parse_criterion_evidence,
    render_evidence_field,
)
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.fakes import FakeMcpIssue
from tests.tracker.conftest import fixture_server

PARENT = "native-parent/forty-two"
LABEL = "acceptance-condition"
STATES = (
    ("Intake", "triage"),
    ("Unscheduled", "backlog"),
    ("Ready", "unstarted"),
    ("Under verification", "started"),
    ("Satisfied", "completed"),
    ("Withdrawn", "canceled"),
    ("Superseded duplicate", "duplicate"),
)
SHAS = (None, "0123456789abcdef" * 2 + "fedcba98", "fedcba9876543210" * 4)


@pytest.fixture(params=SHAS, ids=["ungraded", "sha1", "sha256"])
def graded_sha(request):
    return request.param


@pytest.fixture(params=["\n", "\r\n"], ids=["lf", "crlf"])
def body(graded_sha, request):
    evidence = (
        "**Evidence:** —"
        if graded_sha is None
        else render_evidence_field(
            CriterionEvidence(graded_sha=graded_sha, test="native boundary · β")
        )
    )
    return ("\n**Check:** Preserve the native record.\n\n" + evidence + "\n").replace(
        "\n", request.param
    )


@pytest.fixture
def server(body):
    server = fixture_server()
    server.issues[PARENT] = FakeMcpIssue(id=PARENT)
    for index, (name, kind) in enumerate(STATES):
        key = f"native-criterion/{index}-opaque"
        server.issues[key] = FakeMcpIssue(
            id=key,
            parent_id=PARENT,
            labels=[LABEL, "ordinary-label"],
            status=name,
            status_type=kind,
            description=body,
        )
    return server


async def test_every_native_state_and_evidence_survives_the_port_round_trip(
    tracker, tracker_writes, body, graded_sha
):
    assert {kind for _, kind in STATES} == {kind.value for kind in WorkflowStateKind}
    writes_before = tracker_writes()
    rows = tuple(await tracker.read_criteria(issue_key=PARENT))
    assert {row.issue_key for row in rows} == {
        f"native-criterion/{index}-opaque" for index in range(len(STATES))
    }
    for index, (name, kind) in enumerate(STATES):
        key = f"native-criterion/{index}-opaque"
        row = next(row for row in rows if row.issue_key == key)
        assert row == await tracker.read_issue(issue_key=key)
        assert row.parent_key == PARENT
        assert row.issue_labels == frozenset({"criterion"})
        assert row.state_name == name
        assert row.state_kind.value == kind
        assert row.body == body
        if graded_sha is None:
            with pytest.raises(ValueError, match="explicit fenced JSON"):
                parse_criterion_evidence(row.body)
        else:
            assert parse_criterion_evidence(row.body).graded_sha == graded_sha
    assert tracker_writes() == writes_before

    # Editing this existing native carrier preserves its other fields. No
    # separate criterion artifact or inferred grading state participates.
    replacement = body.replace("Preserve the native record.", "Updated check wording.")
    for row in rows:
        written = await tracker.update_issue(issue_key=row.issue_key, body=replacement)
        expected = row.model_copy(update={"body": replacement})
        assert written.issue_key == expected.issue_key
        assert written.parent_key == expected.parent_key
        assert written.issue_labels == expected.issue_labels
        assert written.state_name == expected.state_name
        assert written.state_kind is expected.state_kind
        assert written.body == replacement
        reread = await tracker.read_issue(issue_key=row.issue_key)
        assert reread == written
    current = {
        row.issue_key: row for row in await tracker.read_criteria(issue_key=PARENT)
    }
    assert set(current) == {row.issue_key for row in rows}
    for old in rows:
        new = current[old.issue_key]
        assert (new.state_name, new.state_kind, new.issue_labels) == (
            old.state_name,
            old.state_kind,
            old.issue_labels,
        )
        assert new.body == replacement
        if graded_sha is not None:
            assert parse_criterion_evidence(new.body).graded_sha == graded_sha
