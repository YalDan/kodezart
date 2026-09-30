"""Runtime evidence separates publication, interrupted repair and completed coverage."""

import pytest
from pydantic import TypeAdapter, ValidationError

from kodezart.types.domain.audit import (
    AuditClaimReport,
    AuditCoverageResult,
    TrackerArtifact,
)
from kodezart.types.domain.audit_runtime import (
    AuditClaimPublication,
    AuditForgePublication,
    AuditPublication,
    AuditPublishedArtifact,
    AuditRepairInput,
    AuditRunReport,
    AuditScopeComplete,
    AuditScopeSummary,
)
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.write_back import WriteBackFinding
from tests.tracker.conftest import FIXTURE_NOW

SCOPE = ScopeRef(kind=ScopeKind.ISSUE, key="actual-native-scope")
SURFACE = WritableSurface(
    kind=SurfaceKind.MARKER_COMMENT, ref=SCOPE, marker="[audit:lane:occurrence]"
)


def claim(verdict="holds"):
    return AuditClaimReport.model_validate(
        {
            "claim": {
                "judgment": {
                    "criterion_key": "native-child",
                    "verdict": verdict,
                    "evidence": "Actual source observation.",
                },
                "head_sha": "a" * 40,
                "record_ref": "actual-native-comment",
                "check": "Current Check",
            },
            "mandate": None,
        }
    )


def test_unverifiable_observation_cannot_become_an_authorized_artifact():
    with pytest.raises(ValidationError, match="unverifiable"):
        AuditPublishedArtifact(
            publication=AuditClaimPublication(
                detector="current_check", report=claim("unverifiable")
            ),
            escalation_refs=(),
        )


@pytest.mark.parametrize("refs", [("",), ("native", "native")])
def test_escalation_references_are_real_nonblank_distinct_values(refs):
    with pytest.raises(ValidationError):
        AuditPublishedArtifact(
            publication=AuditForgePublication(graded_sha="a" * 40, report=claim()),
            escalation_refs=refs,
        )


def test_detector_union_is_closed_and_does_not_accept_ordinal_removal_claims():
    with pytest.raises(ValidationError):
        TypeAdapter(AuditPublication).validate_python(
            {"kind": "claim", "detector": "detector_removal:0", "report": claim()}
        )


@pytest.mark.parametrize(
    "field,value",
    [("ref", "branch-name"), ("preceding_round", 0), ("preceding_round", 1.5)],
)
def test_repair_input_requires_exact_commit_and_actual_positive_round(field, value):
    data = {
        "surface": SURFACE,
        "ref": "a" * 40,
        "preceding_round": 1,
        "finding": WriteBackFinding(
            verdict="unverifiable", evidence="A source was unavailable."
        ),
    }
    data[field] = value
    with pytest.raises(ValidationError):
        AuditRepairInput.model_validate(data)


def test_unverifiable_received_repair_input_is_preserved_without_inventing_artifact():
    finding = WriteBackFinding(
        verdict="unverifiable", evidence="A source was unavailable."
    )
    received = AuditRepairInput(
        surface=SURFACE, ref="a" * 40, preceding_round=1, finding=finding
    )
    assert received.finding == finding
    assert "artifact" not in received.model_dump()
    assert "verdict" not in received.model_dump()


def test_complete_coverage_requires_a_verified_publication_receipt():
    with pytest.raises(ValidationError):
        AuditScopeComplete(
            scope=SCOPE,
            writes=(),
            coverage=AuditCoverageResult(
                scope=SCOPE, observed_at=FIXTURE_NOW, full=True, covered=()
            ),
        )


def test_report_cannot_borrow_another_run_kind():
    with pytest.raises(ValidationError):
        AuditRunReport(
            identity=RunIdentity(
                kind=RunKind.GROOMING, name="audit", started_at=FIXTURE_NOW
            ),
            scopes=(),
        )


def test_historical_forge_payload_cannot_alias_a_different_current_head():
    with pytest.raises(ValidationError, match="recorded Evidence SHA"):
        AuditForgePublication(graded_sha="b" * 40, report=claim())
    publication = AuditForgePublication(graded_sha="a" * 40, report=claim())
    assert publication.model_dump()["kind"] == "forge"
    assert publication.model_dump()["graded_sha"] == "a" * 40


@pytest.mark.parametrize("width", [40, 64])
def test_runtime_evidence_preserves_both_canonical_commit_widths(width):
    commit = "a" * width
    values = claim().model_dump()
    values["claim"]["head_sha"] = commit
    report = AuditClaimReport.model_validate(values)
    assert AuditForgePublication(graded_sha=commit, report=report).graded_sha == commit
    received = AuditRepairInput(
        surface=SURFACE,
        ref=commit,
        preceding_round=1,
        finding=WriteBackFinding(
            verdict="unverifiable", evidence="Source unavailable."
        ),
    )
    assert received.ref == commit


@pytest.mark.parametrize("damage", ["wrong_ref", "duplicate_ref", "duplicate_address"])
def test_summary_cannot_claim_unmatched_or_duplicate_native_records(damage):
    record = TrackerArtifact(
        surface=SURFACE, native_ref="native-comment", content="Exact native body"
    )
    data = {
        "identity": RunIdentity(
            kind=RunKind.AUDIT, name="audit", started_at=FIXTURE_NOW
        ),
        "coverage": AuditCoverageResult(
            scope=SCOPE, observed_at=FIXTURE_NOW, full=True, covered=()
        ),
        "record_refs": (record.native_ref,),
        "records": (record,),
    }
    assert AuditScopeSummary.model_validate(data).records == (record,)
    if damage == "wrong_ref":
        data["record_refs"] = ("foreign-native-comment",)
    elif damage == "duplicate_ref":
        data["record_refs"] = (record.native_ref, record.native_ref)
        data["records"] = (record, record)
    else:
        other = TrackerArtifact(
            surface=SURFACE,
            native_ref="other-native-comment",
            content="Other native body",
        )
        data["record_refs"] = (record.native_ref, other.native_ref)
        data["records"] = (record, other)
    with pytest.raises(ValidationError):
        AuditScopeSummary.model_validate(data)
