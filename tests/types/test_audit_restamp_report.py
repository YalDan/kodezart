"""A refuted restamp trace is complete only with its mandate verdict (KOD-516)."""

import pytest
from pydantic import ValidationError

from kodezart.types.domain.audit import AuditMandateObservation, AuditVerdict
from kodezart.types.domain.audit_evidence import (
    AuditRestampReport,
    AuditRestampTrace,
    restamp_defect_class,
)
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface

GRADED = "a" * 40
OTHER = "b" * 40
SURFACE = WritableSurface(
    kind=SurfaceKind.ISSUE_DESCRIPTION,
    ref=ScopeRef(kind=ScopeKind.ISSUE, key="native/issue"),
)


def trace(verdict: AuditVerdict) -> AuditRestampTrace:
    """A row graded at GRADED, against a stream whose last grading agrees or not."""
    return AuditRestampTrace(
        criterion_key="native/criterion",
        recorded_evidence=CriterionEvidence(graded_sha=GRADED, test="recorded"),
        history=(GRADED if verdict is AuditVerdict.HOLDS else OTHER,),
        verdict=verdict,
        reason="The row's commit against the last recorded grading.",
    )


def mandate(state: str, defect_class: str) -> AuditMandateObservation:
    """One mandate verdict in *state*, its finding naming *defect_class*."""
    return AuditMandateObservation(
        verdict=state,
        covered=()
        if state == "unverifiable"
        else (
            {
                "surface": SURFACE,
                "native_ref": "native/issue",
                "content": "Restamp the row.",
            },
        ),
        unreadable=({"surface": SURFACE, "reason": "Native read failed"},)
        if state == "unverifiable"
        else (),
        finding={
            "issue_id": "native/issue",
            "defect_class": defect_class,
            "role": "mandate",
            "mandate_text": "Restamp the row.",
            "evidence": "Exact source.",
        }
        if state == "holds"
        else None,
        finding_surface=SURFACE if state == "holds" else None,
        evidence="Native coverage evidence.",
    )


@pytest.mark.parametrize("state", ["holds", "refuted", "unverifiable"])
def test_a_refuted_restamp_requires_its_mandate_verdict(state):
    refuted = trace(AuditVerdict.REFUTED)
    report = AuditRestampReport(
        trace=refuted, mandate=mandate(state, restamp_defect_class(refuted))
    )
    assert report.trace is refuted
    assert report.mandate.verdict.value == state
    assert AuditRestampReport.model_validate_json(report.model_dump_json()) == report
    with pytest.raises(ValidationError, match="mandate"):
        AuditRestampReport(trace=refuted, mandate=None)


def test_a_holding_restamp_carries_none():
    held = trace(AuditVerdict.HOLDS)
    assert AuditRestampReport(trace=held, mandate=None).mandate is None
    with pytest.raises(ValidationError, match="mandate"):
        AuditRestampReport(
            trace=held, mandate=mandate("refuted", restamp_defect_class(held))
        )


def test_a_restamp_mandate_names_the_restamp_defect():
    refuted = trace(AuditVerdict.REFUTED)
    report = AuditRestampReport(
        trace=refuted, mandate=mandate("holds", restamp_defect_class(refuted))
    )
    assert report.defect_class() == restamp_defect_class(refuted)
    assert refuted.criterion_key in report.defect_class()
    with pytest.raises(ValidationError, match="another defect"):
        AuditRestampReport(trace=refuted, mandate=mandate("holds", "a foreign defect"))
