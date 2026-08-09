"""Operation configuration — the org-shaped runtime config, tracker-agnostic.

Nothing deployment-shaped lives here (that is AppConfig) and no secret ever
does: ``extra="forbid"`` makes a stray token key a load-time failure.
Authority binds to a ROLE, never to a name in code or in a template.
"""

from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CHECKPOINT_DOCUMENT_KEY = "checkpoint"
RUN_LOG_RECORD_KEY = "run_log"


class DocumentSystem(StrEnum):
    """The system a document or record id belongs to.

    An enum rather than a free string: a free string reintroduces the
    unresolvable-identifier problem one level up, which is the defect this
    vocabulary exists to close.  Two members because two systems exist.
    """

    TRACKER = "tracker"
    KNOWLEDGE = "knowledge"


class ConfigOwnership(StrEnum):
    """Who is authoritative for a declared value, and therefore what boot does.

    Three classes, not two, because the fields do not split in two.  A
    boolean would force ``operation_name`` and ``endpoints`` into one of two
    behaviours neither of which applies to them.
    """

    #: The operation owns it and can create it: ensured at boot, created if
    #: absent, adopted unchanged if present.
    OWNED = "owned"
    #: Another system is authoritative: resolved at boot, and a failure
    #: aborts with a typed error naming the entry.  A principal cannot be
    #: conjured.
    EXTERNAL = "external"
    #: Nothing exists in the workspace to resolve against; structural
    #: validation only.
    LOCAL = "local"


class PrincipalRole(StrEnum):
    """Authority is enforced via roles. Exactly one APPROVER exists.

    Three members because the passes route three things differently and a
    two-valued enum collapses two of them.  ``APPROVER`` holds the approval
    flip; ``PRINCIPAL``'s word creates a reply obligation the queue does
    not otherwise record; ``ASSIGNEE`` is what prepared fires, triage
    filings and decision flags are assigned to.

    Held as a SET rather than singly, because one principal demonstrably
    holds two: the routines assign every prepared fire and every triage
    filing to the same principal who holds the approval act, so a singular
    field forces either a duplicate entry or a lost routing.
    """

    APPROVER = "approver"
    PRINCIPAL = "principal"
    ASSIGNEE = "assignee"


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
    """A tracker user, the role granting their authority, and how they are named.

    ``tracker_user`` is the identifier authority is checked against — a
    provenance record answers "who set this state" with it.  ``handle`` is
    the identifier a mention is RECOGNISED by: the string a person writes
    when addressing this principal in a body or a comment.  The two are
    routinely different, and a model carrying only the first cannot express
    the mention sweep at all — that sweep is text matching, and it has no
    identifier to match on.
    """

    tracker_user: str
    roles: frozenset[PrincipalRole]
    handle: str
    #: The identifier this principal is recognised by on the FORGE, when it
    #: differs from the tracker one.  Two surfaces name one person, and
    #: review-borne mentions are answered on the forge, so recognising one
    #: principal across both needs two identifiers.  ``None`` for a
    #: principal who never appears there.
    forge_handle: str | None = None


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
    """A repository the operation acts on plus its verification chain.

    ``trunk`` is the branch a lane with no blockers is based on.  It lives
    here, on the repository, because a trunk name is a property OF a
    repository: any other home makes one value stand for N repositories
    that may not share a default branch.  It has no default, because the
    only plausible default is the literal the base resolver is required to
    prove it never reads.
    """

    url: str
    trunk: str = Field(min_length=1)
    checks: tuple[CheckStep, ...]


class DocumentEntry(OperationModel):
    """A read-side document reference addressed by a stable key.

    ``system`` is required because an id alone is unresolvable: a rendered
    prompt saying "the marker in <opaque-id>" names no system a session can
    open, and a session given only the rendered prompt cannot recover one.
    """

    system: DocumentSystem
    id: str


class RecordDestination(OperationModel):
    """A WRITE-side destination a pass records a row to.

    Deliberately not a flag on :class:`DocumentEntry`: ``documents`` is a
    read-side registry, and a boolean there would make it silently
    write-capable while still not saying what is written.
    """

    system: DocumentSystem
    id: str
    append_only: bool


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
    records: dict[str, RecordDestination]
    knowledge: dict[str, str]
    endpoints: dict[str, str]
    initiatives: list[Initiative]
    # Prose describing the CLASS of thing this operation treats as private,
    # never a list of instances. Prose generalizes to instances the operator
    # never enumerated, and it lives operator-side, which together is the
    # whole reason this is not a pattern list. ``None`` means the operator
    # has not supplied one; the judgment scanner then refuses to register
    # rather than registering with nothing to judge against.
    private_surface: str | None = None

    @model_validator(mode="after")
    def _check_structure(self) -> Self:
        """Collect EVERY structural failure into one error, never the first."""
        failures: list[str] = []

        approvers = [p for p in self.principals if PrincipalRole.APPROVER in p.roles]
        if len(approvers) != 1:
            failures.append(
                f"exactly one APPROVER principal is required, found {len(approvers)}",
            )
        assignees = [p for p in self.principals if PrincipalRole.ASSIGNEE in p.roles]
        if len(assignees) != 1:
            failures.append(
                f"exactly one ASSIGNEE principal is required, found {len(assignees)}",
            )
        failures.extend(
            f"principals[{index}] must carry the PRINCIPAL role"
            for index, principal in enumerate(self.principals)
            if PrincipalRole.PRINCIPAL not in principal.roles
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

        if RUN_LOG_RECORD_KEY not in self.records:
            failures.append(
                f"records is missing the stable run-log key {RUN_LOG_RECORD_KEY!r}",
            )

        failures.extend(
            f"repos[{index}].checks must not be empty"
            for index, repo in enumerate(self.repos)
            if not repo.checks
        )
        for index, repo in enumerate(self.repos):
            failures.extend(
                f"repos[{index}].checks: {failure}"
                for failure in _check_chain_failures(repo.checks)
            )

        known_users = {p.tracker_user for p in self.principals}
        failures.extend(
            f"agent_identities[{index}] {identity!r} collides with a principal"
            for index, identity in enumerate(self.agent_identities)
            if identity in known_users
        )

        handles = [p.handle for p in self.principals]
        failures.extend(
            f"principals[{index}].handle must not be empty"
            for index, handle in enumerate(handles)
            if not handle.strip()
        )
        failures.extend(
            f"principals[{index}].handle {handle!r} is not unique"
            for index, handle in enumerate(handles)
            if handles.count(handle) > 1
        )
        failures.extend(
            f"principals[{index}].handle {handle!r} collides with an agent identity"
            for index, handle in enumerate(handles)
            if handle in set(self.agent_identities)
        )

        if failures:
            raise ValueError("; ".join(failures))
        return self

    def approver(self) -> Principal:
        """The single principal holding APPROVER authority."""
        return next(p for p in self.principals if PrincipalRole.APPROVER in p.roles)


#: Which class every declared field belongs to, and therefore what boot does
#: with it.  A fixed partition in the MODEL rather than a per-field flag,
#: because a flag makes ownership operator-editable data: an operator could
#: mark ``principals`` ensurable and the adapter would try to create a user.
#:
#: ``documents`` and ``records`` are EXTERNAL rather than OWNED, which
#: deviates from the ruled partition.  OWNED means ensured at boot and
#: created if absent, KEYED BY THE CONFIG'S DECLARED NAME — and neither
#: :class:`DocumentEntry` nor :class:`RecordDestination` carries a name.
#: Their declared value is a server-assigned identifier, so a document
#: absent from the workspace cannot be created *with the id the config
#: names* and "create it if absent" has no implementation that leaves the
#: config true.  Closing it means a declared name per entry with the id
#: ADOPTED rather than declared, which is a change to THIS model, not to an
#: adapter.  The amendment and its ground are recorded on KOD-57; a boot
#: whose OWNED set names a field with no way to instate it fails loudly
#: rather than skipping it, so this cannot go quiet.
#:
#: Totality over ``OperationConfig.model_fields`` is asserted by a test
#: derived from ``model_fields``, never from a hand-written list.
FIELD_OWNERSHIP: dict[str, ConfigOwnership] = {
    "operation_name": ConfigOwnership.LOCAL,
    "workspace": ConfigOwnership.EXTERNAL,
    "principals": ConfigOwnership.EXTERNAL,
    "agent_identities": ConfigOwnership.EXTERNAL,
    "teams": ConfigOwnership.EXTERNAL,
    "queue_states": ConfigOwnership.OWNED,
    "workflow_states": ConfigOwnership.EXTERNAL,
    "repos": ConfigOwnership.LOCAL,
    "documents": ConfigOwnership.EXTERNAL,
    "records": ConfigOwnership.EXTERNAL,
    "knowledge": ConfigOwnership.LOCAL,
    "endpoints": ConfigOwnership.LOCAL,
    "initiatives": ConfigOwnership.EXTERNAL,
    "private_surface": ConfigOwnership.LOCAL,
}
