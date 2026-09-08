"""Drive delta and periodic-full coverage without owning a second clock."""

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta

from kodezart.core.config import AppConfig
from kodezart.types.domain.audit import AuditCandidate, AuditCoverageResult
from kodezart.types.domain.scope import ScopeRef


class AuditCoverage:
    """Disposable per-scope cache; only completed coverage can advance it.

    The scheduler supplies time and the caller supplies the complete eligible
    snapshot. The visitor must finish auditing each entry before returning.
    Failure or cancellation repeats the whole selected window on re-entry.
    Neither tracker collection nor the judgment session is implemented here.
    """

    def __init__(self, *, config: AppConfig) -> None:
        self._full_interval = timedelta(
            seconds=config.audit_full_sweep_interval_seconds
        )
        self._tick_interval = timedelta(seconds=config.audit_sweep_interval_seconds)
        self._covered: dict[ScopeRef, dict[str, datetime]] = {}
        self._last_full: dict[ScopeRef, datetime] = {}
        self._last_tick: dict[ScopeRef, datetime] = {}
        self._active: set[ScopeRef] = set()

    async def cover(
        self,
        *,
        scope: ScopeRef,
        candidates: Sequence[AuditCandidate],
        observed_at: datetime,
        visit: Callable[[AuditCandidate], Awaitable[None]],
    ) -> AuditCoverageResult:
        """Visit the measured selection and commit its cache only on success."""
        if observed_at.utcoffset() is None:
            raise ValueError("audit observation time must carry a timezone")
        if scope in self._active:
            raise ValueError("this scope already has an active audit coverage attempt")
        previous_tick = self._last_tick.get(scope)
        if previous_tick is not None and observed_at < previous_tick:
            raise ValueError("audit observation time moved backwards")
        snapshot = tuple(
            sorted(candidates, key=lambda row: (row.state_changed_at, row.issue_key))
        )
        keys = [row.issue_key for row in snapshot]
        if len(keys) != len(set(keys)):
            raise ValueError("audit candidates contain duplicate issue identities")
        if any(row.state_changed_at > observed_at for row in snapshot):
            raise ValueError("audit candidate state change follows the observation")
        last_full = self._last_full.get(scope)
        full = last_full is None or (
            observed_at - last_full >= self._full_interval
            or observed_at - last_full + self._tick_interval > self._full_interval
        )
        previous = self._covered.get(scope, {})
        selected = (
            snapshot
            if full
            else tuple(
                row
                for row in snapshot
                if previous.get(row.issue_key) != row.state_changed_at
            )
        )
        result = AuditCoverageResult(
            scope=scope, observed_at=observed_at, full=full, covered=selected
        )
        self._active.add(scope)
        try:
            for candidate in selected:
                await visit(candidate)
            if selected:
                retained = {} if full else dict(previous)
                retained.update(
                    (row.issue_key, row.state_changed_at) for row in selected
                )
                self._covered[scope] = retained
                self._last_tick[scope] = observed_at
                if full:
                    self._last_full[scope] = observed_at
            return result
        finally:
            self._active.remove(scope)
