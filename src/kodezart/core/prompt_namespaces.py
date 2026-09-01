"""The three prompt binding namespaces and their disjointness assertion.

Rendering has exactly three binding sources:

1. per-call typed variables supplied by the dispatching node,
2. set-level fragments contributed by the resolved prompt set,
3. the OperationConfig namespace registered at boot.

They must be disjoint: a name that two sources could both supply has no
defined meaning.  Boot asserts it rather than discovering it at render time.
"""

from collections.abc import Mapping, Sequence

from kodezart.core.errors import PromptNamespaceCollisionError
from kodezart.types.domain.operation import (
    OperationConfig,
    PrincipalRole,
    RunKind,
)

SET_FRAGMENT_NAMES: frozenset[str] = frozenset({"skills_reference"})

# Every name a dispatching node binds per call. Kept explicit so the boot
# disjointness assertion has a real set to check, and pinned by a test that
# compares it against what the shipped templates actually reference.
PER_CALL_VARIABLE_NAMES: frozenset[str] = frozenset(
    {
        "task",
        "task_md",
        "task_description",
        "base_ref",
        "validation_findings",
        "prior_prompt",
        "pending_failures",
        "criteria",
        "acceptance_criteria",
        "total_iterations",
        "review_feedback",
        "ci_summary",
        "original_ticket",
        "done_work",
        "failure_evidence",
        "draft_md",
        "previous_draft_md",
        "reviewer_feedback",
        "reviewer_suggestions",
        "reviewer_suggestions_absent",
        "commit_count",
        "file_paths",
        "file_paths_absent",
        "commit_subjects",
        "changeset_is_empty",
        "changeset_has_commits",
        "content",
        "destination",
    }
)


def _bind_absentable(
    bindings: dict[str, object],
    name: str,
    value: object,
    *,
    absent: bool,
) -> None:
    """The mutually exclusive ``name`` / ``name_absent`` pair.

    ``{{#if}}`` treats ``None`` as absent, so the two renderings are
    selected by two mutually exclusive bindings rather than by an
    else-branch the renderer does not have: exactly one of the pair is
    ever non-``None``.  An UNGUARDED reference over the absent state then
    fails loudly as an unbound placeholder — the one outcome no state
    produces is a blank render.
    """
    bindings[name] = None if absent else value
    bindings[f"{name}_absent"] = True if absent else None


def operation_bindings(config: OperationConfig) -> dict[str, object]:
    """The OperationConfig namespace as render bindings.

    Bare names for the two scalars, dotted namespaces for the mappings.
    Nothing here is a per-call value and nothing here is a fragment.

    Every binding that can be absent — the eleven collections, the
    private-surface prose, a principal's forge handle, an unadopted
    document id, a gate step's dependency — is three-state: the value, or
    the paired absent marker, never a hole.

    Two shapes, chosen by how a template addresses the collection.

    A collection a pass ENUMERATES is a LIST, iterated with ``{{#each}}``:
    ``teams`` and ``repos`` are the operation's declared roster, and a pass
    renders every member of it.  Whatever the config declares renders, in
    declaration order, so a third team or a third repository reaches the
    prompt without a template edit.  The enumeration-with-a-conjunction
    concern that ruled these into flat positions (KOD-60 R16) dissolves
    with the prose it was about: the rewritten roster passages enumerate as
    lines, one member per line, and a line list needs no separator
    construct the renderer does not have.

    A collection a pass addresses SINGLY stays keyed, because a role or a
    position is what the template names: ``principals.approver``,
    ``principals.assignee`` and ``principals.1`` by role and position,
    ``agent_identities.0`` and ``initiatives.1`` by position, and
    ``documents``, ``records``, ``knowledge``, ``queue_states``,
    ``workflow_states`` and ``endpoints`` by their configured key.  A role,
    position or key the config does not declare is an unbound placeholder
    and the render refuses, naming it — the refusal at the point of need.
    """
    bindings: dict[str, object] = {
        "operation_name": config.operation_name,
        "workspace": config.workspace,
    }
    _bind_absentable(
        bindings,
        "queue_states",
        dict(config.queue_states),
        absent=not config.queue_states,
    )
    _bind_absentable(
        bindings,
        "workflow_states",
        {stage.value: label for stage, label in config.workflow_states.items()},
        absent=not config.workflow_states,
    )
    # The roster a pass enumerates. ``repository`` is three-state per
    # entry: an operation declaring one repository binds every team to it
    # implicitly and declares nothing, so the absent marker is what a
    # template says "the only declared repository" with.
    _bind_absentable(
        bindings,
        "teams",
        [
            {
                "name": entry.name,
                "key": entry.key,
                "repository": entry.repository,
                "repository_absent": True if entry.repository is None else None,
            }
            for entry in config.teams.values()
        ],
        absent=not config.teams,
    )
    # An id alone renders as an opaque token no reader can resolve, so
    # every document and record reference carries its system beside it.
    # A TRACKER document's id is three-state on the model — absent means
    # "not adopted yet" — and the binding says so rather than rendering a
    # hole.
    _bind_absentable(
        bindings,
        "documents",
        {
            key: {
                "system": entry.system.value,
                "id": entry.id,
                "id_absent": True if entry.id is None else None,
            }
            for key, entry in config.documents.items()
        },
        absent=not config.documents,
    )
    # The record registry is a TOTAL per-kind namespace: its legal keys are
    # exactly the run kinds (refused otherwise at load, KOD-170), so every
    # kind binds the house pair — the declared destination, or the named
    # absence a pass renders its record-nothing arm from.  No whole-registry
    # marker exists: an empty [records] table IS three named absences, and
    # the RunRecorder routes off the same declaration this binds.
    records_namespace: dict[str, object] = {}
    for kind in RunKind:
        entry = config.records.get(kind.value)
        records_namespace[kind.value] = (
            None
            if entry is None
            else {
                "system": entry.system.value,
                "name": entry.name,
                "id": entry.id,
                "append_only": entry.append_only,
            }
        )
        records_namespace[f"{kind.value}_absent"] = True if entry is None else None
    bindings["records"] = records_namespace
    _bind_absentable(
        bindings,
        "knowledge",
        dict(config.knowledge),
        absent=not config.knowledge,
    )
    _bind_absentable(
        bindings,
        "private_surface",
        config.private_surface,
        absent=config.private_surface is None,
    )
    _bind_absentable(
        bindings,
        "endpoints",
        dict(config.endpoints),
        absent=not config.endpoints,
    )
    # ``target_date`` is absent on a real initiative more often than not.
    _bind_absentable(
        bindings,
        "initiatives",
        {
            str(index): {
                "id": item.id,
                "target_date": (
                    None if item.target_date is None else item.target_date.isoformat()
                ),
                "target_date_absent": True if item.target_date is None else None,
            }
            for index, item in enumerate(config.initiatives)
        },
        absent=not config.initiatives,
    )
    # ``handle`` is the identifier a MENTION is recognised by and
    # ``tracker_user`` the display identity the tracker names the principal
    # by — what its user listing reports and what an authored act comes
    # back attributed to, never an authority check (KOD-144). A sweep
    # given only the second has nothing to match on.  ``forge_handle`` is
    # the same principal's name on the forge; a principal who never
    # appears there has none, and the absent case is named.  Beside the
    # positions, the two roles the routines address singly are keyed by
    # role: ``approver`` exists whenever principals do (exactly one is
    # validated at load), ``assignee`` only when a principal carries the
    # role — an unbound ``principals.assignee`` reference is the typed
    # refusal for an operation that declares none.
    principal_views = [
        {
            "tracker_user": p.tracker_user,
            "roles": ", ".join(sorted(role.value for role in p.roles)),
            "handle": p.handle,
            "forge_handle": p.forge_handle,
            "forge_handle_absent": True if p.forge_handle is None else None,
        }
        for p in config.principals
    ]
    principals_namespace: dict[str, object] = {
        str(index): view for index, view in enumerate(principal_views)
    }
    for index, principal in enumerate(config.principals):
        if PrincipalRole.APPROVER in principal.roles:
            principals_namespace["approver"] = principal_views[index]
        if PrincipalRole.ASSIGNEE in principal.roles:
            principals_namespace["assignee"] = principal_views[index]
    _bind_absentable(
        bindings,
        "principals",
        principals_namespace,
        absent=not config.principals,
    )
    _bind_absentable(
        bindings,
        "agent_identities",
        {
            str(index): identity
            for index, identity in enumerate(config.agent_identities)
        },
        absent=not config.agent_identities,
    )
    # A step naming no dependency is a GATE; the absent marker is what a
    # template says "a gate" with, rather than rendering a hole where the
    # ancestor's name would be.  ``name`` and ``slug`` are the display
    # forms the routines write a repository with — the short name and the
    # owner/name form — derived from the one declared ``url`` so the three
    # can never drift apart.
    _bind_absentable(
        bindings,
        "repos",
        [
            {
                "url": repo.url,
                "name": _repo_display(repo.url)[0],
                "slug": _repo_display(repo.url)[1],
                "trunk": repo.trunk,
                "checks": [
                    {
                        "name": step.name,
                        "command": step.command,
                        "depends_on": step.depends_on,
                        "depends_on_absent": (
                            True if step.depends_on is None else None
                        ),
                    }
                    for step in repo.checks
                ],
            }
            for repo in config.repos
        ],
        absent=not config.repos,
    )
    return bindings


def _repo_display(url: str) -> tuple[str, str]:
    """``(name, slug)`` — the short and owner/name forms of a repository URL."""
    trimmed = url.rstrip("/")
    if trimmed.endswith(".git"):
        trimmed = trimmed.removesuffix(".git")
    segments = [segment for segment in trimmed.split("/") if segment]
    return segments[-1], "/".join(segments[-2:])


def assert_namespaces_disjoint(operation_names: Sequence[str]) -> None:
    """Raise when the three binding namespaces overlap."""
    operation = set(operation_names)
    colliding = sorted(
        (operation & PER_CALL_VARIABLE_NAMES)
        | (operation & SET_FRAGMENT_NAMES)
        | (PER_CALL_VARIABLE_NAMES & SET_FRAGMENT_NAMES)
    )
    if colliding:
        msg = "Prompt binding namespaces are not disjoint"
        raise PromptNamespaceCollisionError(msg, colliding=colliding)


def bindings_for(config: OperationConfig | None) -> Mapping[str, object]:
    """Boot-time bindings for the registry: the operation namespace, checked.

    ``None`` means no operation config is configured; the namespace is then
    empty and disjointness is trivially satisfied.
    """
    if config is None:
        assert_namespaces_disjoint(())
        return {}
    bindings = operation_bindings(config)
    assert_namespaces_disjoint(sorted(bindings))
    return bindings
