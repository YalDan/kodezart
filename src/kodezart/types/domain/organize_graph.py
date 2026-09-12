"""Explicit Organize graph operations and the native facts they address."""

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.scope import ScopeContainer
from kodezart.types.domain.scope_address import ScopeRef
from kodezart.types.domain.tracker import (
    IssuePriority,
    IssueRelation,
    TrackerComment,
    TrackerIssue,
    WorkflowStateKind,
)

NativeKey = Annotated[str, Field(min_length=1, pattern=r"\S")]


class ParentChange(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["parent"] = Field(
        description="Set or explicitly clear the current issue's parent."
    )
    parent_id: NativeKey | None = Field(
        description="Exact current native parent key, or null to remove parentage."
    )


class _RelationChange(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    add: tuple[NativeKey, ...] = Field(
        default=(),
        description=(
            "Current native issue keys whose relation must be "
            "added; other edges are preserved."
        ),
    )
    remove: tuple[NativeKey, ...] = Field(
        default=(),
        description=(
            "Current native issue keys whose relation must be "
            "removed; never an implicit replacement set."
        ),
    )

    @model_validator(mode="after")
    def _one_unambiguous_delta(self) -> Self:
        if not self.add and not self.remove:
            raise ValueError(
                "a relation change requires an explicit addition or removal"
            )
        if len(set(self.add)) != len(self.add) or len(set(self.remove)) != len(
            self.remove
        ):
            raise ValueError("a relation change cannot repeat native keys")
        if set(self.add) & set(self.remove):
            raise ValueError("a relation cannot be both added and removed")
        return self


class BlockedByChange(_RelationChange):
    kind: Literal["blocked_by"] = Field(
        description=(
            "Add or remove prerequisite edges, without altering unrelated dependencies."
        )
    )


class RelatedToChange(_RelationChange):
    kind: Literal["related_to"] = Field(
        description=(
            "Add or remove related issue edges; these do not imply dependencies."
        )
    )


class PriorityChange(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["priority"] = Field(
        description=(
            "Set the issue's native priority according to the configured mandate."
        )
    )
    priority: IssuePriority = Field(
        description=(
            "Exact desired domain priority; the tracker adapter maps its backend value."
        )
    )


class MilestoneChange(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["milestone"] = Field(
        description="Assign an existing milestone within the issue's current project."
    )
    milestone_id: NativeKey | None = Field(
        description=(
            "Exact native milestone key, or explicit null "
            "requesting removal; unsupported backend removal "
            "refuses."
        )
    )


GraphChange = Annotated[
    ParentChange | BlockedByChange | RelatedToChange | PriorityChange | MilestoneChange,
    Field(discriminator="kind"),
]


class GraphProposal(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["graph"] = Field(
        description="Propose only explicit graph fields on the addressed issue."
    )
    issue_id: NativeKey = Field(
        description="Exact native tracker key supplied as the authoring subject."
    )
    changes: tuple[GraphChange, ...] = Field(
        min_length=1,
        description=(
            "Explicit field changes, at most one per field; "
            "unrequested graph facts remain unchanged."
        ),
    )

    @model_validator(mode="after")
    def _one_change_per_field(self) -> Self:
        if len({change.kind for change in self.changes}) != len(self.changes):
            raise ValueError("a graph proposal cannot repeat a field")
        return self


class SplitChildProposal(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    deliverable_key: NativeKey = Field(
        description=(
            "Stable logical deliverable identity within this source "
            "issue; reuse its recorded identity on replay."
        )
    )
    title: NativeKey = Field(description="Title of the new ordinary deliverable child.")
    body: NativeKey = Field(
        description=(
            "Complete evidence-grounded specification of the new "
            "child; this does not execute work or mark criteria "
            "completed."
        )
    )


class SplitProposal(CamelCaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["split"] = Field(
        description=(
            "Prepare missing ordinary deliverable children under "
            "the addressed source issue."
        )
    )
    issue_id: NativeKey = Field(
        description=(
            "Exact current native source issue key; it remains the "
            "parent of the split children."
        )
    )
    children: tuple[SplitChildProposal, ...] = Field(
        min_length=1,
        description=(
            "Missing deliverables to create by stable identity; "
            "existing matching children are never overwritten."
        ),
    )

    @model_validator(mode="after")
    def _distinct_deliverables(self) -> Self:
        if len({child.deliverable_key for child in self.children}) != len(
            self.children
        ):
            raise ValueError("a split proposal cannot repeat a deliverable identity")
        return self


class IssueGraphSnapshot(CamelCaseModel):
    """Actual graph facts used for a checked write, never a backend CAS token."""

    model_config = ConfigDict(frozen=True)
    issue_key: NativeKey
    body_digest: NativeKey
    title: str
    state_kind: WorkflowStateKind
    issue_labels: tuple[str, ...]
    parent_key: NativeKey | None
    priority: IssuePriority
    milestone_key: NativeKey | None
    project_id: NativeKey | None
    relations: tuple[IssueRelation, ...]


class OrganizeContext(CamelCaseModel):
    """Full current scope and closure evidence, kept typed until prompt rendering."""

    model_config = ConfigDict(frozen=True)
    scope: ScopeRef
    member_keys: tuple[NativeKey, ...]
    issues: tuple[TrackerIssue, ...]
    ruling_comments: tuple[TrackerComment, ...]
    milestones: tuple[ScopeContainer, ...]
