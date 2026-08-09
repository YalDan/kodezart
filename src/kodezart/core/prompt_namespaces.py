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
from kodezart.types.domain.operation import OperationConfig

SET_FRAGMENT_NAMES: frozenset[str] = frozenset({"skills_reference"})

# Every name a dispatching node binds per call. Kept explicit so the boot
# disjointness assertion has a real set to check, and pinned by a test that
# compares it against what the shipped templates actually reference.
PER_CALL_VARIABLE_NAMES: frozenset[str] = frozenset(
    {
        "task",
        "task_md",
        "task_description",
        "prior_prompt",
        "pending_failures",
        "criteria",
        "acceptance_criteria",
        "total_iterations",
        "review_feedback",
        "ci_summary",
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


def operation_bindings(config: OperationConfig) -> dict[str, object]:
    """The OperationConfig namespace as render bindings.

    Bare names for the two scalars, dotted namespaces for the mappings.
    Nothing here is a per-call value and nothing here is a fragment.
    """
    return {
        "operation_name": config.operation_name,
        "workspace": config.workspace,
        "queue_states": dict(config.queue_states),
        "workflow_states": {
            stage.value: label for stage, label in config.workflow_states.items()
        },
        "teams": dict(config.teams),
        # An id alone renders as an opaque token no reader can resolve, so
        # every document and record reference carries its system beside it.
        "documents": {
            key: {"system": entry.system.value, "id": entry.id}
            for key, entry in config.documents.items()
        },
        "records": {
            key: {
                "system": entry.system.value,
                "id": entry.id,
                "append_only": entry.append_only,
            }
            for key, entry in config.records.items()
        },
        "knowledge": dict(config.knowledge),
        "private_surface": config.private_surface,
        "endpoints": dict(config.endpoints),
        # ``target_date`` is absent on a real initiative more often than not.
        # ``{{#if}}`` treats ``None`` as absent, so the two renderings are
        # selected by two mutually exclusive bindings rather than by an
        # else-branch the renderer does not have: exactly one of the pair is
        # ever non-``None``.
        "initiatives": [
            {
                "id": item.id,
                "target_date": (
                    None if item.target_date is None else item.target_date.isoformat()
                ),
                "target_date_absent": True if item.target_date is None else None,
            }
            for item in config.initiatives
        ],
        # ``handle`` is the identifier a MENTION is recognised by and
        # ``tracker_user`` the one authority is checked against. A sweep
        # given only the second has nothing to match on.
        "principals": [
            {
                "tracker_user": p.tracker_user,
                "role": p.role.value,
                "handle": p.handle,
            }
            for p in config.principals
        ],
        "agent_identities": list(config.agent_identities),
        "repos": [
            {
                "url": repo.url,
                "check_commands": [
                    {
                        "name": step.name,
                        "command": step.command,
                        "depends_on": step.depends_on,
                    }
                    for step in repo.check_commands
                ],
            }
            for repo in config.repos
        ],
    }


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
