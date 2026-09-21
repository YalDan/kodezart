"""Publish native audit artifacts through the canonical bounded verifier."""

from collections.abc import Awaitable, Callable

from kodezart.chains.write_back_verifier import WriteBackVerifier
from kodezart.core.logging import get_logger
from kodezart.core.outbound_write import gated_write
from kodezart.core.protocols import OutboundContentGate, TrackerPort, WriteBackStep
from kodezart.domain.errors import AuditClaimReadError
from kodezart.domain.tracker_writes import marked_comment_body
from kodezart.services.audit_failures import AUDIT_PUBLICATION_FAILURES
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.audit_runtime import AuditRepairInput
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.write_back import WriteBackFinding, WriteBackResult


class _AuditComment:
    def __init__(
        self,
        *,
        tracker: TrackerPort,
        gate: OutboundContentGate,
        surface: WritableSurface,
        job_id: str,
        visibility: RepoVisibility,
        compose: Callable[[WriteBackFinding | None], Awaitable[str]],
        require_current: Callable[[], Awaitable[None]],
        accept_write: Callable[[], Awaitable[None]],
    ) -> None:
        self._tracker, self._gate = tracker, gate
        self._surface, self._job_id = surface, job_id
        self._visibility = visibility
        self._compose, self._require_current = compose, require_current
        self._accept_write = accept_write
        self._log = get_logger(__name__)

    @property
    def surface(self) -> WritableSurface:
        return self._surface

    async def write(self, *, finding: WriteBackFinding | None) -> None:
        body = await self._compose(finding)
        marker = self.surface.marker
        if marker is None:
            raise ValueError("audit publication requires a marker-comment address")
        content = marked_comment_body(marker=marker, body=body)
        permitted = await gated_write(
            gate=self._gate,
            log=self._log,
            content=content,
            visibility=self._visibility,
            shape=WriterShape.PROSE,
            destination=OutboundDestination.TRACKER_COMMENT,
            content_class=ContentClass.AUTHORED,
            aggregates=(),
        )
        # These are already validated observation payloads. A privacy rewrite
        # cannot silently change their cited native identity or source bytes.
        if permitted != content:
            raise AuditClaimReadError(
                "the outbound gate changed the completed audit report payload"
            )
        await self._require_current()
        await self._tracker.upsert_comment(
            target=self.surface.ref.key,
            marker=marker,
            body=body,
            holder=self._job_id,
        )
        await self._accept_write()


class _ObservedRepairStep:
    """Retain only the input already supplied by the existing verifier."""

    def __init__(self, *, step: WriteBackStep, ref: str) -> None:
        self._step, self._ref = step, ref
        self.received: list[AuditRepairInput] = []

    @property
    def surface(self) -> WritableSurface:
        return self._step.surface

    async def write(self, *, finding: WriteBackFinding | None) -> None:
        if finding is not None:
            self.received.append(
                AuditRepairInput(
                    surface=self.surface,
                    ref=self._ref,
                    preceding_round=len(self.received) + 1,
                    finding=finding,
                )
            )
        await self._step.write(finding=finding)


class AuditPublisher:
    """The sole write-back loop owns repair and actual landed-artifact evidence."""

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        gate: OutboundContentGate,
        verifier: WriteBackVerifier,
        lease_seconds: float,
    ) -> None:
        self._tracker, self._gate, self._verifier = tracker, gate, verifier
        self._lease_seconds = lease_seconds

    async def verify_step(
        self,
        *,
        step: WriteBackStep,
        ref: str,
        interrupted: list[AuditRepairInput],
    ) -> WriteBackResult:
        """Retain interrupted repair input; completed rounds stay in the result."""
        observed = _ObservedRepairStep(step=step, ref=ref)
        try:
            return await self._verifier.write_back(step=observed, ref=ref)
        except AUDIT_PUBLICATION_FAILURES:
            interrupted.extend(observed.received)
            raise

    async def write_leased(
        self,
        *,
        step: WriteBackStep,
        ref: str,
        job_id: str,
        interrupted: list[AuditRepairInput],
        accept_grant: Callable[[], Awaitable[None]] | None = None,
    ) -> WriteBackResult:
        """Drive one step under this job's lease on the step's own surface.

        The grant is itself a native comment, so a caller whose freshness
        comparison would see it supplies *accept_grant* to re-pin. A caller
        whose comparison ignores that activity timestamp supplies nothing.
        """
        async with RunSurfaceLease(
            tracker=self._tracker,
            job_id=job_id,
            surfaces=frozenset({step.surface}),
            lease_seconds=self._lease_seconds,
        ):
            if accept_grant is not None:
                # Account only for that known activity timestamp; all source
                # content stays pinned.
                await accept_grant()
            return await self.verify_step(step=step, ref=ref, interrupted=interrupted)

    async def publish(
        self,
        *,
        issue_key: str,
        marker: str,
        ref: str,
        job_id: str,
        visibility: RepoVisibility,
        compose: Callable[[WriteBackFinding | None], Awaitable[str]],
        require_current: Callable[[], Awaitable[None]],
        accept_write: Callable[[], Awaitable[None]],
        interrupted: list[AuditRepairInput],
    ) -> WriteBackResult:
        surface = WritableSurface(
            kind=SurfaceKind.MARKER_COMMENT,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key=issue_key),
            marker=marker,
        )
        await require_current()
        return await self.write_leased(
            step=_AuditComment(
                tracker=self._tracker,
                gate=self._gate,
                surface=surface,
                job_id=job_id,
                visibility=visibility,
                compose=compose,
                require_current=require_current,
                accept_write=accept_write,
            ),
            ref=ref,
            job_id=job_id,
            interrupted=interrupted,
            accept_grant=accept_write,
        )
