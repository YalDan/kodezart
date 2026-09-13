from typing import Literal, assert_type
from kodezart.types.domain.audit import (
    AuditClaimObservation, AuditClaimReport, AuditMandateObservation,
    AuditVerdict, RefutedClaimReport, UnrefutedClaimReport,
)

def consume(report: AuditClaimReport) -> AuditClaimObservation:
    arm = report.root
    if isinstance(arm, RefutedClaimReport):
        assert_type(arm.mandate, AuditMandateObservation)
        assert_type(arm.claim.judgment.verdict, Literal[AuditVerdict.REFUTED])
    else:
        assert_type(arm, UnrefutedClaimReport)
        assert_type(arm.mandate, None)
        assert_type(arm.claim.judgment.verdict, Literal[AuditVerdict.HOLDS, AuditVerdict.UNVERIFIABLE])
    return arm.claim
