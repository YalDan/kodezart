"""Refuse a commit that loses a designated assertion, and mark the lane.

The whole memory of this refusal is the criterion it leaves on the lane, so
a killed run that commits the same loss again finds that criterion rather
than minting a second one: the text is rendered from the pinned record's own
identifiers alone, and the mint's own identity — exact parent plus
current Check — answers a replay with the child that already stands.

A refused weakening never moves the lane's head, so the mark is satisfied as
soon as it is minted and is crossed off in the normal course. A later
weakening of the same test renders the same Check and is answered with that
crossed-off child, so the writer moves any child it is answered with that is
not open back to unstarted, under a lease on that child's own surface. An
open child is left as it stands, so a replay of a killed run writes nothing
more.

What the mark is made of is arithmetic over two pinned Git objects and a
pinned record, so nothing here is authored and nothing here is a second
judgement of anything. The obligation lands as a criterion sub-issue on the
lane, which is the one carrier the lane's rollup reads, and publication is
refused while it stands.

A comparison that cannot be made refuses with no mark: an unreadable
designation is not evidence that the assertions survived.
"""

from collections.abc import Callable

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.outbound_write import gated_exact
from kodezart.core.owned_tasks import settle
from kodezart.core.protocols import (
    CriterionMinter,
    GitSourceReader,
    OutboundContentGate,
)
from kodezart.domain.amendment import (
    AssertionWeakenedError,
    NativeWriteRefusalError,
)
from kodezart.domain.assertion_drift import lost_assertions, weakening_mark
from kodezart.domain.criterion_creation import criterion_body
from kodezart.domain.errors import AssertionComparisonError, GitSourceReadError
from kodezart.services.assertion_drift import AssertionDriftDetector
from kodezart.services.run_surface_lease import RunSurfaceLease
from kodezart.types.domain.agent import RulingProtectedTestRef
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
)
from kodezart.types.domain.organize_owner import CriterionProposal
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import TrackerIssue, WorkflowStateKind


class WeakenedAssertionMarks:
    """Refuse a commit that loses a designated assertion, and leave the mark."""

    def __init__(
        self,
        *,
        tracker: CriterionMinter,
        source: GitSourceReader,
        gate: OutboundContentGate,
        lease_seconds: float,
    ) -> None:
        self._tracker = tracker
        self._detector = AssertionDriftDetector(git=source)
        self._gate = gate
        self._lease_seconds = lease_seconds
        self._log: BoundLogger = get_logger(__name__)

    async def refuse_weakening(
        self,
        *,
        repo_path: str,
        lane_key: str,
        start_sha: str,
        commit_sha: str,
        designated: tuple[RulingProtectedTestRef, ...],
        holder: str,
        visibility: RepoVisibility,
    ) -> None:
        """Compare the two commits and refuse the publication a loss would make.

        A roster that designates nothing reads no Git object at all: there
        is no protection to compare and no refusal to consider. Everything
        else is compared first, gated next, and minted last, so a gate that
        would alter the mark's bytes refuses before the lane carries
        anything.
        """
        if not designated:
            return
        try:
            claims = await self._detector.compare(
                repo_path=repo_path,
                graded_sha=start_sha,
                head_ref=commit_sha,
                protected_tests=designated,
            )
        except (AssertionComparisonError, GitSourceReadError) as exc:
            # A designated file the starting head cannot supply is as
            # uncomparable as a definition it cannot resolve: the writer is
            # refused with its own typed refusal either way, and no mark.
            raise NativeWriteRefusalError(
                "The designated tests could not be compared"
            ) from exc
        marks: list[CriterionProposal] = []
        for claim in claims:
            lost = lost_assertions(before=claim.before, after=claim.after)
            if lost:
                marks.append(weakening_mark(claim=claim, lost=lost))
        if not marks:
            return

        def refusal() -> Exception:
            """The gate refused the text itself: nothing was minted."""
            return AssertionWeakenedError(lane_key=lane_key, marks=())

        for mark in marks:
            await self._gate_exact(
                content=criterion_body(
                    parent_key=lane_key, check=mark.check, do=mark.do
                ),
                visibility=visibility,
                destination=OutboundDestination.TRACKER_DESCRIPTION,
                refusal=refusal,
            )
            await self._gate_exact(
                content=mark.title,
                visibility=visibility,
                destination=OutboundDestination.TRACKER_TITLE,
                refusal=refusal,
            )
        surface = WritableSurface(
            kind=SurfaceKind.CRITERION_CHILD_SET,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key=lane_key),
        )
        minted: list[TrackerIssue] = []
        async with RunSurfaceLease(
            tracker=self._tracker,
            job_id=holder,
            surfaces=frozenset({surface}),
            lease_seconds=self._lease_seconds,
        ) as lease:
            for mark in marks:
                await lease.renew()
                # No revalidation callback: every byte of this mark is derived
                # from two pinned Git objects and a pinned record, so there is
                # no held reading of the board for a second look to confirm.
                created = await settle(
                    self._tracker.create_criterion_if_absent(
                        parent_key=lane_key,
                        title=mark.title,
                        check=mark.check,
                        do=mark.do,
                        holder=holder,
                    )
                )
                minted.append(created)
        # The mint returns a matched child in whatever state it is in; one
        # crossed off stands for no obligation, so it is moved back to
        # unstarted under a lease on its own surface.
        closed = [
            child
            for child in minted
            if child.state_kind is not WorkflowStateKind.UNSTARTED
        ]
        if closed:
            async with RunSurfaceLease(
                tracker=self._tracker,
                job_id=holder,
                surfaces=frozenset(
                    WritableSurface(
                        kind=SurfaceKind.CRITERION_SUB_ISSUE,
                        ref=ScopeRef(kind=ScopeKind.ISSUE, key=child.issue_key),
                    )
                    for child in closed
                ),
                lease_seconds=self._lease_seconds,
            ) as reopening:
                for child in closed:
                    await reopening.renew()
                    await settle(
                        self._tracker.reset_criterion_pending(
                            expected=child, holder=holder
                        )
                    )
        keys = [child.issue_key for child in minted]
        await self._log.ainfo("assertion_weakening_marked", lane=lane_key, marks=keys)
        raise AssertionWeakenedError(lane_key=lane_key, marks=tuple(keys))

    async def _gate_exact(
        self,
        *,
        content: str,
        visibility: RepoVisibility,
        destination: OutboundDestination,
        refusal: Callable[[], Exception],
    ) -> None:
        """Every byte of the mark survives the gate, or nothing is minted."""
        await gated_exact(
            gate=self._gate,
            log=self._log,
            content=content,
            visibility=visibility,
            destination=destination,
            content_class=ContentClass.DERIVED,
            aggregates=(),
            refusal=refusal,
        )
