"""Vendor-neutral addresses for scopes the tracker resolves."""

from enum import StrEnum

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.tracker import TrackerIssue


class ScopeKind(StrEnum):
    """The four kinds of scope an operation can address."""

    INITIATIVE = "initiative"
    PROJECT = "project"
    MILESTONE = "milestone"
    ISSUE = "issue"


class ScopeRef(CamelCaseModel):
    """A scope's kind and opaque key; the adapter resolves its address."""

    model_config = ConfigDict(frozen=True)

    kind: ScopeKind
    key: str = Field(min_length=1)


class ScopeContainer(CamelCaseModel):
    """A container's own metadata and optional containing scope."""

    model_config = ConfigDict(frozen=True)

    ref: ScopeRef
    name: str
    description: str
    url: str
    parent: ScopeRef | None = None


class ResolvedScope(CamelCaseModel):
    """A resolved reference and the issues whose own fields describe its graph."""

    model_config = ConfigDict(frozen=True)

    ref: ScopeRef
    issues: tuple[TrackerIssue, ...]


class ScopePlanSnapshot(CamelCaseModel):
    """A coherent scope family and the outside dependency facts it consulted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: ResolvedScope
    dependencies: tuple[TrackerIssue, ...]
