"""A fake workspace seeded from one operation file, holding one project.

The shared fake serves the whole tool contract but answers its issue listing
without a project filter and without a cursor, which is exactly the half a
project scope and a re-entrant builder depend on. This subclass supplies both,
and seeds teams, statuses, labels and the dialled account FROM the loaded
operation, so a case here reads the same names the adapter is built with rather
than a second copy of them.
"""

from collections.abc import Mapping, Sequence

from kodezart.types.domain.operation import LifecycleStage, OperationConfig
from tests.fakes import ManagedFakeLinearMcpServer

#: How many issues one listing page carries. Two, so a board of this size needs
#: several pages and a builder that stopped after the first would be visible.
PAGE_SIZE = 2

#: The backlog and unstarted rows every seeded team offers beside the
#: operation's own workflow states, and the kind each of them is.
BACKLOG_STATE = "Backlog"
UNSTARTED_STATE = "Todo"

_STAGE_TYPE: Mapping[LifecycleStage, str] = {
    LifecycleStage.IN_PROGRESS: "started",
    LifecycleStage.IN_REVIEW: "started",
    LifecycleStage.DONE: "completed",
}


def _statuses(operation: OperationConfig) -> tuple[list[str], dict[str, str]]:
    """The vocabulary one seeded team offers, and each row's kind.

    Backlog and Todo first, then the operation's own states in stage order. A
    second unstarted row is not seeded: the builder refuses a team that offers
    two, having no basis to choose between them.
    """
    names = [BACKLOG_STATE, UNSTARTED_STATE]
    kinds = {BACKLOG_STATE: "backlog", UNSTARTED_STATE: "unstarted"}
    for stage, kind in _STAGE_TYPE.items():
        name = operation.workflow_states.get(stage)
        if name is not None and name not in kinds:
            names.append(name)
            kinds[name] = kind
    return names, kinds


class ScratchBoardServer(ManagedFakeLinearMcpServer):
    """The fake workspace one scratch project lives in."""

    def __init__(
        self, *, operation: OperationConfig, project: Mapping[str, object]
    ) -> None:
        team = next(iter(operation.teams.values())).name
        names, kinds = _statuses(operation)
        actor = operation.agent_identities[0] if operation.agent_identities else "agent"
        super().__init__(
            # Every declared identity, not only the dialled one: boot resolves
            # each spelling of the writer against the workspace.
            users=list(operation.agent_identities) or [actor],
            teams=[team],
            statuses={team: names},
            state_types=kinds,
            actor=actor,
        )
        #: Both keys, because ``get_project`` looks up the query as given and a
        #: builder addresses the project by name while the scope reader
        #: addresses it by id. One payload behind both, so a label the test
        #: adds is seen through either.
        self.projects = {
            str(project["id"]): project,
            str(project["name"]): project,
        }
        #: Fault knob: report another page and name no cursor to reach it.
        self.omit_cursor = False

    def _tool_list_issues(
        self, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        """Filter by project, parent and label, and page by an offset cursor."""
        label = arguments.get("label")
        team = arguments.get("team")
        parent = arguments.get("parentId")
        project = arguments.get("project")
        selected = [
            issue
            for issue in self.issues.values()
            if (label is None or label in issue.labels)
            and (team is None or issue.team == team)
            and (parent is None or issue.parent_id == parent)
            and (project is None or issue.project_id == project)
        ]
        return self._page([issue.entry() for issue in selected], arguments)

    def _page(
        self, rows: Sequence[Mapping[str, object]], arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        cursor = arguments.get("cursor")
        start = 0 if cursor is None else int(str(cursor).removeprefix("offset:"))
        stop = start + PAGE_SIZE
        page: dict[str, object] = {
            "issues": list(rows[start:stop]),
            "hasNextPage": stop < len(rows),
        }
        if stop < len(rows) and not self.omit_cursor:
            page["cursor"] = f"offset:{stop}"
        return page
