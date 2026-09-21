"""The submission one standing scope's own run is started with."""

from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.operation import OrganizeScopeBinding
from kodezart.types.domain.session import PermissionMode, ToolPreset
from kodezart.types.domain.workflow import WorkflowSubmission


def standing_scope_submission(
    *,
    binding: OrganizeScopeBinding,
    trunk: str,
) -> WorkflowSubmission:
    """The request that starts a run of *binding*'s scope against *trunk*.

    Pure, and the whole of what a declared row becomes: the scope is the
    address the scoped arm walks, the repository is the binding's own, and
    the base is that repository's trunk because a scope run has no blocker
    to stand on and resolves each lane's base for itself.

    The prompt names the scope and carries nothing else. The model requires
    a nonempty one and the scoped engine reads none — each lane's subject is
    its own issue body — so any prose here would be text no session is ever
    sent. ``issue_key`` is absent for the same reason: the run is addressed
    at the scope, and the lanes it walks are read from it rather than
    dispatched one at a time.
    """
    return WorkflowSubmission(
        prompt=f"scope {binding.scope.kind.value} {binding.scope.key}",
        issue_key=None,
        repo_path=None,
        repo_url=binding.repo_url,
        base_spec=trunk_base(trunk),
        implied_base=None,
        scope=binding.scope,
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=ToolPreset.IMPLEMENTATION,
    )
