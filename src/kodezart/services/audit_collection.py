"""Collect complete audit identities and their native state-change stamps."""

from dataclasses import dataclass

from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import ScopeReadError
from kodezart.services.scope_membership import read_scope_members
from kodezart.types.domain.audit import AuditCandidate
from kodezart.types.domain.scope import ScopeRef
from kodezart.types.domain.tracker import TrackerIssue


@dataclass(frozen=True)
class AuditCandidateSnapshot:
    """The complete native family retained with its corresponding stamps."""

    scope: ScopeRef
    issues: tuple[TrackerIssue, ...]
    candidates: tuple[AuditCandidate, ...]


async def collect_audit_candidates(
    *, tracker: TrackerPort, scope: ScopeRef
) -> tuple[AuditCandidate, ...]:
    """Return the existing ordered candidate projection of the native snapshot."""
    return (
        await read_audit_candidate_snapshot(tracker=tracker, scope=scope)
    ).candidates


async def read_audit_candidate_snapshot(
    *, tracker: TrackerPort, scope: ScopeRef
) -> AuditCandidateSnapshot:
    """Return coherent issue/criterion stamps or refuse before coverage starts.

    The second membership enumeration checks additions, removals and mutations
    across the detail reads. No update timestamp substitutes for state history.
    """
    before = await read_scope_members(tracker=tracker, scope=scope)
    observed: dict[str, TrackerIssue] = {}
    candidates: list[AuditCandidate] = []
    for key, issue in before.items():
        revision = await tracker.read_issue_state_change(issue_key=key)
        if revision.issue != issue:
            raise ScopeReadError(
                "audit issue changed before state-history read", ref=scope
            )
        observed[key] = revision.issue
        candidates.append(
            AuditCandidate(issue_key=key, state_changed_at=revision.state_changed_at)
        )
    after = await read_scope_members(tracker=tracker, scope=scope)
    if after != observed:
        raise ScopeReadError("audit membership changed during collection", ref=scope)
    return AuditCandidateSnapshot(
        scope=scope,
        issues=tuple(observed[key] for key in sorted(observed)),
        candidates=tuple(
            sorted(candidates, key=lambda row: (row.state_changed_at, row.issue_key))
        ),
    )
