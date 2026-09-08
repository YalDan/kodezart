"""Scope payloads measured through the connected Linear MCP tools.

These captures establish wire shape, not the deployment credential's
capabilities. Milestone reads currently omit a URL; that absence remains
explicit here and is refused by the adapter's metadata capability.
"""

from pydantic import Field

from kodezart.types.domain.linear_mcp import LinearIssueDetailWire, LinearWireModel


class LinearScopeIdentityWire(LinearWireModel):
    """A listed object's canonical identifier."""

    id: str = Field(min_length=1)


class LinearScopeNamedWire(LinearScopeIdentityWire):
    """The identity and display name returned by a milestone listing."""

    name: str


class LinearScopePageWire(LinearWireModel):
    """Explicit cursor state on the issue and project listing envelopes."""

    has_next_page: bool
    cursor: str | None = None


class LinearScopeIssueWire(LinearScopeIdentityWire):
    """Membership fields; full issue details are read separately."""

    project_milestone: LinearScopeIdentityWire | None = None


class LinearScopeIssuesWire(LinearScopePageWire):
    issues: list[LinearScopeIssueWire]


class LinearScopeProjectsWire(LinearScopePageWire):
    projects: list[LinearScopeIdentityWire]


class LinearScopeMilestonesWire(LinearWireModel):
    """The milestone tool returns its complete set without pagination."""

    milestones: list[LinearScopeNamedWire]


class LinearScopeMetadataWire(LinearScopeNamedWire):
    description: str | None
    url: str | None = None


class LinearScopeProjectWire(LinearScopeMetadataWire):
    initiatives: list[LinearScopeIdentityWire]


class LinearScopeInitiativeWire(LinearScopeMetadataWire):
    parent_initiatives: list[LinearScopeIdentityWire]
    sub_initiatives: list[LinearScopeIdentityWire]


class LinearApprovalIssueWire(LinearIssueDetailWire):
    """An approval read requires reported labels and issue parentage."""

    labels: list[str]
    parent_id: str | None


class LinearApprovalProjectWire(LinearScopeProjectWire):
    labels: list[str]


class LinearApprovalInitiativeWire(LinearScopeInitiativeWire):
    labels: list[str]
