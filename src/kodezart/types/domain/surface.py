"""Vendor-neutral addresses for independently leased tracker write surfaces."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from kodezart.types.domain.scope import ScopeKind, ScopeRef


class SurfaceKind(StrEnum):
    """The complete writable-surface vocabulary.

    A criterion sub-issue is one surface covering its body, state and labels.
    Its parentage determines which fire declares it in its surface set; the
    address remains the criterion's own tracker-minted issue key.

    Creating a criterion has no child key yet. The parent's criterion-child
    set is the separate address for that membership-creation operation; it
    does not authorize modifying any existing child's body, state or labels.
    """

    ISSUE_DESCRIPTION = "issue_description"
    MARKER_COMMENT = "marker_comment"
    CONTAINER_DESCRIPTION = "container_description"
    CONTAINER_STATUS_UPDATE = "container_status_update"
    ISSUE_LABEL_SET = "issue_label_set"
    CRITERION_SUB_ISSUE = "criterion_sub_issue"
    CRITERION_CHILD_SET = "criterion_child_set"


_CONTAINER_KINDS: frozenset[SurfaceKind] = frozenset(
    {SurfaceKind.CONTAINER_DESCRIPTION, SurfaceKind.CONTAINER_STATUS_UPDATE},
)


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
