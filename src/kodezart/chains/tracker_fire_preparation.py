"""Actual addressed first-entry preparation, with no branch or tracker writer."""

from kodezart.chains.scope_walker import read_scope_ready
from kodezart.core.protocols import (
    GitService,
    RepoCache,
    TrackerCriteriaValidator,
    TrackerPort,
)
from kodezart.domain.errors import TrackerFirePreparationError
from kodezart.services.git_observations import read_remote_head
from kodezart.services.repo_observations import ensure_repository
from kodezart.types.domain.branch import BaseSpec, WorkRefRole
from kodezart.types.domain.operation import RunKind
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope_ready import ScopeReadySet
from kodezart.types.domain.tracker_feasibility import (
    TrackerFeasibilityObservation,
    TrackerFeasibilityRequest,
)


class AddressedTrackerFirePreparation:
    """Join native readiness and dispatch facts to the existing fresh validator.

    This first-entry arm pins the native recorded base's actual remote head.
    Existing work refs require the scoped resume selector and refuse here;
    a prior branch's blocker base must never impersonate that branch's head.
    Preparing authorizes no ruling, state transition or loop execution.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        git: GitService,
        cache: RepoCache,
        validator: TrackerCriteriaValidator,
        remote: str,
    ) -> None:
        self._tracker: TrackerPort = tracker
        self._git: GitService = git
        self._cache: RepoCache = cache
        self._validator: TrackerCriteriaValidator = validator
        self._remote = remote

    async def prepare(
        self,
        *,
        selection: ScopeReadySet,
        issue_key: str,
        repo_url: str,
        base_spec: BaseSpec,
        cache_key: str,
        run_identity: RunIdentity | None,
    ) -> TrackerFeasibilityObservation:
        """Validate a first-entry fire only from its own current native sources."""

        def refuse(reason: str) -> TrackerFirePreparationError:
            return TrackerFirePreparationError(issue_key=issue_key, reason=reason)

        if (
            run_identity is None
            or run_identity.kind is not RunKind.FIRE
            or run_identity.name != issue_key
        ):
            raise refuse("the fire run identity does not name the addressed issue")
        targets = tuple(
            row for row in selection.ready if row.issue.issue_key == issue_key
        )
        if len(targets) != 1:
            raise refuse("the addressed issue is not uniquely ready in this scope")
        repository_url = await self._tracker.recorded_repository(issue_key=issue_key)
        if repository_url is None or repository_url != repo_url:
            raise refuse("the queued repository differs from the native issue record")
        recorded_base = await self._tracker.read_base_spec(issue_key=issue_key)
        if recorded_base is None or recorded_base != base_spec:
            raise refuse("the queued base differs from the native dispatch record")
        refs = tuple(await self._tracker.work_refs(issue_key=issue_key))
        if any(row.issue_id != issue_key for row in refs):
            raise refuse("a native work ref names a different issue")
        if any(row.role is not WorkRefRole.INTEGRATION for row in refs):
            raise refuse("scoped resume-head selection is not implemented")
        repository = await ensure_repository(
            cache=self._cache, repo_url=repo_url, cache_key=cache_key
        )
        head = await read_remote_head(
            git=self._git,
            repository=repository,
            remote=self._remote,
            branch=recorded_base.base_branch,
        )
        if head is None:
            raise refuse("the recorded dispatch branch is absent from the remote")
        observed = await self._validator.validate(
            TrackerFeasibilityRequest(
                issue_key=issue_key,
                repo_url=repo_url,
                head_sha=head,
                cache_key=cache_key,
            )
        )
        if observed.spec.subject != issue_key or observed.head_sha != head:
            raise refuse("the validator returned a different subject or head")
        if (
            await self._tracker.recorded_repository(issue_key=issue_key)
            != repository_url
        ):
            raise refuse("the recorded repository changed during preparation")
        if await self._tracker.read_base_spec(issue_key=issue_key) != recorded_base:
            raise refuse("the recorded dispatch base changed during preparation")
        if tuple(await self._tracker.work_refs(issue_key=issue_key)) != refs:
            raise refuse("the issue's work refs changed during preparation")
        if (
            await read_scope_ready(ref=selection.scope.ref, tracker=self._tracker)
            != selection
        ):
            raise refuse("scope readiness changed during preparation")
        if (
            await read_remote_head(
                git=self._git,
                repository=repository,
                remote=self._remote,
                branch=recorded_base.base_branch,
            )
            != head
        ):
            raise refuse("the remote dispatch head changed during preparation")
        return observed
