"""The scratch-scope builder writes the recorded shape once, and nothing else.

"Never point this at a live board" is asserted here as a mechanism rather than
read as a warning: every case below drives the real builder over a double and
observes what it refused to write.
"""

import ast
import inspect
from pathlib import Path

import pytest

from kodezart.composition.tracker import build_tracker
from kodezart.core.backoff import RetryPolicy
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import ScopeCycleError
from kodezart.domain.fire_spec import criterion_field_bodies
from kodezart.types.domain.operation import LifecycleStage, OperationConfig
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.tracker import TrackerBackend
from tests.tools import scratch_scope
from tests.tools.scratch_board import UNSTARTED_STATE, ScratchBoardServer
from tests.tools.scratch_scope import (
    SCRATCH_DECLARATION,
    ScratchScopeBuilder,
    ScratchScopeRefusalError,
    scratch_scope_plan,
    target_from,
)

TEAM_KEY = "primary"
TEAM_NAME = "Scratch board"
REPO_URL = "https://example.invalid/example-org/scratch-repo"
PROJECT_ID = "scratch-project-id"
PROJECT_NAME = "Scratch scope"
CRITERION_LABEL = "criterion"
APPROVED_LABEL = "scope:approved"
DONE_STATE = "Done"

MODULE = Path(scratch_scope.__file__)


def scratch_operation(**updates: object) -> OperationConfig:
    """An operation declaring exactly what the builder resolves its target from."""
    fields: dict[str, object] = {
        "operation_name": "scratch",
        "workspace": "scratch-workspace",
        "agent_identities": ["scratch-agent"],
        "teams": {TEAM_KEY: {"name": TEAM_NAME, "key": "SCR"}},
        "issue_labels": {
            "criterion": CRITERION_LABEL,
            "decision": "decision",
            "tracker": "tracker",
        },
        "scope_labels": {
            "triage": "scope:triage",
            "proposed": "scope:proposed",
            "approved": APPROVED_LABEL,
        },
        "workflow_states": {
            LifecycleStage.IN_PROGRESS: "In Progress",
            LifecycleStage.IN_REVIEW: "In Review",
            LifecycleStage.DONE: DONE_STATE,
        },
        "repos": [{"url": REPO_URL, "trunk": "main"}],
    }
    fields.update(updates)
    return OperationConfig.model_validate(fields)


def project_payload(*, description: str | None = None) -> dict[str, object]:
    return {
        "id": PROJECT_ID,
        "name": PROJECT_NAME,
        "description": (
            f"The scratch board a live walk runs against.\n{SCRATCH_DECLARATION}\n"
            if description is None
            else description
        ),
        "url": f"https://tracker.invalid/project/{PROJECT_ID}",
        "initiatives": [],
        "labels": [],
    }


def builder_over(server: ScratchBoardServer, operation: OperationConfig):
    return ScratchScopeBuilder(
        caller=server,
        target=target_from(
            operation=operation,
            team_key=TEAM_KEY,
            project=PROJECT_NAME,
            repo_url=REPO_URL,
        ),
        plan=scratch_scope_plan(),
    )


def board(**project: object) -> tuple[ScratchBoardServer, OperationConfig]:
    operation = scratch_operation()
    return (
        ScratchBoardServer(operation=operation, project=project_payload(**project)),
        operation,
    )


def writes(server: ScratchBoardServer) -> list[tuple[str, dict[str, object]]]:
    """Every call that changes the board, in the order it was made."""
    return [
        (name, dict(arguments))
        for name, arguments in server.calls
        if not name.startswith(("get_", "list_"))
    ]


async def built_board() -> tuple[ScratchBoardServer, OperationConfig, object]:
    server, operation = board()
    built = await builder_over(server, operation).build()
    return server, operation, built


async def test_build_writes_three_lanes_with_their_criteria_and_one_blocker_edge():
    """The recorded shape, read back off the board rather than off the plan."""
    server, _operation, built = await built_board()
    plan = scratch_scope_plan()
    assert list(built.lanes) == ["A", "B", "C"]
    assert [len(built.criteria[name]) for name in ("A", "B", "C")] == [3, 2, 3]
    for name, key in built.lanes.items():
        lane = server.issues[key]
        assert lane.project_id == PROJECT_ID
        assert lane.status == UNSTARTED_STATE
        assert lane.parent_id is None
        for criterion_key, item in zip(
            built.criteria[name], plan.lane(name).criteria, strict=True
        ):
            criterion = server.issues[criterion_key]
            assert criterion.parent_id == key
            assert criterion.status == UNSTARTED_STATE
            body = criterion.description
            assert criterion_field_bodies(body, field="Check") == (item.check,)
            assert criterion_field_bodies(body, field="Evidence") == ("",)
    # Exactly one edge, on B, pointing at A; nothing on A or C.
    assert server.issues[built.lanes["B"]].relations == [
        ("blockedBy", built.lanes["A"])
    ]
    assert server.issues[built.lanes["C"]].relations == []
    assert server.issues[built.lanes["A"]].relations == [("blocks", built.lanes["B"])]
    # The project itself is untouched: the approval act is not the builder's.
    assert server.projects[PROJECT_ID] == project_payload()


async def test_build_sends_no_label_but_the_criterion_label_on_sub_issues():
    """No approval label, no queue label, no label on a lane at all (KOD-752)."""
    server, _operation, built = await built_board()
    lane_saves = [
        arguments
        for name, arguments in writes(server)
        if name == "save_issue"
        and arguments.get("parentId") is None
        and "title" in arguments
    ]
    assert len(lane_saves) == 3
    assert not [
        arguments
        for arguments in lane_saves
        if arguments.get("labels") or arguments.get("addLabels")
    ]
    criterion_saves = [
        arguments
        for name, arguments in writes(server)
        if name == "save_issue" and arguments.get("parentId") is not None
    ]
    assert len(criterion_saves) == 8
    assert all(
        arguments["labels"] == [CRITERION_LABEL] for arguments in criterion_saves
    )
    assert not [name for name, _ in server.calls if "label" in name]
    assert all(not server.issues[key].labels for key in built.lanes.values())


def tool_names_in_source() -> set[str]:
    """Every tool the builder's own source can name, read off its syntax tree.

    Both halves: the imported ``_TOOL_*`` constants, and any bare string
    literal passed as a call's ``name``. A tool named some third way is a blind
    spot stated here rather than hidden.
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name.startswith("_TOOL_")
    }
    literals = {
        argument.value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and _call_target(call.func) in {"_call", "call_tool"}
        for argument in (*call.args, *(word.value for word in call.keywords))
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    }
    return {getattr(scratch_scope, name) for name in imported} | literals


def _call_target(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def test_no_tool_the_builder_names_can_label_a_project():
    """The five tools it speaks, and no sixth — a project label is not among them."""
    assert tool_names_in_source() == {
        "get_project",
        "list_issues",
        "get_issue",
        "save_issue",
        "list_issue_statuses",
    }


async def test_a_project_without_the_declaration_is_refused_before_any_write():
    server, operation = board(description="An ordinary project.")
    with pytest.raises(ScratchScopeRefusalError, match=SCRATCH_DECLARATION):
        await builder_over(server, operation).build()
    assert writes(server) == []


async def test_a_declaration_mentioned_in_prose_is_not_a_declaration():
    """A line that talks ABOUT the declaration is not the declaration."""
    server, operation = board(
        description=f"this is not a {SCRATCH_DECLARATION}\nand nor is this one\n"
    )
    with pytest.raises(ScratchScopeRefusalError, match=SCRATCH_DECLARATION):
        await builder_over(server, operation).build()
    assert writes(server) == []


async def seeded_with_foreign_issue() -> tuple[ScratchBoardServer, OperationConfig]:
    """A built board that somebody else then filed an issue into."""
    server, operation = board()
    await builder_over(server, operation).build()
    from tests.fakes import FakeMcpIssue

    server.issues["FOREIGN-1"] = FakeMcpIssue(
        id="FOREIGN-1",
        title="Somebody else's work",
        team=TEAM_NAME,
        project_id=PROJECT_ID,
    )
    return server, operation


async def test_a_project_holding_an_issue_the_builder_did_not_create_is_refused():
    """The foreign row sits past the first listing page and is still found."""
    server, operation = await seeded_with_foreign_issue()
    mark = len(writes(server))
    with pytest.raises(ScratchScopeRefusalError) as caught:
        await builder_over(server, operation).build()
    assert caught.value.foreign == ("FOREIGN-1",)
    assert writes(server)[mark:] == []


#: Every act the builder exposes, read off the class so a plant added later is
#: covered by the case below without this list being edited.
PLANTS = tuple(
    name
    for name, member in inspect.getmembers(
        ScratchScopeBuilder, inspect.iscoroutinefunction
    )
    if not name.startswith("_")
)


def test_every_public_act_is_named_by_the_plant_case():
    assert set(PLANTS) >= {"build", "plant_cycle", "clear_cycle", "plant_false_done"}


@pytest.mark.parametrize("act", PLANTS)
async def test_every_plant_is_refused_on_a_project_with_a_foreign_issue(act: str):
    """Every act reads the board again first, so none of them inherits a verdict."""
    server, operation = await seeded_with_foreign_issue()
    builder = builder_over(server, operation)
    mark = len(writes(server))
    arguments = (
        {"criterion_key": "whatever", "sha": "a" * 40}
        if act == "plant_false_done"
        else {}
    )
    with pytest.raises(ScratchScopeRefusalError):
        await getattr(builder, act)(**arguments)
    assert writes(server)[mark:] == []


def test_the_command_line_has_no_default_target():
    """A target nobody typed is a board somebody gets pointed at by accident."""
    parser = scratch_scope._parser()
    required = {
        action.dest
        for action in parser._actions
        if action.required and action.option_strings
    }
    assert required == {"operation_config", "team", "project", "repo_url"}
    assert all(
        action.default is None
        for action in parser._actions
        if action.option_strings and action.dest != "help"
    )
    with pytest.raises(SystemExit):
        parser.parse_args(["build"])


async def test_a_listing_that_cannot_be_exhausted_is_refused():
    """A page reporting a successor it names no cursor for is not a whole board.

    A builder that took such a page for the whole project would read a board
    with most of its rows missing — and then decide, from that reading, that
    the project holds nothing it did not create.
    """
    server, operation, _built = await built_board()
    server.omit_cursor = True
    mark = len(writes(server))
    with pytest.raises(ScratchScopeRefusalError, match="another page"):
        await builder_over(server, operation).build()
    assert writes(server)[mark:] == []


@pytest.mark.parametrize(
    ("team_key", "repo_url"),
    [(TEAM_KEY, "https://example.invalid/other"), ("undeclared", REPO_URL)],
)
async def test_an_undeclared_team_or_repository_is_refused_before_any_tool_call(
    team_key: str, repo_url: str
):
    server, operation = board()
    with pytest.raises(ScratchScopeRefusalError):
        target_from(
            operation=operation,
            team_key=team_key,
            project=PROJECT_NAME,
            repo_url=repo_url,
        )
    assert server.calls == []


async def test_a_second_build_adopts_everything_and_writes_nothing():
    server, operation, first = await built_board()
    mark = len(writes(server))
    second = await builder_over(server, operation).build()
    assert writes(server)[mark:] == []
    assert second.created == ()
    assert set(second.adopted) == set(first.created) - {
        key for key in first.created if " blockedBy " in key
    }
    assert second.lanes == first.lanes
    assert second.criteria == first.criteria


async def test_a_partly_built_board_is_completed_not_duplicated():
    """A run that died between two writes is finished by the next one."""
    server, operation = board()
    builder = builder_over(server, operation)
    states = await builder._states()
    plan = scratch_scope_plan()
    lane = plan.lane("A")
    key = await builder._create_lane(
        lane, project=PROJECT_ID, state=states["unstarted"]
    )
    await builder._create_criterion(
        lane.criteria[0], parent=key, project=PROJECT_ID, state=states["unstarted"]
    )
    built = await builder_over(server, operation).build()
    assert built.lanes["A"] == key
    assert len(built.criteria["A"]) == 3
    assert built.criteria["A"][0] in built.adopted
    assert len(server.issues) == 3 + 8


def tracker_over(server: ScratchBoardServer, operation: OperationConfig):
    tracker, _ledger = build_tracker(
        backend=TrackerBackend.LINEAR,
        retry=RetryPolicy(attempts=1, initial_delay=0.0),
        operation=operation,
        caller=server,
    )
    return tracker


async def test_the_planted_cycle_is_refused_by_the_topology_plan():
    """The plant is a real invalid state, read through the shipped adapter."""
    server, operation, built = await built_board()
    # The approval a person gives, given here so the lanes are candidates at
    # all: an unapproved scope reports no topology to be cyclic.
    server.projects[PROJECT_ID]["labels"].append(APPROVED_LABEL)
    builder = builder_over(server, operation)
    await builder.plant_cycle()
    tracker = tracker_over(server, operation)
    ref = ScopeRef(kind=ScopeKind.PROJECT, key=PROJECT_ID)
    with pytest.raises(ScopeCycleError):
        await scratch_scope_ready(tracker, ref)
    await builder.clear_cycle()
    ready = await scratch_scope_ready(tracker, ref)
    # B keeps the edge the plant never touched, so it is blocked and nothing else.
    assert {row.issue_key for row in ready.blocked} == {built.lanes["B"]}
    assert {row.issue.issue_key for row in ready.ready} == {
        built.lanes["A"],
        built.lanes["C"],
    }


async def scratch_scope_ready(tracker, ref):
    from kodezart.chains.scope_walker import read_scope_ready

    return await read_scope_ready(ref=ref, tracker=tracker)


async def test_the_planted_claim_is_a_done_criterion_whose_evidence_parses():
    """A false CLAIM: a Done criterion whose Evidence names a test nobody ran."""
    server, operation, built = await built_board()
    key = built.criteria["C"][0]
    before = server.issues[key].description
    sha = "b" * 40
    mark = len(writes(server))
    await builder_over(server, operation).plant_false_done(criterion_key=key, sha=sha)
    criterion = server.issues[key]
    assert criterion.status == DONE_STATE
    assert criterion.status_type == "completed"
    evidence = parse_criterion_evidence(criterion.description)
    assert evidence.graded_sha == sha
    # Every byte outside the Evidence row is the body the build wrote.
    assert criterion_field_bodies(
        criterion.description, field="Check"
    ) == criterion_field_bodies(before, field="Check")
    assert criterion_field_bodies(
        criterion.description, field="Do"
    ) == criterion_field_bodies(before, field="Do")
    # Two saves, because a body and a state never travel together here.
    planted = writes(server)[mark:]
    assert [name for name, _ in planted] == ["save_issue", "save_issue"]
    assert set(planted[0][1]) == {"id", "description"}
    assert set(planted[1][1]) == {"id", "state"}
