"""Declared read failures retained as unavailable by concrete audit consumers."""

from pydantic import ValidationError

from kodezart.core.errors import (
    NoStructuredOutputError,
    TrackerAccessDeniedError,
    TrackerProtocolError,
    TrackerUnavailableError,
    TrackerWriterAttributionError,
)
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import (
    AgentSDKError,
    AssertionComparisonError,
    AuditClaimReadError,
    AuditEvidenceReadError,
    CheckObservationError,
    CriterionReadError,
    CriterionResolutionError,
    ForgeAPIError,
    GitOperationError,
    GitRepositoryError,
    GitSourceReadError,
    IssueLabelReadError,
    LaneRecordReadError,
    OutboundContentBlockedError,
    PrincipalAuthoredSurfaceError,
    PRStateReadError,
    RulingRecordReadError,
    ScopeReadError,
    SurfaceLeaseError,
    SurfaceLeaseLostError,
    SurfaceWriteAttributionError,
    TransientAPIError,
    WorkspaceError,
    WriteBackReadError,
)
from kodezart.types.domain.criterion_evidence import CriterionEvidence

# Programming failures (including generic ValueError/RuntimeError) do not
# become audit observations. Each port owns its operational error taxonomy.
AUDIT_READ_FAILURES = (
    AgentSDKError,
    NoStructuredOutputError,
    TrackerAccessDeniedError,
    TrackerProtocolError,
    TrackerUnavailableError,
    AuditClaimReadError,
    AuditEvidenceReadError,
    CheckObservationError,
    CriterionResolutionError,
    ForgeAPIError,
    GitOperationError,
    GitRepositoryError,
    GitSourceReadError,
    LaneRecordReadError,
    PRStateReadError,
    ScopeReadError,
    TransientAPIError,
    WorkspaceError,
    WriteBackReadError,
    ValidationError,
)


AUDIT_PUBLICATION_FAILURES = (
    *AUDIT_READ_FAILURES,
    IssueLabelReadError,
    OutboundContentBlockedError,
    PrincipalAuthoredSurfaceError,
    SurfaceLeaseError,
    SurfaceLeaseLostError,
    SurfaceWriteAttributionError,
    TrackerWriterAttributionError,
)


# The assertion-drift comparison's own refusals, and its criterion family
# read, are not audit read failures: widening that tuple would widen
# publication handling with it.
DRIFT_READ_FAILURES = (
    *AUDIT_READ_FAILURES,
    AssertionComparisonError,
    RulingRecordReadError,
    CriterionReadError,
)


def parse_audit_evidence(body: str) -> CriterionEvidence:
    """Translate only the synchronous native codec's declared malformed input."""
    try:
        return parse_criterion_evidence(body)
    except ValueError as exc:
        raise AuditClaimReadError(
            f"the native Evidence record is invalid: {exc}"
        ) from exc
