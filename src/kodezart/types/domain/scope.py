"""Vendor-neutral addresses for scopes the tracker resolves."""

from pydantic import ConfigDict

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.scope_address import (
    ScopeKind as ScopeKind,
)
from kodezart.types.domain.scope_address import (
    ScopeRef as ScopeRef,
)
from kodezart.types.domain.tracker import TrackerIssue


class ScopeContainer(CamelCaseModel):
    """A container's own metadata and optional containing scope."""

    model_config = ConfigDict(frozen=True)

    ref: ScopeRef
    name: str
    description: str
    url: str | None
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
