"""Type-check real consumer construction with implementations of only their reads."""

from collections.abc import Sequence

from kodezart.chains.audit_forge import AuditForgeVerifier
from kodezart.config.app import AppConfig
from kodezart.core.protocols import (
    GitService,
    GitSourceReader,
    OutboundContentGate,
    RepoCache,
)
from kodezart.domain.errors import CriterionResolutionError
from kodezart.services.assertion_drift import AssertionDriftDetector
from kodezart.services.audit_sources import AuditSourceReader
from kodezart.services.criterion_sources import NativeCriterionResolver
from kodezart.services.escalation_records import EscalationRecordReader
from kodezart.services.fire_context import FireContextAssembler
from kodezart.services.lane_records import LaneRecordReader
from kodezart.services.recorded_assertion_drift import RecordedAssertionDriftDetector
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.tracker import TrackerAsset, TrackerComment, TrackerIssue


class CommentInput:
    async def list_comments(self, *, issue_key: str) -> Sequence[TrackerComment]:
        return ()


class CriterionInput:
    async def read_criteria(self, *, issue_key: str) -> Sequence[TrackerIssue]:
        return ()


class ResolverInput:
    async def resolve_criterion(
        self, *, issue_key: str, criterion_key: str
    ) -> TrackerIssue:
        raise CriterionResolutionError(
            issue_key=issue_key, criterion_key=criterion_key, reason="no family here"
        )


class DocumentInput:
    async def list_issue_assets(self, *, issue_key: str) -> Sequence[TrackerAsset]:
        return ()

    async def read_document(self, *, document_key: str) -> str:
        return ""


async def compose(
    *,
    operation: OperationConfig,
    config: AppConfig,
    git: GitService,
    source: GitSourceReader,
    cache: RepoCache,
    gate: OutboundContentGate,
    detector: AssertionDriftDetector,
) -> None:
    comments = CommentInput()
    criteria = CriterionInput()
    records = LaneRecordReader(tracker=comments, operation=operation)
    EscalationRecordReader(tracker=comments, operation=operation)
    rulings = RulingRecordReader(tracker=comments, operation=operation)
    sources = AuditSourceReader(
        resolver=ResolverInput(),
        records=records,
        git=git,
        source=source,
        cache=cache,
        operation=operation,
        remote="configured-remote",
    )
    RecordedAssertionDriftDetector(
        tracker=criteria, sources=sources, rulings=rulings, detector=detector
    )
    AuditForgeVerifier(
        resolver=ResolverInput(), ci=None, operation=operation, config=config
    )
    FireContextAssembler(
        tracker=DocumentInput(),
        gate=gate,
        max_count=1,
        max_bytes=64,
        fetch_timeout_seconds=1,
    )
    await NativeCriterionResolver(tracker=criteria).resolve_criterion(
        issue_key="owner", criterion_key="child"
    )
