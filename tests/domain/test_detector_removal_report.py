"""Each demonstrated source loss retains its own mandate-completed report."""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.audit import AuditClaimReport, AuditMandateObservation
from kodezart.types.domain.audit_detection_removal import (
    DetectorRemovalObservation,
    DetectorRemovalReport,
    DetectorRemovalReportEntry,
)


def finding(suffix):
    return {
        "mechanism": {
            "path": f"mechanism_{suffix}.py",
            "line": 1,
            "text": f"def protected_{suffix}():\n    return 'present'\n",
        },
        "detector": {
            "path": f"test_{suffix}.py",
            "line": 1,
            "text": f"assert protected_{suffix}() == 'present'\n",
        },
        "absence_demonstration": (
            f"The removed {suffix} test fails at the current head."
        ),
    }


def observation(verdict="refuted"):
    return DetectorRemovalObservation(
        judgment={
            "criterion_key": "native/condition",
            "verdict": verdict,
            "evidence": "Current suite and historical counterfactual were executed.",
            "findings": [finding("z"), finding("a")] if verdict == "refuted" else [],
        },
        graded_sha="graded",
        head_sha="head",
        record_ref="native-comment",
        check="The current Check.",
    )


def complete(verdict="refuted"):
    observed = observation(verdict)
    return DetectorRemovalReport(
        observation=observed,
        reports=tuple(
            DetectorRemovalReportEntry(
                finding=item,
                report=AuditClaimReport(
                    claim=observed.claim(item),
                    mandate=AuditMandateObservation(
                        verdict="refuted",
                        covered=(),
                        unreadable=(),
                        finding=None,
                        finding_surface=None,
                        evidence="The provided set contains no mandating instruction.",
                    )
                    if item is not None
                    else None,
                ),
            )
            for item in observed.judgment.findings or (None,)
        ),
    )


@pytest.mark.parametrize("verdict", ["holds", "refuted", "unverifiable"])
def test_complete_report_preserves_native_evidence_and_serializes(verdict):
    result = complete(verdict)
    assert DetectorRemovalReport.model_validate_json(result.model_dump_json()) == result
    assert len(result.reports) == (2 if verdict == "refuted" else 1)
    for entry in result.reports:
        assert entry.report.claim.judgment.verdict.value == verdict
        if entry.finding is not None:
            assert (
                entry.finding.model_dump_json() in entry.report.claim.judgment.evidence
            )
            assert entry.report.mandate is not None
    with pytest.raises(ValidationError, match="frozen"):
        result.reports = ()


@pytest.mark.parametrize("damage", ["omit", "duplicate", "swap", "null", "foreign"])
def test_findings_cannot_be_dropped_or_reassociated(damage):
    data = complete().model_dump()
    rows = list(data["reports"])
    if damage == "omit":
        rows.pop()
    elif damage == "duplicate":
        rows.append(rows[0])
    elif damage == "swap":
        rows.reverse()
    elif damage == "null":
        rows[0]["finding"] = None
    else:
        rows[0]["finding"] = finding("foreign")
    data["reports"] = rows
    with pytest.raises(ValidationError, match=r"complete report|differs"):
        DetectorRemovalReport.model_validate(data)


@pytest.mark.parametrize(
    "field", ["criterion_key", "evidence", "head_sha", "record_ref", "check", "mandate"]
)
def test_source_identity_and_mandate_cannot_be_lost(field):
    data = complete().model_dump()
    report = data["reports"][0]["report"]
    if field == "mandate":
        report[field] = None
    elif field in {"criterion_key", "evidence"}:
        report["claim"]["judgment"][field] = "foreign"
    else:
        report["claim"][field] = "foreign"
    with pytest.raises(ValidationError, match=r"differs|mandate"):
        DetectorRemovalReport.model_validate(data)


def test_refuted_observation_cannot_be_projected_without_its_finding():
    with pytest.raises(ValueError, match="requires its finding"):
        observation().claim(None)


def test_unobserved_finding_cannot_enter_a_quiet_result():
    with pytest.raises(ValueError, match="outside"):
        observation("holds").claim(observation().judgment.findings[0])
