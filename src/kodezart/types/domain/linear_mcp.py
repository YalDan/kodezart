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
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
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


class LinearRelationWire(LinearWireModel):
    """One relation edge as Linear reports it."""

    type: str
    identifier: str


class LinearAssetWire(LinearWireModel):
    """One attachment or document reference on an issue."""

    id: str
    title: str
    url: str
    content_type: str | None = None
    size: int | None = None


class LinearIssueWire(LinearWireModel):
    """A Linear issue as the MCP server reports it."""

    id: str
    title: str
    description: str | None = None
    priority: LinearPriorityWire
    status: str
    status_type: str
    labels: list[str] = Field(default_factory=list)
    relations: list[LinearRelationWire] = Field(default_factory=list)
    attachments: list[LinearAssetWire] = Field(default_factory=list)
    documents: list[LinearAssetWire] = Field(default_factory=list)
    parent_id: str | None = None
    assignee: str | None = None
    created_at: datetime
    updated_at: datetime
    url: str


class LinearIssueListWire(LinearWireModel):
    """The ``list_issues`` envelope."""

    issues: list[LinearIssueWire]


class LinearCommentWire(LinearWireModel):
    """A Linear comment as the MCP server reports it."""

    id: str
    issue_id: str
    user: str
    body: str
    created_at: datetime


class LinearCommentListWire(LinearWireModel):
    """The ``list_comments`` envelope."""

    comments: list[LinearCommentWire]


class LinearHistoryEntryWire(LinearWireModel):
    """One issue-history entry: who changed which labels, and when.

    This is the provenance source.  Linear's history records the actor of
    every label change, which is what makes "who set this state"
    answerable at all — the approving act is a label transition performed
    by a person, and authority binds to that act.
    """

    actor: str
    added_labels: list[str] = Field(default_factory=list)
    removed_labels: list[str] = Field(default_factory=list)
    created_at: datetime


class LinearHistoryWire(LinearWireModel):
    """The ``list_issue_history`` envelope."""

    history: list[LinearHistoryEntryWire]


class LinearNamedWire(LinearWireModel):
    """A named workspace entity — a user, team, label or status.

    ``team_id`` is the container the entity is defined in, when the vendor
    reports one.  Absent means the listing did not say, which is NOT the
    same fact as "defined at workspace scope" and is never read as one.
    """

    name: str
    team_id: str | None = None


class LinearNamedListWire(LinearWireModel):
    """Envelope for any list of named workspace entities."""

    entries: list[LinearNamedWire]


class LinearDocumentWire(LinearWireModel):
    """The ``get_document`` payload."""

    id: str
    content: str
