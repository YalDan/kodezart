"""Operation configuration — the org-shaped runtime config, tracker-agnostic.

Nothing deployment-shaped lives here (that is AppConfig) and no secret ever
does: ``extra="forbid"`` makes a stray token key a load-time failure.
Authority binds to a ROLE, never to a name in code or in a template.
"""

from collections.abc import Sequence
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


class CheckStep(OperationModel):
    """One command in a repository's check chain, and what gates it.

    ``depends_on`` names the step whose success this one is conditional on.
    A step naming none is a GATE: its failure is a root cause.  A step whose
    named ancestor failed is a CASCADE and carries no independent
    information.

    The distinction is the whole point of the type.  The passes carry an
    honesty rule — report one root failure plus its cascades, never a list
    of independent-looking reds — and a flat list of command strings cannot
    say which command that rule is about.
    """

    name: str
    command: str
    depends_on: str | None = None


class RepoEntry(OperationModel):
    """A repository the operation acts on plus its verification chain."""

    url: str
    check_commands: list[CheckStep]


class DocumentEntry(OperationModel):
    """A read-side document reference addressed by a stable key."""

    id: str


class Initiative(OperationModel):
    """An initiative the operation is steering toward.

    ``target_date`` is optional because a real initiative frequently has
    none.  A required field forced every config to invent one, and a pass
    rendered from an invented date reports a distance to a commitment the
    tracker does not hold — an assertion about the operation manufactured
    by its own configuration model.
    """

    id: str
    target_date: date | None = None


def _check_chain_failures(steps: Sequence[CheckStep]) -> list[str]:
    """Every structural failure in one repository's check chain.

    A chain that names a step twice, depends on a step that is not in it,
    or closes a cycle cannot be classified into roots and cascades at all,
    so it is rejected at load rather than mis-reported at run time.
    """
    failures: list[str] = []
    seen: set[str] = set()
    for step in steps:
        if step.name in seen:
            failures.append(f"duplicate step name {step.name!r}")
        seen.add(step.name)

    by_name = {step.name: step for step in steps}
    for step in steps:
        if step.depends_on is None:
            continue
        if step.depends_on not in by_name:
            failures.append(
                f"step {step.name!r} depends on unknown step {step.depends_on!r}",
            )
            continue
        walked: set[str] = {step.name}
        cursor: str | None = step.depends_on
        while cursor is not None:
            if cursor in walked:
                failures.append(f"step {step.name!r} closes a dependency cycle")
                break
            walked.add(cursor)
            cursor = by_name[cursor].depends_on
    return failures


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
        for index, repo in enumerate(self.repos):
            failures.extend(
                f"repos[{index}].check_commands: {failure}"
                for failure in _check_chain_failures(repo.check_commands)
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
