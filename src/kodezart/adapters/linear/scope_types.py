"""Scope payloads measured through the connected Linear MCP tools.

These captures establish wire shape, not the deployment credential's
capabilities. Milestone reads currently omit a URL; that absence remains
explicit here and is refused by the adapter's metadata capability.
"""

from collections.abc import Mapping

from pydantic import Field, model_validator

from kodezart.adapters.linear.wire import (
    LinearAddressedIssueWire,
    LinearWireModel,
)


class LinearScopeIdentityWire(LinearWireModel):
    """A listed object's canonical identifier."""

    id: str = Field(min_length=1)


class LinearScopeContainerWire(LinearScopeIdentityWire):
    """A project or initiative answered at top level: its UUID is its identity.

    Measured 2026-09-24 against the connected Linear MCP: a top-level project
    or initiative answer reports a display identifier (``P-DUC-33``, ``I-4``)
    under ``id`` and the UUID under ``uuid``, while every payload that
    REFERENCES a container — an issue's ``projectId``, an initiative's
    ``projects``, a project's ``initiatives`` — carries the UUID alone.  The
    UUID is the one identity every read agrees on, so it is what ``id`` holds
    here whenever the answer reports it; an answer without ``uuid`` (a nested
    reference, an older server) keeps its ``id`` as it came.
    """

    @model_validator(mode="before")
    @classmethod
    def _uuid_is_the_identity(cls, data: object) -> object:
        if isinstance(data, Mapping):
            uuid = data.get("uuid")
            if isinstance(uuid, str) and uuid:
                return {**data, "id": uuid}
        return data


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
    projects: list[LinearScopeContainerWire]


class LinearScopeMilestonesWire(LinearWireModel):
    """The milestone tool returns its complete set without pagination."""

    milestones: list[LinearScopeNamedWire]


class LinearScopeMetadataWire(LinearScopeNamedWire):
    description: str | None
    url: str | None = None


class LinearScopeProjectWire(LinearScopeContainerWire, LinearScopeMetadataWire):
    initiatives: list[LinearScopeIdentityWire]


class LinearScopeInitiativeWire(LinearScopeContainerWire, LinearScopeMetadataWire):
    parent_initiatives: list[LinearScopeIdentityWire]
    sub_initiatives: list[LinearScopeIdentityWire]


class LinearApprovalIssueWire(LinearAddressedIssueWire):
    """An approval read requires reported labels and issue parentage.

    Measured 2026-09-24 on the first live approval read: a root issue's
    ``get_issue`` answer carries no ``parentId`` key at all, while a
    sub-issue's carries its parent's key.  Absence is therefore "no parent",
    the same reading :class:`LinearIssueWire` gives the listing entry, and
    a required field here refused every root member of a scope.
    """

    labels: list[str]
    parent_id: str | None = None


class LinearApprovalProjectWire(LinearScopeProjectWire):
    labels: list[str]


class LinearApprovalInitiativeWire(LinearScopeInitiativeWire):
    labels: list[str]
