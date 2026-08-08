"""Resolving a lane's base from the tracker graph, and building it if needed.

The rule lives in :mod:`kodezart.domain.base_resolution` and is pure.  This
service is the I/O half: it reads the ``blockedBy`` edges and the recorded
work refs through ``TrackerPort``, asks ``GitService`` which refs contain
which, hands the resolved values to the rule, and — on the combined arm only
— constructs the integration ref the rule named.

Two things it deliberately does NOT do.  It never reads a pull request's
merge or open/closed state: under the standing ruling an open unmerged pull
request is the steady state of every finished lane, so pull-request state is
not an input to base resolution at all.  And it never substitutes trunk for
a premise it could not locate — a lane whose base cannot be resolved does
not dispatch.
"""

from collections.abc import Sequence
from datetime import datetime

from kodezart.core.logging import BoundLogger, get_logger
from kodezart.core.protocols import GitService, TrackerPort
from kodezart.domain.base_resolution import BasePlan, resolve_base
from kodezart.domain.errors import (
    BaseIntegrationConflictError,
    BaseResolutionError,
    MergeConflictError,
)
from kodezart.types.domain.branch import BaseInput, BaseSpec, WorkRef, WorkRefRole
from kodezart.types.domain.tracker import IssueRelationKind


class BaseResolver:
    """Resolves the base an issue's lane must be dispatched on."""

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        git: GitService,
        remote: str,
    ) -> None:
        self._tracker: TrackerPort = tracker
        self._git: GitService = git
        self._remote: str = remote
        self._log: BoundLogger = get_logger(__name__)

    async def resolve(
        self,
        *,
        issue_key: str,
        repo_path: str,
        integration_workspace: str,
        trunk: str,
        now: datetime,
    ) -> BaseSpec:
        """The ``BaseSpec`` for *issue_key*, constructing its base if needed."""
        blocker_keys = await self._blocker_keys(issue_key)
        inputs = [
            await self._input_for(issue_key=issue_key, blocker_key=blocker_key)
            for blocker_key in blocker_keys
        ]
        for item in inputs:
            await self._require_present_on_remote(
                issue_key=issue_key,
                item=item,
                repo_path=repo_path,
            )
        plan = resolve_base(
            issue_id=issue_key,
            blocker_inputs=inputs,
            containment=await self._containment(inputs, repo_path=repo_path),
            trunk=trunk,
        )
        if plan.requires_construction():
            await self._construct(
                issue_key=issue_key,
                plan=plan,
                repo_path=repo_path,
                integration_workspace=integration_workspace,
                now=now,
            )
        return plan.spec

    async def _blocker_keys(self, issue_key: str) -> tuple[str, ...]:
        issue = await self._tracker.read_issue(issue_key=issue_key)
        return tuple(
            relation.issue_key
            for relation in issue.relations
            if relation.kind is IssueRelationKind.BLOCKED_BY
        )

    async def _input_for(self, *, issue_key: str, blocker_key: str) -> BaseInput:
        """The blocker's deliverable ref, or the nearest ancestor's.

        A blocker is frequently not the issue whose branch delivers it —
        work riding another issue's pull request records no ref of its own.
        A resolution assuming otherwise would refuse to dispatch a lane
        whose premise is in fact present.
        """
        ref = await self._nearest_deliverable_ref(blocker_key)
        if ref is None:
            raise BaseResolutionError(
                "no deliverable ref on the blocker or any of its ancestors",
                issue_id=issue_key,
                blocker_issue_ids=(blocker_key,),
            )
        if ref.pushed_head_sha is None:
            raise BaseResolutionError(
                "the blocker's deliverable ref has never been pushed",
                issue_id=issue_key,
                blocker_issue_ids=(blocker_key,),
                branches=(ref.branch,),
            )
        return BaseInput(
            blocker_issue_id=blocker_key,
            branch=ref.branch,
            sha=ref.pushed_head_sha,
        )

    async def _nearest_deliverable_ref(self, blocker_key: str) -> WorkRef | None:
        seen: set[str] = set()
        cursor: str | None = blocker_key
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            for ref in await self._tracker.list_work_refs(issue_key=cursor):
                if ref.role is WorkRefRole.DELIVERABLE:
                    return ref
            cursor = (await self._tracker.read_issue(issue_key=cursor)).parent_key
        return None

    async def _require_present_on_remote(
        self,
        *,
        issue_key: str,
        item: BaseInput,
        repo_path: str,
    ) -> None:
        sha = await self._git.remote_branch_sha(repo_path, self._remote, item.branch)
        if sha is None:
            raise BaseResolutionError(
                "the blocker's deliverable ref is absent from the remote",
                issue_id=issue_key,
                blocker_issue_ids=(item.blocker_issue_id,),
                branches=(item.branch,),
            )

    async def _containment(
        self,
        inputs: Sequence[BaseInput],
        *,
        repo_path: str,
    ) -> tuple[tuple[str, str], ...]:
        """``(contained, containing)`` for every ordered pair of distinct refs."""
        branches = sorted({item.branch for item in inputs})
        pairs: list[tuple[str, str]] = []
        for contained in branches:
            for containing in branches:
                if contained == containing:
                    continue
                if await self._git.is_ancestor(repo_path, contained, containing):
                    pairs.append((contained, containing))
        return tuple(pairs)

    async def _construct(
        self,
        *,
        issue_key: str,
        plan: BasePlan,
        repo_path: str,
        integration_workspace: str,
        now: datetime,
    ) -> None:
        """Build the integration ref, push it, and record it at its role.

        Rebuilt, never patched: the branch name is a digest over the ordered
        inputs, so a change to any input yields a NEW ref rather than
        advancing an existing one under a graded branch.
        """
        branch = plan.spec.base_branch
        await self._git.create_worktree(
            repo_path,
            str(plan.branch_point),
            integration_workspace,
            branch,
            create_branch=True,
        )
        try:
            for source in plan.merge_inputs:
                await self._git.merge_branch(integration_workspace, source)
        except MergeConflictError as exc:
            await self._git.remove_worktree(repo_path, integration_workspace)
            raise BaseIntegrationConflictError(
                "inputs to the integration base conflict",
                issue_id=issue_key,
                branches=tuple(item.branch for item in plan.spec.inputs),
                paths=exc.paths,
            ) from exc
        await self._git.push(integration_workspace, branch)
        sha = await self._git.current_sha(integration_workspace)
        await self._git.remove_worktree(repo_path, integration_workspace)
        await self._tracker.record_work_ref(
            ref=WorkRef(
                issue_id=issue_key,
                role=WorkRefRole.INTEGRATION,
                branch=branch,
                pushed_head_sha=sha,
                recorded_at=now,
            ),
        )
        await self._log.ainfo(
            "base_integration_ref_constructed",
            issue_key=issue_key,
            branch=branch,
            inputs=[item.branch for item in plan.spec.inputs],
        )
