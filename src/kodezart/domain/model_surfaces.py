"""The write surfaces a marked model's membership resolves to."""

from collections.abc import Mapping, Sequence
from typing import Final

from kodezart.types.domain.operation import OperationConfig
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import TrackerIssue

#: The classification a marked model's membership is queried under. Named
#: once, in the layer that owns the model, so the workspace a case builds
#: and the job that resolves a lease name the same model.
MODEL_CLASSIFICATION: Final[str] = "criterion_lifecycle"


def member_surfaces(
    *,
    members: Sequence[TrackerIssue],
    criteria: Mapping[str, Sequence[TrackerIssue]],
) -> frozenset[WritableSurface]:
    """Every write surface a marked model covers: its members and their criteria.

    A member contributes its own description; each of its criterion
    children contributes that child's own sub-issue surface, addressed by
    the child's own key. Nothing is read out of a body here — the members
    and the children are both supplied by reads, which is what keeps a
    model's extent a matter of the board rather than of prose somebody
    wrote on it.
    """
    surfaces = {
        WritableSurface(
            kind=SurfaceKind.ISSUE_DESCRIPTION,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key=member.issue_key),
        )
        for member in members
    }
    surfaces |= {
        WritableSurface(
            kind=SurfaceKind.CRITERION_SUB_ISSUE,
            ref=ScopeRef(kind=ScopeKind.ISSUE, key=child.issue_key),
        )
        for member in members
        for child in criteria.get(member.issue_key, ())
    }
    return frozenset(surfaces)


def model_lease_set(
    *,
    requested: frozenset[WritableSurface],
    model: frozenset[WritableSurface],
) -> frozenset[WritableSurface]:
    """The set a job asking for *requested* actually takes.

    A job whose request touches an issue the model covers takes the whole
    model, because two jobs writing different members of one model are
    writing one thing. A job touching no issue of the model takes exactly
    what it asked for, which is what lets two jobs over unmarked
    independent surfaces both acquire.

    Membership is settled by issue key rather than by surface identity: a
    request for a member's criterion sub-issue and a request for that
    member's description are both requests inside the model, and a
    comparison over whole addresses would call the first one outside it.
    """
    covered = {surface.ref.key for surface in model}
    if any(surface.ref.key in covered for surface in requested):
        return requested | model
    return requested


def model_classification(operation: OperationConfig) -> str | None:
    """The classification this deployment resolves a marked model under.

    ``None`` where the deployment maps no tracker label onto it — which is
    configuration presence and not a capability question: a writing job on
    such a board leases exactly the surfaces it named, byte for byte what
    it leased before.

    A function beside the name itself rather than an expression inside its
    caller, because a test about a shipped operation file needs the answer
    the caller resolves, and a second copy of the expression would be a
    second opinion about which model that is.
    """
    return (
        MODEL_CLASSIFICATION
        if operation.issue_labels.get(MODEL_CLASSIFICATION)
        else None
    )
