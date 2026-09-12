"""Run a live scope inside its existing queue job, one fresh lane per tick."""

from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from pydantic import TypeAdapter

from kodezart.chains.criteria import require_current_native_snapshot
from kodezart.chains.native_delivery import NativeLaneWorkflow
from kodezart.chains.scope_walker import read_scope_ready
from kodezart.core.protocols import DeliveryProbe, RepoCache, TrackerPort
from kodezart.domain.errors import ScopedExecutionUnavailableError, ScopeReadError
from kodezart.domain.git_url import resolve_repo_url
from kodezart.services.base_resolver import BaseResolver
from kodezart.types.domain.agent import AgentEvent, WorkflowCompleteEvent
from kodezart.types.domain.branch import BaseSpec, WorkRefRole
from kodezart.types.domain.dispatch import ExclusionClause, IssueExclusion
from kodezart.types.domain.native_delivery import (
    NativeDeliveryState,
    PendingLaneDelivery,
    SkippedLaneDelivery,
)
from kodezart.types.domain.operation import RepoEntry, RunKind
from kodezart.types.domain.run_records import RunIdentity
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_ready import ScopeReadySet
from kodezart.types.domain.scope_runtime import (
    ScopeLaneEvent,
    ScopeWalkEvent,
    ScopeWalkObservation,
)
from kodezart.types.domain.session import AllowedTools, PermissionMode
from kodezart.types.domain.tracker import WorkflowStateKind
from kodezart.types.domain.workflow import ExecutionContext

_REQUEST_METADATA = "scope_lane_request"
_RUN_METADATA = "scope_lane_run_identity"
_NATIVE_STATE = TypeAdapter(NativeDeliveryState)


class ScopeWorkflowEngine:
    """The sole lane selector inside a scope request.

    No child is submitted to the queue that is already running this scope.
    A lane's checkpoint address is separate from the real queue job's holder
    identity. This owner writes neither tracker state nor approval/claim marks.
    The invocation stops after currently eligible lanes have each fired once;
    remaining tracker obligations stay explicit for a later invocation.
    """

    def __init__(
        self,
        *,
        tracker: TrackerPort,
        lane_for: Callable[[str], NativeLaneWorkflow],
        probe_for: Callable[[str], DeliveryProbe | None],
        resolver: BaseResolver,
        cache: RepoCache,
        repositories: Sequence[RepoEntry],
        git_base_url: str,
        integration_workspace_dir: str,
    ) -> None:
        self._tracker = tracker
        self._lane_for = lane_for
        self._probe_for = probe_for
        self._resolver = resolver
        self._cache = cache
        self._repositories = repositories
        self._git_base_url = git_base_url
        self._integration_workspace_dir = integration_workspace_dir

    async def run(
        self,
        *,
        prompt: str,
        issue_key: str | None = None,
        run_identity: RunIdentity | None = None,
        repo_path: str | None,
        repo_url: str | None,
        base_spec: BaseSpec,
        scope: ScopeRef | None,
        implied_base: BaseSpec | None = None,
        permission_mode: PermissionMode,
        allowed_tools: AllowedTools,
        cache_key: str,
    ) -> AsyncIterator[AgentEvent]:
        """Reread selection before every fire; never infer closure from a fire exit."""
        if scope is None:
            raise ValueError("The scope controller requires an addressed scope")
        if issue_key is not None and (
            scope.kind is not ScopeKind.ISSUE or scope.key != issue_key
        ):
            raise ScopeReadError(
                "scope and submitted issue identity disagree", ref=scope
            )
        if repo_url is None:
            raise ScopedExecutionUnavailableError(
                "Scope execution requires a declared repository", ref=scope
            )
        url = resolve_repo_url(repo_url, self._git_base_url)
        entries = [
            repo
            for repo in self._repositories
            if resolve_repo_url(repo.url, self._git_base_url) == url
        ]
        if len(entries) != 1:
            raise ScopeReadError("scope repository is absent or ambiguous", ref=scope)
        repo = entries[0]
        lane = self._lane_for(url)
        probe = self._probe_for(url)
        if probe is None:
            raise ScopedExecutionUnavailableError(
                "Scope execution requires an open-delivery reader for this origin",
                ref=scope,
            )
        # The request's base describes the scope input, never an independently
        # trusted lane base. Each lane's graph gets the current resolver answer.
        _ = prompt, run_identity, base_spec, implied_base
        dispatched: list[str] = []
        skipped: list[str] = []
        tick = 0
        while True:
            tick += 1
            ready = await read_scope_ready(ref=scope, tracker=self._tracker)
            exclusions = [
                IssueExclusion(
                    issue_key=blocked.issue_key,
                    clause=ExclusionClause.LIVE_BLOCKER,
                    detail=key,
                )
                for blocked in ready.blocked
                for key in blocked.blocker_keys
            ]
            selected = None
            for candidate in ready.ready:
                if candidate.issue.issue_key in dispatched:
                    continue
                if await probe.open_delivery_exists(
                    repo_url=url, issue_key=candidate.issue.issue_key
                ):
                    exclusions.append(
                        IssueExclusion(
                            issue_key=candidate.issue.issue_key,
                            clause=ExclusionClause.OPEN_DELIVERY,
                        )
                    )
                    continue
                selected = candidate
                break
            yield _observation(scope, tick, ready, dispatched, skipped, exclusions)
            if selected is None:
                return
            key = selected.issue.issue_key
            lane_key = _lane_checkpoint_key(cache_key, key)
            path = repo_path or await self._cache.ensure_available(url, lane_key)
            spec = await self._resolver.resolve(
                issue_key=key,
                repo_path=path,
                integration_workspace=str(
                    Path(self._integration_workspace_dir) / lane_key
                ),
                trunk=repo.trunk,
                now=datetime.now(tz=UTC),
            )
            # Base resolution and cache I/O can yield to tracker changes. Do not
            # run the candidate merely because an earlier tick admitted it.
            refreshed = await read_scope_ready(ref=scope, tracker=self._tracker)
            current = next(
                (row for row in refreshed.ready if row.issue.issue_key == key), None
            )
            if current is None or current != selected:
                continue
            if await probe.open_delivery_exists(repo_url=url, issue_key=key):
                continue
            fire_state, config = lane.fire.prepare(
                prompt=current.issue.body or current.issue.title,
                issue_key=key,
                scope=ScopeRef(kind=ScopeKind.ISSUE, key=key),
                repo_path=path,
                repo_url=url,
                base_spec=spec,
                implied_base=spec,
                permission_mode=permission_mode,
                allowed_tools=allowed_tools,
                cache_key=lane_key,
                run_identity=RunIdentity(
                    kind=RunKind.FIRE, name=key, started_at=datetime.now(tz=UTC)
                ),
            )
            context = ExecutionContext.from_configurable(config)
            address = {
                "job": cache_key,
                "scope": scope.model_dump_json(),
                "issue": key,
                "repo_url": context.repo_url,
                "repo_path": context.repo_path,
                "base": spec.model_dump_json(),
            }
            assert context.run_identity is not None
            config["metadata"] = {
                _REQUEST_METADATA: address,
                _RUN_METADATA: context.run_identity.model_dump(mode="json"),
            }
            initial: NativeDeliveryState | None = lane.prepare(fire_state)
            if lane.fire.checkpointer is not None:
                saved = await lane.graph.aget_state(config)
                if saved.values:
                    if (saved.metadata or {}).get(_REQUEST_METADATA) != address:
                        raise ScopeReadError(
                            "native checkpoint belongs to a different scope request",
                            ref=scope,
                        )
                    saved_state = _NATIVE_STATE.validate_python(saved.values)
                    if (
                        saved_state["issue_key"] != key
                        or saved_state["repo_url"] != context.repo_url
                    ):
                        raise ScopeReadError(
                            "native checkpoint state differs from its request identity",
                            ref=scope,
                        )
                    original_run = RunIdentity.model_validate(
                        (saved.metadata or {}).get(_RUN_METADATA)
                    )
                    if (
                        original_run.kind is not RunKind.FIRE
                        or original_run.name != key
                    ):
                        raise ScopeReadError(
                            "native checkpoint run identity differs from its lane",
                            ref=scope,
                        )
                    config["configurable"]["run_identity"] = original_run.model_dump()
                    config["metadata"][_RUN_METADATA] = original_run.model_dump(
                        mode="json"
                    )
                    # Even a fully completed checkpoint must not replay a cached
                    # acceptance without asking today's criterion authority.
                    await require_current_native_snapshot(
                        saved_state, reader=lane.fire.criteria
                    )
                    initial = None
            if initial is not None:
                refs = await self._tracker.work_refs(issue_key=key)
                if any(ref.role is WorkRefRole.DELIVERABLE for ref in refs):
                    raise ScopeReadError(
                        "recorded branch requires validated cross-job reentry; "
                        "refusing to mint",
                        ref=scope,
                    )
            dispatched.append(key)
            final: NativeDeliveryState | None = None
            async for namespace, mode, payload in lane.graph.astream(
                initial,
                config=config,
                stream_mode=["custom", "values"],
                subgraphs=True,
            ):
                if mode == "values" and not namespace:
                    final = _NATIVE_STATE.validate_python(payload)
                elif mode == "custom":
                    if not isinstance(payload, AgentEvent):
                        raise TypeError("Native lane emitted a non-AgentEvent")
                    if not isinstance(payload, WorkflowCompleteEvent):
                        yield ScopeLaneEvent(lane_key=key, event=payload)
            if final is None or isinstance(final["delivery"], PendingLaneDelivery):
                raise ScopeReadError(
                    "native lane has no final delivery phase", ref=scope
                )
            if isinstance(final["delivery"], SkippedLaneDelivery):
                skipped.append(key)


def _lane_checkpoint_key(job_id: str, lane_key: str) -> str:
    """A graph namespace, never a new queue job or writable-surface holder."""
    return f"{job_id}-scope-{sha256(lane_key.encode()).hexdigest()}"


def _observation(
    scope: ScopeRef,
    tick: int,
    ready: ScopeReadySet,
    dispatched: list[str],
    skipped: list[str],
    exclusions: list[IssueExclusion],
) -> ScopeWalkEvent:
    return ScopeWalkEvent(
        observation=ScopeWalkObservation(
            scope=scope,
            tick=tick,
            ready=tuple(row.issue.issue_key for row in ready.ready),
            dispatched=tuple(dispatched),
            skipped_lanes=tuple(skipped),
            unresolved_criteria=tuple(
                row.issue_key
                for row in ready.criteria
                if row.state_kind is not WorkflowStateKind.COMPLETED
            ),
            unapproved_lanes=ready.unapproved,
            exclusions=tuple(exclusions),
        )
    )
