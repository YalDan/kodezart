"""Mandate-completed reports cannot drop or relabel observed categories."""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.audit import AuditClaimObservation, AuditClaimReport
from kodezart.types.domain.audit_overclaim import (
    AuditOverclaimObservation,
    AuditOverclaimReport,
    OverclaimKind,
    OverclaimReportEntry,
)
from tests.domain.test_audit_overclaim import judgment


def complete():
    observed = AuditOverclaimObservation(
        judgment=judgment(),
        graded_sha="graded",
        head_sha="head",
        record_ref="native-comment",
        check="The current Check.",
    )
    return AuditOverclaimReport(
        observation=observed,
        reports=tuple(
            OverclaimReportEntry(
                kind=reading.kind,
                report=AuditClaimReport(
                    claim=AuditClaimObservation(
                        judgment={
                            "criterion_key": observed.judgment.criterion_key,
                            "verdict": reading.verdict,
                            "evidence": reading.evidence,
                        },
                        head_sha=observed.head_sha,
                        record_ref=observed.record_ref,
                        check=observed.check,
                    ),
                    mandate=None,
                ),
            )
            for reading in observed.judgment.checks
        ),
    )


def test_exact_report_survives_serialization_with_all_categories():
    result = complete()
    assert AuditOverclaimReport.model_validate_json(result.model_dump_json()) == result
    assert tuple(row.kind for row in result.reports) == tuple(OverclaimKind)
    with pytest.raises(ValidationError, match="frozen"):
        result.reports = ()


@pytest.mark.parametrize("damage", ["omit", "duplicate", "swap", "relabel"])
def test_equal_reading_text_cannot_hide_missing_or_reordered_categories(damage):
    result = complete().model_dump()
    rows = list(result["reports"])
    if damage == "omit":
        rows.pop()
    elif damage == "duplicate":
        rows.append(rows[0])
    elif damage == "swap":
        rows[0], rows[1] = rows[1], rows[0]
    else:
        rows[0]["kind"] = OverclaimKind.SELF_RULE
    result["reports"] = rows
    with pytest.raises(ValidationError, match=r"reading|categories"):
        AuditOverclaimReport.model_validate(result)


@pytest.mark.parametrize(
    "field", ["criterion_key", "verdict", "evidence", "head_sha", "record_ref", "check"]
)
def test_reports_cannot_change_native_identity_or_observed_judgment(field):
    result = complete().model_dump()
    claim = result["reports"][0]["report"]["claim"]
    if field in {"criterion_key", "verdict", "evidence"}:
        claim["judgment"][field] = "unverifiable" if field == "verdict" else "foreign"
    else:
        claim[field] = "foreign"
    with pytest.raises(ValidationError, match="differs"):
        AuditOverclaimReport.model_validate(result)


def test_refuted_category_cannot_be_returned_without_its_mandate():
    result = complete().model_dump()
    result["observation"]["judgment"]["checks"][3]["verdict"] = "refuted"
    result["reports"][3]["report"]["claim"]["judgment"]["verdict"] = "refuted"
    with pytest.raises(ValidationError, match="mandate"):
        AuditOverclaimReport.model_validate(result)
