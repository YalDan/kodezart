"""Operation configuration — the org-shaped runtime config, tracker-agnostic.

Nothing deployment-shaped lives here (that is AppConfig) and no secret ever
does: ``extra="forbid"`` makes a stray token key a load-time failure.
Authority binds to a ROLE, never to a name in code or in a template.
"""

from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

CHECKPOINT_DOCUMENT_KEY = "checkpoint"


class PrincipalRole(StrEnum):
    """Authority is enforced via roles. Exactly one APPROVER exists."""

    APPROVER = "approver"
    PRINCIPAL = "principal"


class QueueState(StrEnum):
    """The queue members code addresses BY NAME.

    ``queue_states`` itself is an open mapping: additional members are legal
    as pure configuration entries, addressable from templates, and reshape
    neither this type nor any consumer.
    """

    TRIAGE = "triage"
    PROPOSED = "proposed"
    APPROVED = "approved"
    DONE = "done"
    DECISION = "decision"


class LifecycleStage(StrEnum):
    """Lifecycle stages resolved through the workflow_states mapping."""

    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"


class OperationModel(BaseModel):
    """Base for operation-config models: closed, frozen."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Principal(OperationModel):
    """A tracker user and the role that grants their authority."""

    tracker_user: str
    role: PrincipalRole


class RepoEntry(OperationModel):
    """A repository the operation acts on plus its verification commands."""

    url: str
    check_commands: list[str]


class DocumentEntry(OperationModel):
    """A read-side document reference addressed by a stable key."""

    id: str


class Initiative(OperationModel):
    """An initiative the operation is steering toward."""

    id: str
    target_date: date


class OperationConfig(OperationModel):
    """The whole operation configuration, validated structurally at load."""

    operation_name: str
    workspace: str
    principals: list[Principal]
    agent_identities: list[str]
    teams: dict[str, str]
    queue_states: dict[str, str]
    workflow_states: dict[LifecycleStage, str]
    repos: list[RepoEntry]
    documents: dict[str, DocumentEntry]
    knowledge: dict[str, str]
    endpoints: dict[str, str]
    initiatives: list[Initiative]

    @model_validator(mode="after")
    def _check_structure(self) -> Self:
        """Collect EVERY structural failure into one error, never the first."""
        failures: list[str] = []

        approvers = [p for p in self.principals if p.role is PrincipalRole.APPROVER]
        if len(approvers) != 1:
            failures.append(
                f"exactly one APPROVER principal is required, found {len(approvers)}",
            )

        for member in QueueState:
            if member.value not in self.queue_states:
                failures.append(
                    f"queue_states is missing required key {member.value!r}"
                )

        for stage in LifecycleStage:
            if stage not in self.workflow_states:
                failures.append(
                    f"workflow_states is missing required stage {stage.value!r}",
                )

        if CHECKPOINT_DOCUMENT_KEY not in self.documents:
            failures.append(
                f"documents is missing the stable checkpoint key "
                f"{CHECKPOINT_DOCUMENT_KEY!r}",
            )

        failures.extend(
            f"repos[{index}].check_commands must not be empty"
            for index, repo in enumerate(self.repos)
            if not repo.check_commands
        )

        known_users = {p.tracker_user for p in self.principals}
        failures.extend(
            f"agent_identities[{index}] {identity!r} collides with a principal"
            for index, identity in enumerate(self.agent_identities)
            if identity in known_users
        )

        if failures:
            raise ValueError("; ".join(failures))
        return self

    def approver(self) -> Principal:
        """The single principal holding APPROVER authority."""
        return next(p for p in self.principals if p.role is PrincipalRole.APPROVER)
