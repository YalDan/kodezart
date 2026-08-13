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


class OperationMemberAbsentError(Exception):
    """Raised at the point of need when an absent config member is required.

    Absence is legal at load — an empty board boots — so nothing about an
    empty collection fails until a consumer actually needs a member of it.
    The refusal then happens at the call site that needed the member, and
    it carries two facts: ``missing`` names the role or key the config does
    not declare, and ``stops`` names what cannot work without it.  A boot
    error would blame the config for a decision it legitimately made; a
    blank render or an exhausted iterator would report nothing at all.
    """

    def __init__(self, *, missing: str, stops: str) -> None:
        super().__init__(f"operation config declares no {missing}; {stops}")
        self.missing: str = missing
        self.stops: str = stops


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
    #:
    #: NOT an orphan, though it currently reads as one: ``a43e5df`` reverted
    #: the fire-prep binding that was its only consumer, on the ground that
    #: no issue body names a forge identifier, and it has had no production
    #: reader since.  The deployed routine awaiting verbatim restoration
    #: (KOD-60) states the requirement in its own words — the operation's
    #: approver is one principal written one way on the tracker and another
    #: on the forge — and carries both spellings as literals.  Deriving that
    #: sentence from configuration is what this field is for, and the
    #: restoration is its reader.  Deleting it as unreferenced would have to
    #: be undone by the pass that lands the routine.
    forge_handle: str | None = None


class TeamEntry(OperationModel):
    """A tracker team: its display name and its short key.

    Two identifiers because the routines use both, in one sentence: the
    display name is what a human reads and what boot resolves against the
    live workspace; the key is the short identifier the tracker prefixes
    issues with, and a rendered prompt that names the first cannot derive
    the second.  Added under KOD-60 R17 — the verbatim routine texts carry
    the key as its own token, and a mapping of key -> name had no home
    for it.
    """

    name: str = Field(min_length=1)
    key: str = Field(min_length=1)


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

    ``name`` is what an ensure keys on, and it is what makes this field
    OWNED rather than EXTERNAL (KOD-57 R2, amendment 3, and R9).  A
    document's id is assigned by the system that holds it, so a config
    could never declare the id of a document that does not exist yet — the
    reason the whole field was classed EXTERNAL and a first boot needed the
    checkpoint document made by hand.  Naming it instead closes that: boot
    creates the document by name when the workspace has none and ADOPTS the
    id it is given.

    ``id`` is therefore three-state and each state is a different fact.
    Absent means "not adopted yet"; present means "this exact document",
    and boot refuses rather than creating a second one when the workspace
    does not hold it.  A document in the KNOWLEDGE system must declare its
    id at load, because nothing in this process can create one there and an
    unadopted id would render as a placeholder no session can open.
    """

    system: DocumentSystem
    name: str = Field(min_length=1)
    id: str | None = None

    @model_validator(mode="after")
    def _knowledge_documents_declare_an_id(self) -> Self:
        if self.system is DocumentSystem.KNOWLEDGE and self.id is None:
            msg = (
                f"document {self.name!r} lives in the "
                f"{DocumentSystem.KNOWLEDGE.value} system, which this operation "
                f"cannot instate, so its id must be declared"
            )
            raise ValueError(msg)
        return self


class RecordDestination(OperationModel):
    """A WRITE-side destination a pass records a row to.

    Deliberately not a flag on :class:`DocumentEntry`: ``documents`` is a
    read-side registry, and a boolean there would make it silently
    write-capable while still not saying what is written.

    ``name`` is the destination's display title — what a routine addresses
    its log by in prose.  Added under KOD-60 R17: the verbatim routine
    texts carry the title as its own token, and rendering ``id`` where the
    text carries a name is the substitution error the byte-identity gate
    caught.
    """

    system: DocumentSystem
    name: str = Field(min_length=1)
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
    """The whole operation configuration, validated structurally at load.

    Two scalars are required — a config that names no operation and no
    workspace describes nothing.  Every collection defaults empty, because
    each one's absence is a real operation: an empty board boots.  What an
    empty collection costs is paid at the point of need — the consumer that
    requires a member refuses with :class:`OperationMemberAbsentError`
    naming the missing role or key and what stops working — never at load.
    Structural validation applies to what IS present: a populated registry
    missing the member code addresses by name is a typo and fails loudly,
    while an empty one is a decision and loads.
    """

    operation_name: str
    workspace: str
    principals: list[Principal] = Field(default_factory=list)
    agent_identities: list[str] = Field(default_factory=list)
    teams: dict[str, TeamEntry] = Field(default_factory=dict)
    queue_states: dict[str, str] = Field(default_factory=dict)
    workflow_states: dict[LifecycleStage, str] = Field(default_factory=dict)
    repos: list[RepoEntry] = Field(default_factory=list)
    documents: dict[str, DocumentEntry] = Field(default_factory=dict)
    records: dict[str, RecordDestination] = Field(default_factory=dict)
    knowledge: dict[str, str] = Field(default_factory=dict)
    endpoints: dict[str, str] = Field(default_factory=dict)
    initiatives: list[Initiative] = Field(default_factory=list)
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

        if self.principals:
            approvers = [
                p for p in self.principals if PrincipalRole.APPROVER in p.roles
            ]
            if len(approvers) != 1:
                failures.append(
                    f"exactly one APPROVER principal is required, "
                    f"found {len(approvers)}",
                )
            assignees = [
                p for p in self.principals if PrincipalRole.ASSIGNEE in p.roles
            ]
            if len(assignees) > 1:
                failures.append(
                    f"at most one ASSIGNEE principal may be declared, "
                    f"found {len(assignees)}",
                )
        failures.extend(
            f"principals[{index}] must carry the PRINCIPAL role"
            for index, principal in enumerate(self.principals)
            if PrincipalRole.PRINCIPAL not in principal.roles
        )

        if self.queue_states:
            for member in QueueState:
                if member.value not in self.queue_states:
                    failures.append(
                        f"queue_states is missing required key {member.value!r}"
                    )

        if self.workflow_states:
            for stage in LifecycleStage:
                if stage not in self.workflow_states:
                    failures.append(
                        f"workflow_states is missing required stage {stage.value!r}",
                    )

        if self.documents and CHECKPOINT_DOCUMENT_KEY not in self.documents:
            failures.append(
                f"documents is missing the stable checkpoint key "
                f"{CHECKPOINT_DOCUMENT_KEY!r}",
            )

        if self.records and RUN_LOG_RECORD_KEY not in self.records:
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
        """The single principal holding APPROVER authority.

        Refuses when no principal carries the role — an empty board loads,
        and the cost lands here, on the consumer that needs the approval
        act attributed.
        """
        for principal in self.principals:
            if PrincipalRole.APPROVER in principal.roles:
                return principal
        raise OperationMemberAbsentError(
            missing="principal carrying the APPROVER role",
            stops=(
                "approval provenance cannot be attributed, so nothing can be dispatched"
            ),
        )

    def assignee(self) -> Principal:
        """The single principal prepared work is assigned to.

        Refuses when no principal carries the role.  Its reader is the
        reinstated pass path (KOD-60): a fire-prep pass that assigns
        prepared fires, triage filings and decision flags calls this at
        the point of assignment and refuses to run on the error, rather
        than failing boot for an operation that never prepares fires.
        Defined here because this model file has exactly one writer in the
        lane (KOD-56 R3) and it is not the pass reinstatement.
        """
        for principal in self.principals:
            if PrincipalRole.ASSIGNEE in principal.roles:
                return principal
        raise OperationMemberAbsentError(
            missing="principal carrying the ASSIGNEE role",
            stops=(
                "prepared fires, triage filings and decision flags have "
                "no target, so a pass that assigns them refuses to run"
            ),
        )

    def team_keys(self) -> tuple[str, ...]:
        """Every team key this operation declares, in declaration order.

        The container boundary a dispatch scan is narrowed to and every
        candidate is judged against.  Refuses when the config declares no
        team: a workspace holds more than one operation's board, so an
        unbounded scan is not "the whole operation" — it is somebody else's
        work, reachable by an approval act this operation does not own.
        """
        if not self.teams:
            raise OperationMemberAbsentError(
                missing="teams entry",
                stops=(
                    "a dispatch scan has no container to be bounded by, so "
                    "nothing distinguishes this operation's board from any "
                    "other in the workspace and no issue can be selected"
                ),
            )
        return tuple(self.teams)

    def checkpoint_document(self) -> DocumentEntry:
        """The read-side document the scan-window marker lives in.

        Refuses when the registry declares no checkpoint entry.  Its
        reader is the reinstated pass path (KOD-60): a scheduled pass
        establishes its scan window from this document and refuses to run
        without one.  Defined here for the same one-writer reason as
        :meth:`assignee`.
        """
        entry = self.documents.get(CHECKPOINT_DOCUMENT_KEY)
        if entry is None:
            raise OperationMemberAbsentError(
                missing=(f"documents entry under the {CHECKPOINT_DOCUMENT_KEY!r} key"),
                stops=(
                    "the scan-window marker cannot be read, so a scheduled "
                    "pass cannot establish its window and refuses to run"
                ),
            )
        return entry

    def run_log_record(self) -> RecordDestination:
        """The write-side destination a pass records its run-log row to.

        Refuses when the registry declares no run-log entry.  Its reader
        is the reinstated pass path (KOD-60): a pass that records a run-log
        row calls this at the point of writing and refuses without a
        destination.  Defined here for the same one-writer reason as
        :meth:`assignee`.
        """
        entry = self.records.get(RUN_LOG_RECORD_KEY)
        if entry is None:
            raise OperationMemberAbsentError(
                missing=f"records entry under the {RUN_LOG_RECORD_KEY!r} key",
                stops=(
                    "a pass has no destination for its run-log row and "
                    "refuses to record one"
                ),
            )
        return entry


#: Which class every declared field belongs to, and therefore what boot does
#: with it.  A fixed partition in the MODEL rather than a per-field flag,
#: because a flag makes ownership operator-editable data: an operator could
#: mark ``principals`` ensurable and the adapter would try to create a user.
#:
#: ``documents`` is OWNED, as KOD-57 R2 ruled and amendment 3 deferred:
#: :class:`DocumentEntry` now carries the declared ``name`` an ensure keys
#: on, with the id ADOPTED rather than declared, so "create it if absent"
#: has an implementation that leaves the config true.  A document in the
#: KNOWLEDGE system is not this operation's to create and produces no ref;
#: it declares its id at load instead, so nothing about it is silent.
#:
#: ``records`` stays EXTERNAL and the ground is its own rather than
#: inherited.  It is the WRITE-side registry, no pass writes to it in this
#: arrangement (both templates say so), and R7(b) names a read-side
#: document.  Giving it a name and an ensure would instate a destination
#: nothing writes to.  The day a writer exists, the shape :class:`DocumentEntry`
#: now has is the shape it takes.
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
    "documents": ConfigOwnership.OWNED,
    "records": ConfigOwnership.EXTERNAL,
    "knowledge": ConfigOwnership.LOCAL,
    "endpoints": ConfigOwnership.LOCAL,
    "initiatives": ConfigOwnership.EXTERNAL,
    "private_surface": ConfigOwnership.LOCAL,
}
