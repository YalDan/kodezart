"""Linear MCP wire shapes — Pydantic validation at the adapter boundary.

Vendor vocabulary lives here and nowhere else, exactly as
``types/domain/github.py`` holds the forge's.  Nothing in this module may
be imported by a consumer: the tracker port speaks
``types/domain/tracker.py`` only.

``extra="ignore"``: the vendor adds fields to its own payloads and that is
not this process's business.  Every field the adapter reads is declared
here, so an absent or mistyped one is a validation failure, never a
substituted default.  Vendor camelCase arrives through aliases so the
Python surface stays snake_case.

Every shape here is MEASURED against the live server, not reasoned from
the vendor's documentation (KOD-143).  It has to be: no tool on that
server declares an ``outputSchema``, so a payload's shape is knowable
only by probing it, and the first version of this module — authored
blind — got five of them structurally wrong.  A shape changed here
without a fresh capture behind it is a guess wearing a type.
"""

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.alias_generators import to_camel


class LinearWireModel(BaseModel):
    """Base for Linear MCP payload shapes."""

    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        alias_generator=to_camel,
        populate_by_name=True,
    )


class LinearPriorityWire(LinearWireModel):
    """Linear's priority object. ``value`` is the raw numeric field.

    The numeric encoding is NOT an order: ``0`` means "no priority" and
    ranks last.  Mapping it is ``LinearMcpTracker``'s job; nothing outside
    the adapter ever sees this number.
    """

    value: int


class LinearRelatedIssueWire(LinearWireModel):
    """One issue on the far end of a relation edge.

    ``id`` is the human identifier (``KOD-56``), the same spelling every
    other payload addresses an issue by, so a relation reads back through
    the same door it points at.
    """

    id: str


class LinearIssueRelationsWire(LinearWireModel):
    """The relation edges of one issue, as ``get_issue`` reports them.

    NOT a list of typed edges: the vendor answers with ONE object whose
    keys are the relation kinds, each carrying the issues on that edge.
    Three arms are many-valued and arrive as arrays; ``duplicateOf`` is
    single-valued and arrives as ``null`` when there is none.  All four
    keys are present on every measured payload, so all four are required
    — an absent one is a vendor change this adapter must be told about.

    Only a read that asks (``includeRelations``) produces this object at
    all; a ``list_issues`` entry carries no relations key whatsoever.
    """

    blocks: list[LinearRelatedIssueWire]
    blocked_by: list[LinearRelatedIssueWire]
    related_to: list[LinearRelatedIssueWire]
    duplicate_of: LinearRelatedIssueWire | None

    def arms(self) -> Sequence[tuple[str, Sequence[LinearRelatedIssueWire]]]:
        """Each arm by its VENDOR key, with the issues standing on it.

        ``duplicateOf`` is reported as the zero- or one-element sequence
        it means, so a reader walks one shape rather than two.  The keys
        stay in the vendor's spelling because mapping them to a domain
        vocabulary is the adapter's job and no consumer's.
        """
        return (
            ("blocks", self.blocks),
            ("blockedBy", self.blocked_by),
            ("relatedTo", self.related_to),
            (
                "duplicateOf",
                () if self.duplicate_of is None else (self.duplicate_of,),
            ),
        )


class LinearAssetWire(LinearWireModel):
    """One attachment or document reference on an issue.

    ``content_type`` and ``size`` are absent from every measured payload;
    they stay declared and optional because the port's asset carries them
    and ``None`` says "the tracker did not report one", which is not the
    same fact as any particular value.
    """

    id: str
    title: str
    url: str
    content_type: str | None = None
    size: int | None = None


class LinearIssueWire(LinearWireModel):
    """A Linear issue, in the fields EVERY issue-bearing payload carries.

    Measured against ``list_issues`` entries and ``get_issue`` alike.  The
    collections a list entry never carries live on
    :class:`LinearIssueDetailWire` instead, so nothing here answers "what
    is attached to this issue" from a payload that was never asked.
    """

    id: str
    title: str
    description: str | None = None
    priority: LinearPriorityWire
    status: str
    status_type: str
    #: The team the issue lives on, by display name — the same spelling the
    #: ``team`` scan argument takes, so one configured value both narrows a
    #: query and identifies what came back.  Required: the vendor reports it
    #: on every issue in every payload the adapter reads, and a default here
    #: would answer "which board is this from" with a guess.
    team: str
    labels: list[str] = Field(default_factory=list)
    #: ``None`` means the payload did not REPORT relations — which is what
    #: every ``list_issues`` entry does, and what a ``get_issue`` read that
    #: did not ask for them does.  It is not the claim that the issue has
    #: none; those two facts are different and only one of them is a
    #: statement about the issue.
    relations: LinearIssueRelationsWire | None = None
    parent_id: str | None = None
    assignee: str | None = None
    created_at: datetime
    updated_at: datetime
    url: str


class LinearIssueDetailWire(LinearIssueWire):
    """The ``get_issue`` payload — the whole issue, not a list entry.

    The asset arrays arrive on this read and on no listing, measured, and
    arrive present-and-empty for an issue carrying nothing.  Required
    rather than defaulted for exactly that reason: "the vendor stopped
    sending the key" and "this issue has nothing attached" are different
    facts, and only the second one is an empty list.
    """

    attachments: list[LinearAssetWire]
    documents: list[LinearAssetWire]


class LinearIssueListWire(LinearWireModel):
    """The ``list_issues`` envelope."""

    issues: list[LinearIssueWire]


class LinearCommentAuthorWire(LinearWireModel):
    """Who wrote a comment. The one authorship the vendor surface attests."""

    id: str
    name: str


class LinearCommentWire(LinearWireModel):
    """A Linear comment as the MCP server reports it.

    There is no ``user`` field and no ``issueId`` field — measured.  The
    author arrives as an object, and which issue a comment belongs to is
    known by the caller that asked for it, never read back off the entry.
    """

    id: str
    author: LinearCommentAuthorWire
    body: str
    created_at: datetime


class LinearCommentListWire(LinearWireModel):
    """The ``list_comments`` envelope."""

    comments: list[LinearCommentWire]


class LinearNamedWire(LinearWireModel):
    """A named workspace entity — a user, team, label or status.

    ``team_id`` is the container the entity is defined in, when the vendor
    reports one — and NOTHING resolves an entity's container from it.  No
    measured listing carries the field at all, so a reader of it would see
    every team-scoped label as workspace-level; the adapter takes the
    container from WHICH listing answered instead, that being the only
    statement about scope these payloads actually make (KOD-143, the label
    addendum of 2026-08-25).  The declaration stays because the field is
    the vendor's own and optional, as the asset wire's unmeasured fields
    are.
    """

    name: str
    team_id: str | None = None


class LinearLabelListWire(LinearWireModel):
    """The ``list_issue_labels`` envelope — the array is keyed ``labels``.

    Each list tool names its array after ITSELF; there is no shared
    envelope key across them, so there is one model per tool here and no
    invented common one.
    """

    labels: list[LinearNamedWire]


class LinearTeamWire(LinearNamedWire):
    """One team, carrying the UUID some tool arguments insist on.

    Almost every argument this adapter sends a team to takes "name or ID",
    which is why the configuration names teams the way a person does.
    ``create_issue_label.teamId`` is the exception: its declared input
    schema says "Team UUID", and the live server answers a name with
    ``teamId must be a UUID`` and a 400.  So the id is declared here — on
    teams only, because teams are the only listing this adapter reads one
    from.
    """

    id: str


class LinearTeamListWire(LinearWireModel):
    """The ``list_teams`` envelope — the array is keyed ``teams``."""

    teams: list[LinearTeamWire]


class LinearUserWire(LinearNamedWire):
    """One user, under BOTH identities the workspace answers to.

    ``display_name`` is the handle a mention addresses, and it is not the
    account name: every measured entry carries both and no measured entry
    has them equal.  It is declared because it is now READ — a configured
    identity may legitimately be either spelling, so user resolution
    matches the union of the two (KOD-143 addendum 3).  Declaring it
    before anything read it would have been the module's own rule broken.
    """

    display_name: str


class LinearUserListWire(LinearWireModel):
    """The ``list_users`` envelope — the array is keyed ``users``."""

    users: list[LinearUserWire]


#: ``list_issue_statuses`` answers with a BARE ARRAY of ``{id, type, name}``
#: — no envelope, no key, nothing to unwrap.  A different shape CLASS from
#: every other listing, which is why it is a type adapter here and not a
#: model: there is no object to give a field to.
LINEAR_NAMED_ARRAY: TypeAdapter[list[LinearNamedWire]] = TypeAdapter(
    list[LinearNamedWire],
)


class LinearDocumentWire(LinearWireModel):
    """The ``get_document`` payload — measured, and far wider than this.

    The capture carries ``id``, ``title``, ``content``, ``icon``,
    ``color``, ``url``, ``slugId``, ``createdAt``, ``updatedAt``,
    ``archivedAt``, ``creator`` and ``updatedBy`` as ``{id, name}``
    objects, and four nullable owners — ``project`` as ``{id, name}`` when
    the document has one, ``initiative``, ``team`` and ``issue`` as
    ``null`` beside it.

    One field is declared, because the adapter reads one field: the read
    path answers with the document's text and nothing else.  Declaring
    the rest would state a requirement no caller has, so a vendor that
    stopped sending ``slugId`` would fail a read that never wanted it.
    The measured shape is recorded above so the next reader learns it
    from here rather than by probing again; the day something reads one
    of those fields, it is declared then, with the capture behind it.
    """

    content: str


class LinearDocumentSummaryWire(LinearWireModel):
    """One entry of ``list_documents``, and what ``save_document`` returns.

    Separate from :class:`LinearNamedWire` because a document is addressed
    by a server-assigned ``id`` and named by a ``title``, while a label is
    addressed by the name itself.  Folding the two would make the ensure
    path read one field and mean the other.
    """

    id: str
    title: str


class LinearDocumentListWire(LinearWireModel):
    """The ``list_documents`` envelope."""

    documents: list[LinearDocumentSummaryWire]
