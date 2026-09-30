"""Vendor-neutral addresses for independently leased tracker write surfaces."""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from kodezart.types.domain.scope_address import ScopeKind, ScopeRef


class SurfaceKind(StrEnum):
    """The complete writable-surface vocabulary.

    A criterion sub-issue is one surface covering its body, state and labels.
    Its parentage determines which fire declares it in its surface set; the
    address remains the criterion's own tracker-minted issue key.

    Creating a criterion has no child key yet. The parent's criterion-child
    set is the separate address for that membership-creation operation; it
    does not authorize modifying any existing child's body, state or labels.

    Issue graph addresses cover parentage, dependency/related edges, priority
    and milestone assignment. Inverse changes also require affected peers'
    graph addresses. A split set addresses new membership and initial child
    contents under its source issue, never edits to an existing child.
    """

    ISSUE_DESCRIPTION = "issue_description"
    MARKER_COMMENT = "marker_comment"
    CONTAINER_DESCRIPTION = "container_description"
    CONTAINER_STATUS_UPDATE = "container_status_update"
    ISSUE_LABEL_SET = "issue_label_set"
    CRITERION_SUB_ISSUE = "criterion_sub_issue"
    CRITERION_CHILD_SET = "criterion_child_set"
    ISSUE_GRAPH = "issue_graph"
    ISSUE_SPLIT_SET = "issue_split_set"


class SurfaceAuthorship(StrEnum):
    """Who the tracker itself records as the author of a surface's body.

    The partition a write seam needs and the whole of it: either the
    backend attributes the text standing there to the writing account, or
    it does not.  ``PRINCIPAL_AUTHORED`` is therefore everything else —
    another member's words, and equally a body the backend attributes to
    nobody at all, because an unattributed surface is not one this writer
    can show it wrote.

    Read from the tracker's own attribution and never from the text: a
    body that merely looks machine-written is not a record of who wrote
    it, and a seam deciding otherwise would be guessing about a
    principal's words.
    """

    PRINCIPAL_AUTHORED = "principal_authored"
    MACHINE_AUTHORED = "machine_authored"


_CONTAINER_KINDS: frozenset[SurfaceKind] = frozenset(
    {SurfaceKind.CONTAINER_DESCRIPTION, SurfaceKind.CONTAINER_STATUS_UPDATE},
)

#: The surfaces whose body this port can both read attribution for and
#: replace.  Authorship is asked of exactly those: a kind the port cannot
#: replace has no write seam to refuse at, and answering for one would
#: state a capability no caller can use.
BODY_AUTHORSHIP_SURFACES: frozenset[SurfaceKind] = frozenset(
    {SurfaceKind.ISSUE_DESCRIPTION, SurfaceKind.CRITERION_SUB_ISSUE},
)


@dataclass(frozen=True, slots=True, kw_only=True)
class SurfaceProvenance:
    """The tracker's own attribution of a body, and who has written it.

    ``holders`` is the DISTINCT set of holders whose body writes this port
    recorded, in the order the backend placed those records, so a holder
    that wrote twice is named once and the pair reads the same way round
    for every reader of the same log.  An empty tuple is a body no holder
    recorded a write of, which is not the same as a body nobody wrote:
    ``authorship`` is the only answer about the text standing there.
    """

    authorship: SurfaceAuthorship
    holders: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(not holder.strip() for holder in self.holders):
            raise ValueError("a surface write holder is nonblank")
        if len(set(self.holders)) != len(self.holders):
            raise ValueError("surface write holders are distinct in write order")


def ordered_holders(written_by: Iterable[str]) -> tuple[str, ...]:
    """Body-write holders folded to the distinct set, first occurrence kept.

    Stated once beside the answer it is the shape of, so every
    implementation folds a repeated holder the same way instead of each
    deciding whether the second write is a second holder.
    """
    return tuple(dict.fromkeys(written_by))


@dataclass(frozen=True, slots=True, kw_only=True)
class WritableSurface:
    """One immutable address, suitable for membership in a declared set.

    ``ref`` identifies the issue or container using the same neutral address
    as scope resolution. ``marker`` is part of the address only for a comment:
    two markers on an issue and its description are independent surfaces.
    Marker contents are opaque; adapters own their storage encoding.
    """

    kind: SurfaceKind
    ref: ScopeRef
    marker: str | None = None

    def __post_init__(self) -> None:
        if not self.ref.key.strip():
            raise ValueError("a writable surface requires a nonblank scope key")
        if self.kind in _CONTAINER_KINDS:
            if self.ref.kind is ScopeKind.ISSUE:
                raise ValueError("a container surface requires a container reference")
        elif self.ref.kind is not ScopeKind.ISSUE:
            raise ValueError("an issue surface requires an issue reference")
        if self.kind is SurfaceKind.MARKER_COMMENT:
            if self.marker is None or not self.marker.strip():
                raise ValueError("a marker-keyed comment requires a nonblank marker")
        elif self.marker is not None:
            raise ValueError("only a marker-keyed comment may carry a marker")


def require_body_authorship_surface(surface: WritableSurface) -> None:
    """Refuse an authorship question no implementation can answer.

    Stated once, beside the set itself, so every implementation refuses
    the same addresses rather than each drawing its own boundary.
    """
    if surface.kind not in BODY_AUTHORSHIP_SURFACES:
        raise ValueError(
            f"authorship is recorded for an issue body, not {surface.kind.value}"
        )


type WriteRevalidation = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True, kw_only=True)
class DescriptionWriteAuthority:
    """An actual surface grant, with any caller-owned source authorization.

    A caller writing from a judged source supplies its fresh revalidation;
    the adapter still enforces identity and the surface grant itself.
    """

    holder: str
    surface: WritableSurface
    revalidate: WriteRevalidation | None = None

    def __post_init__(self) -> None:
        if not self.holder.strip():
            raise ValueError("description authority requires a nonblank holder")
        if self.surface.kind not in {
            SurfaceKind.ISSUE_DESCRIPTION,
            SurfaceKind.CRITERION_SUB_ISSUE,
        }:
            raise ValueError(
                "description authority requires an issue or criterion description"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class SurfaceLease:
    """One holder's exclusive grant over a whole declared surface set.

    The set is the unit: a lease covers every surface it names and expires
    for all of them at once, so a holder never owns half of what it asked
    for. ``holder`` is the writing run's identity, opaque to this value.
    """

    holder: str
    surfaces: frozenset[WritableSurface]
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.holder.strip():
            raise ValueError("a surface lease requires a nonblank holder")
        if not self.surfaces:
            raise ValueError("a surface lease requires at least one surface")
