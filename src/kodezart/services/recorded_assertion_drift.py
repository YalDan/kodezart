"""Compare explicitly protected assertions from current native ruling records."""

from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import AssertionComparisonError
from kodezart.services.assertion_drift import AssertionDriftDetector
from kodezart.services.audit_sources import AuditSourceReader
from kodezart.services.ruling_records import RulingRecordReader
from kodezart.types.domain.assertion_drift import (
    AssertionDeviationClaim,
    ProtectedTestRef,
)
from kodezart.types.domain.audit import AuditClaimRequest
from kodezart.types.domain.tracker import TrackerIssue


class RecordedAssertionDriftDetector:
    """Native ruling ownership supplies protection; Evidence supplies a baseline.

    A returned deviation belongs to its ruling's native comment. It does not
    claim that the criterion supplying the comparison SHA has been refuted.
    No test name is inferred from Evidence prose, a dispatch base or file layout.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        sources: AuditSourceReader,
        rulings: RulingRecordReader,
        detector: AssertionDriftDetector,
    ) -> None:
        self._tracker = tracker
        self._sources = sources
        self._rulings = rulings
        self._detector = detector

    async def _family(self, issue_key: str) -> tuple[TrackerIssue, ...]:
        criteria = tuple(await self._tracker.read_criteria(issue_key=issue_key))
        keys = [row.issue_key for row in criteria]
        if len(set(keys)) != len(keys) or any(
            row.parent_key != issue_key
            or row.issue_key == issue_key
            or "criterion" not in row.issue_labels
            for row in criteria
        ):
            raise AssertionComparisonError(
                source_ref=issue_key, reason="the native criterion family is invalid"
            )
        return criteria

    async def compare(
        self, request: AuditClaimRequest
    ) -> tuple[AssertionDeviationClaim, ...]:
        snapshot = await self._sources.read(request)
        family = await self._family(request.lane_issue_key)
        if snapshot.criterion not in family:
            raise AssertionComparisonError(
                source_ref=request.criterion_key,
                reason="the baseline criterion differs from its native family",
            )
        owners = (request.lane_issue_key, *(row.issue_key for row in family))
        records = {
            owner: await self._rulings.read_all(
                issue_key=owner, lane_key=request.lane_key
            )
            for owner in owners
        }
        protected: list[ProtectedTestRef] = []
        native_keys: set[str] = set()
        for occurrences in records.values():
            for comment, ruling in occurrences:
                if (
                    not comment.comment_key.strip()
                    or comment.comment_key in native_keys
                ):
                    raise AssertionComparisonError(
                        source_ref=ruling.ruling_id,
                        reason="a ruling has an absent or duplicate native comment key",
                    )
                native_keys.add(comment.comment_key)
                if ruling.protected_tests is None:
                    raise AssertionComparisonError(
                        source_ref=comment.comment_key,
                        reason="the ruling has no recorded protected-test designation",
                    )
                protected.extend(
                    ProtectedTestRef(
                        source_ref=comment.comment_key,
                        path=reference.path,
                        qualified_name=reference.qualified_name,
                    )
                    for reference in ruling.protected_tests
                )
        claims = await self._detector.compare(
            repo_path=snapshot.repository,
            graded_sha=snapshot.evidence.graded_sha,
            head_ref=snapshot.head_sha,
            protected_tests=tuple(protected),
        )
        for owner, original in records.items():
            if (
                await self._rulings.read_all(issue_key=owner, lane_key=request.lane_key)
                != original
            ):
                raise AssertionComparisonError(
                    source_ref=owner,
                    reason="the ruling records changed during comparison",
                )
        if await self._family(request.lane_issue_key) != family:
            raise AssertionComparisonError(
                source_ref=request.lane_issue_key,
                reason="the criterion family changed during comparison",
            )
        await self._sources.require_unchanged(snapshot)
        return claims
