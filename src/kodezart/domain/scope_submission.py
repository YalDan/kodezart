"""Pure facts about a scope's submissions.

The request that starts one, and which of two live ones goes first.
"""

from collections.abc import Sequence

from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.job import JobRecord
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


def prior_live_job(*, live: Sequence[JobRecord], job_id: str) -> JobRecord | None:
    """The live job over this scope that *job_id* has to yield to, or ``None``.

    *live* is every job addressed at the scope that has not reached TERMINAL,
    oldest submission first. The rule is a total order and nothing else: the
    OLDEST live record goes first, so a job that is itself the oldest yields
    to nobody and every later one yields to that one record.

    Why not "refuse on ANY other live job": two jobs over one scope can both
    be RUNNING at once — the queue marks a record RUNNING before it calls the
    engine, so each of them is live at the moment the other's entry asks —
    and a rule that refused on any other live job would refuse both and the
    scope would never run.

    A *job_id* the sequence does not hold yields to the first live record.
    That is the caller that drove the engine directly rather than through the
    queue: it has no record of its own to be ordered by, so it cannot claim
    to be the earlier of the two.
    """
    oldest = next(iter(live), None)
    if oldest is None or oldest.job_id == job_id:
        return None
    return oldest
