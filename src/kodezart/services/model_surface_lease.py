"""A writing job over a marked model's member holds the whole model."""

from types import TracebackType

from kodezart.core.protocols import ModelMemberReader, SurfaceLeaseTracker
from kodezart.domain.model_surfaces import member_surfaces, model_lease_set
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.surface import WritableSurface


class ModelSurfaceLease:
    """A writing job over a marked model's member holds the whole model.

    The model's extent is resolved AT ACQUISITION, from the label query
    and each member's criteria — never from anything written in a body —
    and the resolved set is then handed to the landed lease. Arbitration,
    renewal and release are that lease's and the port's: nothing here
    re-implements them, and a contended acquisition raises the surface
    lease error the port raises, neither caught nor translated.

    Where no classification is configured this resolves nothing, issues no
    read and leases exactly the set it was given, which is what leaves two
    jobs over unmarked independent surfaces both acquiring.
    """

    def __init__(
        self,
        *,
        reader: ModelMemberReader,
        tracker: SurfaceLeaseTracker,
        classification: str | None,
        job_id: str,
        surfaces: frozenset[WritableSurface],
        lease_seconds: float,
    ) -> None:
        if not job_id.strip() or not surfaces:
            raise ValueError("a model lease needs a job id and a nonempty write set")
        if classification is not None and not classification.strip():
            raise ValueError("a model lease's classification is a name or is absent")
        self._reader = reader
        self._tracker = tracker
        self._classification = classification
        self._job_id = job_id
        self._surfaces = surfaces
        self._lease_seconds = lease_seconds
        self._lease: RunSurfaceLease | None = None

    async def __aenter__(self) -> RunSurfaceLease:
        lease = RunSurfaceLease(
            tracker=self._tracker,
            job_id=self._job_id,
            surfaces=await self._resolved(),
            lease_seconds=self._lease_seconds,
        )
        self._lease = lease
        return await lease.__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        lease, self._lease = self._lease, None
        if lease is not None:
            await lease.__aexit__(exc_type, exc, traceback)

    async def _resolved(self) -> frozenset[WritableSurface]:
        """The model's whole surface set, or the request where none covers it."""
        if self._classification is None:
            return self._surfaces
        members = tuple(
            await self._reader.read_labeled_issues(classification=self._classification)
        )
        criteria = {
            member.issue_key: tuple(
                await self._reader.read_criteria(issue_key=member.issue_key)
            )
            for member in members
        }
        return model_lease_set(
            requested=self._surfaces,
            model=member_surfaces(members=members, criteria=criteria),
        )
