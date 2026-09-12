"""One writing job owns one declared set until its operation has settled."""

from types import TracebackType
from typing import Self

from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import TrackerPort
from kodezart.domain.errors import SurfaceLeaseError, SurfaceLeaseLostError
from kodezart.types.domain.surface import WritableSurface


class RunSurfaceLease:
    """Acquire once, renew explicitly, and release after every exit.

    The caller supplies the queue's actual job id and the complete write
    set for this operation. The existing tracker port owns arbitration;
    this component owns the lifetime of its requests. No timer renews the
    lease and a failed renewal never becomes another acquisition.

    Callers settle their writes before exiting the context, so release
    cannot race a detached mutation. Operations for the same job and set
    are serialized by their caller, as marker upserts already require.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        job_id: str,
        surfaces: frozenset[WritableSurface],
        lease_seconds: float,
    ) -> None:
        if not job_id.strip() or not surfaces:
            raise ValueError("a run lease needs a job id and a nonempty write set")
        self._tracker = tracker
        self._job_id = job_id
        self._surfaces = surfaces
        self._lease_seconds = lease_seconds
        self._entered = False
        self._active = False

    async def __aenter__(self) -> Self:
        if self._entered:
            raise ValueError("a run surface lease may be entered only once")
        self._entered = True
        try:
            await settle(
                self._tracker.acquire_surfaces(
                    surfaces=self._surfaces,
                    holder=self._job_id,
                    lease_seconds=self._lease_seconds,
                )
            )
        except SurfaceLeaseError:
            # Refusal already holds nothing by the port contract. Another
            # withdrawal could replace the known refusal with an outage.
            raise
        except BaseException:
            # A cancelled or failed acquisition can have reached the
            # backend. Settle it before withdrawing this job's markers.
            await self._release()
            raise
        self._active = True
        return self

    async def renew(self) -> None:
        """Extend the whole set or permanently stop this operation's writes."""
        if not self._active:
            raise SurfaceLeaseLostError(job_id=self._job_id, surfaces=self._surfaces)
        self._active = False
        renewed = await settle(
            self._tracker.renew_surfaces(
                surfaces=self._surfaces,
                holder=self._job_id,
                lease_seconds=self._lease_seconds,
            )
        )
        if renewed is None:
            # None does not identify a lost surface or its current owner.
            # Preserve that uncertainty rather than claiming an absence.
            raise SurfaceLeaseLostError(job_id=self._job_id, surfaces=self._surfaces)
        self._active = True

    async def _release(self) -> None:
        self._active = False
        await settle(
            self._tracker.release_surfaces(
                surfaces=self._surfaces,
                holder=self._job_id,
            )
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._release()
