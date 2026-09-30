"""Build the scratch scope a live walk runs against, once, and refuse anything else.

The shape is recorded rather than invented: one project; lane A with three
criteria; lane B with two, blocked by A; lane C with three, so a walk of it is
long enough to be killed mid-loop. Every lane's work is confined to new files
under ``scripts/`` and to Node built-ins, so a run can be read without a
toolchain. All issues start unstarted, nothing carries a label but the
structural criterion label on the sub-issues, and no approval label is set
anywhere — approving a scope is a person's act, so neither this builder nor a
run performs it.

**Never a live board, as a mechanism rather than a warning.** Two conditions
are checked over a fresh listing before EVERY write, the plants included:

* the project's own description carries ``kodezart-scratch-scope`` as a whole
  line of its own, which is one act a person performs on a project they mean
  to hand over; and
* the project holds no issue this builder did not create — anything that is
  not a planned lane or one of that lane's planned criteria, and any duplicate
  of a planned title.

Either failing is a :class:`ScratchScopeRefusalError` raised before a single write.
The declaration alone would let an empty live project through; the ownership
rule alone would let a builder point itself at any empty project it liked.

**Re-entrant by construction.** The board this runs against is usually one that
already exists, so every act is check-before-create: a second build adopts every
lane, criterion and edge it finds and writes nothing, and a partly built board
is completed rather than duplicated.

Run it as ``uv run python -m tests.tools.scratch_scope build --operation-config
… --team … --project … --repo-url …``. Nothing in the repository's own test run
invokes that entry point; the cases beside this module drive the builder over a
double.
"""

import argparse
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from kodezart.adapters.linear.tracker import (
    _TOOL_GET_ISSUE,
    _TOOL_GET_PROJECT,
    _TOOL_LIST_ISSUE_STATUSES,
    _TOOL_LIST_ISSUES,
    _TOOL_SAVE_ISSUE,
)
from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.tracker import make_mcp_tool_caller, refuse_foreign_credential
from kodezart.config.app import AppConfig
from kodezart.core.protocols import McpToolCaller
from kodezart.domain.criterion_creation import criterion_body
from kodezart.domain.criterion_evidence import apply_evidence
from kodezart.types.domain.criterion_evidence import CriterionEvidence
from kodezart.types.domain.operation import LifecycleStage, OperationConfig

#: The line a project must carry, on its own, before this builder writes to it.
#: A person puts it there; a builder that could declare its own target could be
#: pointed anywhere.
SCRATCH_DECLARATION = "kodezart-scratch-scope"

#: How many issues a listing page carries, and how many offenders a refusal
#: names before it stops listing them.
PAGE_LIMIT = 50
NAMED_OFFENDERS = 10

#: The workflow-state kind every issue this builder creates starts in.
UNSTARTED = "unstarted"


class ScratchScopeRefusalError(RuntimeError):
    """The target is not this builder's to write to, or cannot be read whole."""

    def __init__(self, reason: str, *, foreign: Sequence[str] = ()) -> None:
        detail = f"{reason}: {', '.join(foreign)}" if foreign else reason
        super().__init__(detail)
        self.reason = reason
        self.foreign: tuple[str, ...] = tuple(foreign)


@dataclass(frozen=True, slots=True)
class CriterionPlan:
    """One criterion: the title it is found by, and its two written fields."""

    title: str
    check: str
    do: str


@dataclass(frozen=True, slots=True)
class LanePlan:
    """One lane issue, its criteria, and the lanes it waits for."""

    name: str
    title: str
    body: str
    criteria: tuple[CriterionPlan, ...]
    blocked_by: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScratchScopePlan:
    """The whole recorded shape, as data rather than as a sequence of calls."""

    lanes: tuple[LanePlan, ...]

    def lane(self, name: str) -> LanePlan:
        return next(lane for lane in self.lanes if lane.name == name)

    def titles(self) -> tuple[str, ...]:
        """Every title the plan owns, lanes and criteria alike."""
        return tuple(
            title
            for lane in self.lanes
            for title in (lane.title, *(item.title for item in lane.criteria))
        )


@dataclass(frozen=True, slots=True)
class ScratchTarget:
    """Where the plan is built, resolved from the operation before any call.

    ``team`` is the tracker's own display name for the declared team, which is
    what the backend's issue and status tools take.
    """

    team: str
    project: str
    repo_url: str
    criterion_label: str
    done_state: str


@dataclass(frozen=True, slots=True)
class BuiltScope:
    """What the board holds afterwards, and which half of it this run wrote."""

    project_id: str
    lanes: Mapping[str, str]
    criteria: Mapping[str, tuple[str, ...]]
    created: tuple[str, ...] = ()
    adopted: tuple[str, ...] = ()


def _lane(
    name: str, *, subject: str, blocked_by: tuple[str, ...], checks: Sequence[str]
) -> LanePlan:
    """One lane of the recorded shape, written the way an organize step writes.

    Every lane's work is new files under ``scripts/`` using Node built-ins
    only: nothing it does depends on a toolchain, a network or another lane's
    files, so a failure is the walk's and never the fixture's.
    """
    body = (
        f"{subject}\n\n"
        f"Add new files under `scripts/` only. Node built-ins only — no "
        f"dependency is added and no existing file is edited."
    )
    return LanePlan(
        name=name,
        title=f"Scratch lane {name} — {subject}",
        body=body,
        criteria=tuple(
            CriterionPlan(
                title=f"{name}{index} — {check}",
                check=(
                    f"`node scripts/{name.lower()}.mjs {check.split()[0].lower()}` "
                    f"prints the {check} line and exits zero."
                ),
                do=(
                    f"Write the {check} step into `scripts/{name.lower()}.mjs` "
                    f"using Node built-ins only."
                ),
            )
            for index, check in enumerate(checks, start=1)
        ),
        blocked_by=blocked_by,
    )


def scratch_scope_plan() -> ScratchScopePlan:
    """The recorded shape: A with three, B with two behind A, C with three."""
    return ScratchScopePlan(
        lanes=(
            _lane(
                "A",
                subject="a line reader",
                blocked_by=(),
                checks=("read", "count", "report"),
            ),
            _lane(
                "B",
                subject="a summary over the reader",
                blocked_by=("A",),
                checks=("summarise", "format"),
            ),
            _lane(
                "C",
                subject="a standalone timer",
                blocked_by=(),
                checks=("start", "tick", "stop"),
            ),
        )
    )


def target_from(
    *, operation: OperationConfig, team_key: str, project: str, repo_url: str
) -> ScratchTarget:
    """Resolve the target from the operation, refusing before any tool call.

    Each refusal here is one a later act would have met anyway — mid-build for
    the team, at the first cross-off for the done state, at the first scoped
    read for the criterion label. Asking the configuration first costs nothing
    and names the member instead of a backend error.
    """
    if not project.strip():
        raise ScratchScopeRefusalError("the target project must be named")
    entry = operation.teams.get(team_key)
    if entry is None:
        raise ScratchScopeRefusalError(
            f"the operation declares no team {team_key!r}",
            foreign=tuple(sorted(operation.teams)),
        )
    matches = [repo for repo in operation.repos if repo.url == repo_url]
    if len(matches) != 1:
        raise ScratchScopeRefusalError(
            f"the operation declares no single repository {repo_url!r}",
            foreign=tuple(repo.url for repo in operation.repos),
        )
    label = operation.issue_labels.get("criterion", "").strip()
    if not label:
        raise ScratchScopeRefusalError(
            "the operation declares no issue_labels['criterion']"
        )
    done = operation.workflow_states.get(LifecycleStage.DONE, "").strip()
    if not done:
        raise ScratchScopeRefusalError(
            "the operation declares no workflow_states['done']"
        )
    return ScratchTarget(
        team=entry.name,
        project=project,
        repo_url=matches[0].url,
        criterion_label=label,
        done_state=done,
    )


def _row_title(row: Mapping[str, object]) -> str:
    return str(row.get("title", ""))


def _row_id(row: Mapping[str, object]) -> str:
    return str(row["id"])


def _row_parent(row: Mapping[str, object]) -> str | None:
    parent = row.get("parentId")
    return None if parent is None else str(parent)


def foreign_issues(
    *, plan: ScratchScopePlan, rows: Sequence[Mapping[str, object]]
) -> tuple[str, ...]:
    """Every listed issue this builder could not have created.

    Two kinds. A row whose title and parentage match no planned lane and no
    planned criterion of a matched lane is something else's; and a row sharing
    a planned title with another row is one of two the builder cannot tell
    apart, so neither is safely adoptable.
    """
    lane_by_title = {lane.title: lane for lane in plan.lanes}
    lane_by_id = {
        _row_id(row): lane_by_title[_row_title(row)]
        for row in rows
        if _row_parent(row) is None and _row_title(row) in lane_by_title
    }
    offenders: set[str] = set()
    for row in rows:
        title, parent = _row_title(row), _row_parent(row)
        if parent is None:
            known = title in lane_by_title
        else:
            lane = lane_by_id.get(parent)
            known = lane is not None and any(
                item.title == title for item in lane.criteria
            )
        if not known:
            offenders.add(_row_id(row))
    counted: dict[str, list[str]] = {}
    for row in rows:
        counted.setdefault(_row_title(row), []).append(_row_id(row))
    for keys in counted.values():
        if len(keys) > 1:
            offenders.update(keys)
    return tuple(sorted(offenders))


def declares_scratch(description: object) -> bool:
    """Whether the declaration stands as a line of its own, not inside prose."""
    text = description if isinstance(description, str) else ""
    return any(line.strip() == SCRATCH_DECLARATION for line in text.splitlines())


def refuse_live_board(
    *,
    project: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
    plan: ScratchScopePlan,
) -> None:
    """Refuse unless the project declared itself and holds nothing else."""
    if not declares_scratch(project.get("description")):
        raise ScratchScopeRefusalError(
            f"the project does not carry {SCRATCH_DECLARATION!r} as a line of its own"
        )
    foreign = foreign_issues(plan=plan, rows=rows)
    if foreign:
        raise ScratchScopeRefusalError(
            "the project holds issues this builder did not create",
            foreign=foreign[:NAMED_OFFENDERS],
        )


@dataclass
class _Board:
    """One reading of the target: the project payload and every issue in it."""

    project: Mapping[str, object]
    rows: tuple[Mapping[str, object], ...] = ()

    @property
    def project_id(self) -> str:
        return str(self.project["id"])

    def children_of(self, parent: str) -> Iterator[Mapping[str, object]]:
        return (row for row in self.rows if _row_parent(row) == parent)

    def root_titled(self, title: str) -> Mapping[str, object] | None:
        return next(
            (
                row
                for row in self.rows
                if _row_parent(row) is None and _row_title(row) == title
            ),
            None,
        )


class ScratchScopeBuilder:
    """Writes the recorded shape once, and nothing onto any other board."""

    def __init__(
        self,
        *,
        caller: McpToolCaller,
        target: ScratchTarget,
        plan: ScratchScopePlan,
    ) -> None:
        self._caller = caller
        self._target = target
        self._plan = plan

    async def build(self) -> BuiltScope:
        """Adopt what the board already holds and create exactly the rest."""
        board = await self._read_board()
        states = await self._states()
        created: list[str] = []
        adopted: list[str] = []
        lanes: dict[str, str] = {}
        criteria: dict[str, tuple[str, ...]] = {}
        for lane in self._plan.lanes:
            row = board.root_titled(lane.title)
            if row is None:
                key = await self._create_lane(
                    lane, project=board.project_id, state=states[UNSTARTED]
                )
                created.append(key)
            else:
                key = _row_id(row)
                adopted.append(key)
            lanes[lane.name] = key
            keys: list[str] = []
            children = {
                _row_title(child): _row_id(child) for child in board.children_of(key)
            }
            for item in lane.criteria:
                child = children.get(item.title)
                if child is None:
                    child = await self._create_criterion(
                        item,
                        parent=key,
                        project=board.project_id,
                        state=states[UNSTARTED],
                    )
                    created.append(child)
                else:
                    adopted.append(child)
                keys.append(child)
            criteria[lane.name] = tuple(keys)
        for lane in self._plan.lanes:
            for blocker in lane.blocked_by:
                await self._ensure_blocked_by(lanes[lane.name], blocker=lanes[blocker])
        return BuiltScope(
            project_id=board.project_id,
            lanes=lanes,
            criteria=criteria,
            created=tuple(created),
            adopted=tuple(adopted),
        )

    async def plant_cycle(self) -> None:
        """Make A wait on B while B still waits on A, so the plan has no root."""
        lanes = await self._lane_keys()
        await self._call(
            _TOOL_SAVE_ISSUE,
            {"id": lanes["A"], "blockedBy": [lanes["B"]]},
        )

    async def clear_cycle(self) -> None:
        """Take back only the edge the plant added; B keeps its own."""
        lanes = await self._lane_keys()
        await self._call(
            _TOOL_SAVE_ISSUE,
            {"id": lanes["A"], "removeBlockedBy": [lanes["B"]]},
        )

    async def plant_false_done(self, *, criterion_key: str, sha: str) -> None:
        """Put a criterion at Done behind an Evidence row that claims a test.

        Two saves, because this deployment never sends a body and a state in
        one save: the Evidence row first, then the state. The claim is what
        makes it false — a Done with no Evidence at all says nothing untrue.
        """
        board = await self._read_board()
        row = next(
            (item for item in board.rows if _row_id(item) == criterion_key), None
        )
        if row is None:
            raise ScratchScopeRefusalError(
                f"the project holds no criterion {criterion_key!r}"
            )
        states = await self._states()
        body = apply_evidence(
            body=str(row.get("description", "")),
            evidence=CriterionEvidence(
                graded_sha=sha, test="planted claim: no such test"
            ),
        )
        await self._call(_TOOL_SAVE_ISSUE, {"id": criterion_key, "description": body})
        await self._call(
            _TOOL_SAVE_ISSUE,
            {"id": criterion_key, "state": states[self._target.done_state]},
        )

    async def _lane_keys(self) -> Mapping[str, str]:
        board = await self._read_board()
        keys: dict[str, str] = {}
        for lane in self._plan.lanes:
            row = board.root_titled(lane.title)
            if row is None:
                raise ScratchScopeRefusalError(
                    f"the project holds no lane {lane.name!r}"
                )
            keys[lane.name] = _row_id(row)
        return keys

    async def _read_board(self) -> _Board:
        """The project and every issue in it, then the two refusals over both.

        Read again for every act, the plants included: what makes "never a live
        board" a mechanism is that no act stands on a listing an earlier one
        made.
        """
        payload = await self._call(_TOOL_GET_PROJECT, {"query": self._target.project})
        if not isinstance(payload, Mapping):
            raise ScratchScopeRefusalError("the project read answered no project")
        rows = await self._list_issues({"project": str(payload["id"])})
        refuse_live_board(project=payload, rows=rows, plan=self._plan)
        return _Board(project=payload, rows=rows)

    async def _list_issues(
        self, arguments: Mapping[str, object]
    ) -> tuple[Mapping[str, object], ...]:
        rows: list[Mapping[str, object]] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            request = {**arguments, "includeArchived": True, "limit": PAGE_LIMIT}
            if cursor is not None:
                request["cursor"] = cursor
            page = await self._call(_TOOL_LIST_ISSUES, request)
            if not isinstance(page, Mapping):
                raise ScratchScopeRefusalError("the issue listing answered no page")
            rows.extend(
                item for item in page.get("issues", []) if isinstance(item, Mapping)
            )
            if not page.get("hasNextPage"):
                return tuple(rows)
            cursor = page.get("cursor") if isinstance(page.get("cursor"), str) else None
            if cursor is None or cursor in seen:
                raise ScratchScopeRefusalError(
                    "the issue listing reports another page it cannot reach"
                )
            seen.add(cursor)

    async def _states(self) -> Mapping[str, str]:
        """The one unstarted state and the configured done state, by identity.

        Exactly one unstarted row, because a board offering two gives the
        builder a choice it has no basis to make and a walk a state it does not
        expect.
        """
        payload = await self._call(
            _TOOL_LIST_ISSUE_STATUSES, {"team": self._target.team}
        )
        if not isinstance(payload, Sequence) or isinstance(payload, str | bytes):
            raise ScratchScopeRefusalError("the status listing answered no rows")
        rows = [row for row in payload if isinstance(row, Mapping)]
        unstarted = [row for row in rows if str(row.get("type")) == UNSTARTED]
        if len(unstarted) != 1:
            raise ScratchScopeRefusalError(
                f"the team offers {len(unstarted)} unstarted workflow states"
            )
        done = [row for row in rows if str(row.get("name")) == self._target.done_state]
        if len(done) != 1:
            raise ScratchScopeRefusalError(
                f"the team does not offer {self._target.done_state!r} exactly once"
            )
        return {
            UNSTARTED: str(unstarted[0]["id"]),
            self._target.done_state: str(done[0]["id"]),
        }

    async def _create_lane(self, lane: LanePlan, *, project: str, state: str) -> str:
        """A lane issue carries no label at all: its stage markers are earned."""
        created = await self._call(
            _TOOL_SAVE_ISSUE,
            {
                "team": self._target.team,
                "project": project,
                "title": lane.title,
                "description": lane.body,
                "state": state,
            },
        )
        return _created_key(created)

    async def _create_criterion(
        self, item: CriterionPlan, *, parent: str, project: str, state: str
    ) -> str:
        """A criterion carries the structural label and an empty Evidence row."""
        created = await self._call(
            _TOOL_SAVE_ISSUE,
            {
                "team": self._target.team,
                "project": project,
                "parentId": parent,
                "title": item.title,
                "description": criterion_body(
                    parent_key=parent, check=item.check, do=item.do
                ),
                "labels": [self._target.criterion_label],
                "state": state,
            },
        )
        return _created_key(created)

    async def _ensure_blocked_by(self, key: str, *, blocker: str) -> None:
        """Add the one edge the plan names, or leave the one already there."""
        payload = await self._call(
            _TOOL_GET_ISSUE, {"id": key, "includeRelations": True}
        )
        if not isinstance(payload, Mapping):
            raise ScratchScopeRefusalError("the issue read answered no issue")
        # The relations object is keyed by relation kind, so the edge this
        # plan names is looked for in its own arm and in no other.
        relations = payload.get("relations")
        arm = relations.get("blockedBy", ()) if isinstance(relations, Mapping) else ()
        present = isinstance(arm, Sequence) and any(
            isinstance(edge, Mapping) and str(edge.get("id")) == blocker for edge in arm
        )
        if present:
            return
        await self._call(_TOOL_SAVE_ISSUE, {"id": key, "blockedBy": [blocker]})

    async def _call(
        self, name: str, arguments: Mapping[str, object]
    ) -> Mapping[str, object] | Sequence[object]:
        return await self._caller.call_tool(name=name, arguments=arguments)


def _created_key(payload: object) -> str:
    if not isinstance(payload, Mapping) or "id" not in payload:
        raise ScratchScopeRefusalError("the issue save answered no identity")
    return str(payload["id"])


@dataclass(frozen=True, slots=True)
class _Command:
    """One sub-command's name and the extra arguments it requires."""

    name: str
    extra: tuple[str, ...] = field(default=())


COMMANDS = (
    _Command("build"),
    _Command("plant-cycle"),
    _Command("clear-cycle"),
    _Command("plant-false-done", extra=("criterion", "sha")),
)


def _parser() -> argparse.ArgumentParser:
    """Every target member is required and none of them has a default.

    A default target is a board somebody gets pointed at by forgetting an
    argument, which is the one thing this tool must make impossible.
    """
    parser = argparse.ArgumentParser(prog="scratch_scope", description=__doc__)
    parser.add_argument("command", choices=[item.name for item in COMMANDS])
    parser.add_argument("--operation-config", required=True)
    parser.add_argument("--team", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--criterion")
    parser.add_argument("--sha")
    return parser


async def _run(arguments: argparse.Namespace) -> BuiltScope | None:
    operation = load_operation_config(Path(arguments.operation_config))
    target = target_from(
        operation=operation,
        team_key=arguments.team,
        project=arguments.project,
        repo_url=arguments.repo_url,
    )
    # The process environment only: a credential file is never read here.
    settings = AppConfig(_env_file=None).tracker
    if settings.token is None:
        raise ScratchScopeRefusalError("no tracker credential is configured")
    token = settings.token.get_secret_value()
    refuse_foreign_credential(backend=settings.backend, token=token)
    caller = make_mcp_tool_caller(settings=settings, token=token)
    await caller.probe()
    await caller.open()
    try:
        builder = ScratchScopeBuilder(
            caller=caller, target=target, plan=scratch_scope_plan()
        )
        match arguments.command:
            case "build":
                return await builder.build()
            case "plant-cycle":
                await builder.plant_cycle()
            case "clear-cycle":
                await builder.clear_cycle()
            case _:
                if not arguments.criterion or not arguments.sha:
                    raise ScratchScopeRefusalError(
                        "planting a claim requires --criterion and --sha"
                    )
                await builder.plant_false_done(
                    criterion_key=arguments.criterion, sha=arguments.sha
                )
        return None
    finally:
        await caller.close()


def main(argv: Sequence[str]) -> int:
    """Parse, run, and print what a walk of the built board is addressed by."""
    import asyncio

    arguments = _parser().parse_args(list(argv))
    built = asyncio.run(_run(arguments))
    if built is None:
        return 0
    for name, key in built.lanes.items():
        print(f"lane {name}: {key}")
        for criterion in built.criteria[name]:
            print(f"  criterion: {criterion}")
    print(
        json.dumps(
            {
                "scope": {"kind": "project", "key": built.project_id},
                "repoUrl": arguments.repo_url,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - the live entry point
    raise SystemExit(main(__import__("sys").argv[1:]))
