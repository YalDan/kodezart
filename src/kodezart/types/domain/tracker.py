"""Vendor-neutral tracker vocabulary.

Every type here is domain language.  No vendor concept — no cycles, no
initiatives, no vendor identifier format — appears in this module, and
none may appear in ``TrackerPort``'s signatures.  Adapters own the
translation between these types and whatever their backend calls the
same thing.

Issues are addressed by ``issue_key``: the stable, human-readable
identifier the backend already exposes (``KOD-57`` on Linear, ``#412``
on GitHub Issues, ``PROJ-8`` on Jira).  The adapter maps a key onto its
backend's internal identifier; consumers never see one.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.operation import OperationConfig, QueueState


class TrackerBackend(StrEnum):
    """The tracker adapters this build can select between."""

    LINEAR = "linear"


class IssuePriority(StrEnum):
    """Priority as an ordered domain enum.

    Deliberately a ``StrEnum`` and not an ``IntEnum``: no member carries a
    number, so no consumer can sort priorities by accident.  The total
    order lives in ``PRIORITY_RANK_ORDER`` and is read through
    ``priority_rank``.  Each adapter owns the mapping from its backend's
    own encoding into these members.
    """

    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


PRIORITY_RANK_ORDER: tuple[IssuePriority, ...] = (
    IssuePriority.URGENT,
    IssuePriority.HIGH,
    IssuePriority.MEDIUM,
    IssuePriority.LOW,
    IssuePriority.NONE,
)


def priority_rank(priority: IssuePriority) -> int:
    """Position of *priority* in the domain order; the lower rank wins."""
    return PRIORITY_RANK_ORDER.index(priority)


class WorkflowStateKind(StrEnum):
    """The kind partition every tracker's workflow states collapse onto.

    ``state_name`` on an issue carries the backend's own state name; this
    is the part consumers may branch on.  An issue is OPEN iff its kind is
    neither ``COMPLETED`` nor ``CANCELED`` — read through ``is_open``.
    """

    TRIAGE = "triage"
    BACKLOG = "backlog"
    UNSTARTED = "unstarted"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELED = "canceled"


_CLOSED_STATE_KINDS: frozenset[WorkflowStateKind] = frozenset(
    {WorkflowStateKind.COMPLETED, WorkflowStateKind.CANCELED},
)


def is_open(kind: WorkflowStateKind) -> bool:
    """True iff *kind* is neither completed nor canceled."""
    return kind not in _CLOSED_STATE_KINDS


class IssueRelationKind(StrEnum):
    """The relation kinds the port carries."""

    BLOCKED_BY = "blocked_by"
    BLOCKS = "blocks"
    PARENT = "parent"
    CHILD = "child"
    RELATED = "related"
    DUPLICATE = "duplicate"


class MappingKind(StrEnum):
    """The configured-mapping categories boot validation resolves or instates."""

    USER = "user"
    TEAM = "team"
    QUEUE_STATE = "queue_state"
    WORKFLOW_STATE = "workflow_state"
    DOCUMENT = "document"


#: The mapping kinds an ensure can instate, in ANY backend.  A kind absent
#: here belongs to a field the model does not class OWNED, so no ref builder
#: produces it and no adapter may create it — instatability is a property of
#: the domain's ownership partition, not of a vendor's capabilities.  Ensuring
#: a ref outside this set is ``TrackerEnsureConflictError`` everywhere, which
#: is what keeps an adapter and a test double from disagreeing about it.
INSTATABLE_MAPPING_KINDS: frozenset[MappingKind] = frozenset(
    {MappingKind.QUEUE_STATE, MappingKind.DOCUMENT},
)


class EnsureAction(StrEnum):
    """What ensuring one OWNED mapping did to the workspace.

    Two members, both observable, so a first boot against a fresh
    workspace is distinguishable from a boot against an established one.
    An ensure that would ALTER an existing definition is neither member —
    it is a typed error that performs no write.
    """

    ADOPTED = "adopted"
    CREATED = "created"


class ClaimStatus(StrEnum):
    """Outcome partition of one atomic claim attempt.

    ``LOST`` is a value, never an exception: losing a race is an ordinary
    result the caller routes on.
    """

    GRANTED = "granted"
    LOST = "lost"


class TrackerModel(CamelCaseModel):
    """Base for tracker domain models: frozen, closed."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class IssueRelation(TrackerModel):
    """One edge from an issue to another issue."""

    kind: IssueRelationKind
    issue_key: str = Field(min_length=1)


class TrackerIssue(TrackerModel):
    """A tracker issue in domain vocabulary.

    ``queue_states`` holds only semantic members: whatever the backend
    marks an issue with that the configured mapping does NOT name is not a
    queue state and never reaches a consumer.

    ``team_key`` is the same discipline applied to the issue's container:
    it carries the configured key of the team the issue belongs to, and
    ``None`` when the issue belongs to a team the configuration does not
    declare.  The two states are the whole partition an eligibility clause
    needs, and neither is the absence of an answer — every backend puts an
    issue somewhere, and the adapter is what decides whether that somewhere
    is one of the operation's own.
    """

    issue_key: str = Field(min_length=1)
    title: str
    body: str
    priority: IssuePriority
    state_name: str
    state_kind: WorkflowStateKind
    queue_states: frozenset[QueueState]
    team_key: str | None
    relations: tuple[IssueRelation, ...] = ()
    parent_key: str | None = None
    assignee_key: str | None = None
    created_at: datetime
    updated_at: datetime
    url: str


class TrackerComment(TrackerModel):
    """One comment on an issue."""

    comment_key: str = Field(min_length=1)
    issue_key: str = Field(min_length=1)
    author_key: str = Field(min_length=1)
    body: str
    created_at: datetime


class StateTransition(TrackerModel):
    """Who set a state on an issue, and when.

    This is the provenance record every adapter must be able to answer
    with: authority binds to the approver's ACT, so the actor is the
    load-bearing field, not the resulting state.
    """

    issue_key: str = Field(min_length=1)
    queue_state: QueueState
    actor_key: str = Field(min_length=1)
    occurred_at: datetime


class ClaimResult(TrackerModel):
    """The outcome of one atomic claim attempt."""

    issue_key: str = Field(min_length=1)
    status: ClaimStatus
    holder: str = Field(min_length=1)
    expires_at: datetime


class TrackerAsset(TrackerModel):
    """Metadata for one attachment or document referenced by an issue."""

    asset_key: str = Field(min_length=1)
    title: str
    url: str
    content_type: str | None = None
    size_bytes: int | None = None


class MappingRef(TrackerModel):
    """One configured mapping entry, resolved against the workspace at boot.

    ``name`` is the semantic name the configuration addresses (a role's
    user, a team key, a queue-state member, a lifecycle stage, a document);
    ``identifier`` is the backend identifier the configuration binds it to.

    ``identifier`` is ``None`` only where the WORKSPACE assigns the value —
    a document, whose id exists once it does — and there it means "not
    adopted yet": the config can name the thing without being able to name
    its identifier.  An ensure over such a ref reports the identifier it
    adopted or created.  For every other kind the identifier IS the
    declared value, so a ref without one names nothing and every port
    refuses it rather than guessing.
    """

    kind: MappingKind
    name: str = Field(min_length=1)
    identifier: str | None = None
    #: Backend identifier of the container an INSTATED value is created in,
    #: or ``None`` for one that belongs to the workspace rather than to a
    #: container.  Read only on the ensure path: resolution never creates.
    scope: str | None = None

    def describe(self) -> str:
        """Human-readable one-line description used in boot failures."""
        target = (
            "(assigned by the workspace)"
            if self.identifier is None
            else repr(self.identifier)
        )
        return f"{self.kind.value} {self.name!r} -> {target}"


class MappingOutcome(TrackerModel):
    """What ensuring one OWNED mapping did, reported in the startup record.

    ``identifier`` is what the workspace holds for this mapping AFTER the
    ensure, which is the ref's own identifier for every kind that declares
    one and the adopted or created id for the kinds that do not.  Without
    it a server-assigned id is lost at the boundary and the config it
    belongs in can never be made true.
    """

    ref: MappingRef
    action: EnsureAction
    identifier: str = Field(min_length=1)


class MappingReconciliation(TrackerModel):
    """What boot instated, and the operation config that is true afterwards.

    Two fields because the outcomes are the record and the config is the
    thing every later consumer reads.  An adopted document id exists only
    in an outcome until it is written back, and a prompt rendered from the
    config boot started with would name an id the workspace does not hold.
    """

    config: OperationConfig
    outcomes: tuple[MappingOutcome, ...] = ()


class IssueQuery(TrackerModel):
    """A scan over the tracker, expressed in domain terms only."""

    queue_state: QueueState | None = None
    team_key: str | None = None
    updated_since: datetime | None = None
    page_size: int = Field(gt=0)
