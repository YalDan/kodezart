"""The shipped scope operation file, walked and booted as an operator has it.

`tests/integration/test_scope_runtime.py` is about what a walk does; this
module is about whether the file a person copies can be walked at all. Every
member the walk needs and the file lacks shows up here as a lane failure, which
is how the file's contents are settled rather than guessed.
"""

import asyncio
import json
import re
from pathlib import Path

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.tracker import criteria_stage_label_key
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.main import create_app, lifespan
from kodezart.services.tracker_boot import owned_mappings
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.dispatch import ExclusionClause
from kodezart.types.domain.operation import LifecycleStage
from kodezart.types.domain.prompts import PromptKey
from kodezart.types.domain.scope_runtime import ScopeWalkEvent
from kodezart.types.domain.session import PermissionMode
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.integration.test_scope_runtime import (
    ORIGIN,
    WalkRepos,
    board,
    bounded_walk,
    lane_failures,
    lane_record,
    resumable,
)
from tests.tools.scratch_board import ScratchBoardServer
from tests.tools.scratch_scope import (
    SCRATCH_DECLARATION,
    ScratchScopeBuilder,
    scratch_scope_plan,
    target_from,
)

#: The file a scope operator copies. Loaded rather than restated: a test
#: written against its own copy of these names would pass over a file nobody
#: could boot.
SCOPE_EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "operation.scope.toml"


def shipped():
    return load_operation_config(SCOPE_EXAMPLE)


async def test_the_shipped_scope_config_walks_one_lane_to_a_crossed_off_criterion():
    """One lane, from the shipped file's own names, all the way to Done.

    The board is labelled with the criteria-stage key the file's own mandate
    table names and answers under the file's own marker prefixes, so nothing
    here stands on a constant this module chose. A purpose the walk resolves
    and the file does not declare would be contained at the lane boundary and
    named in `failed_lanes`, which is why that assertion comes first — it is
    how this file's marker list was settled rather than guessed.

    Over the forge-less origin the module's other walks use, because the shared
    forge double answers for one hardcoded address and pointing a shipped
    example at that address would be a worse file. Nothing this case is about
    is decided by the origin: the labels, the markers and the states are.
    """
    loaded = shipped()
    repos = WalkRepos()
    port = board(lanes=("A",), operation=loaded)
    harness = resumable(repos=repos, port=port, operation=loaded, origin=ORIGIN)
    events = await bounded_walk(harness, origin=ORIGIN)
    assert lane_failures(events) == ()
    criterion = port.issues["A/check"]
    assert criterion.state_kind is WorkflowStateKind.COMPLETED
    assert criterion.state_name == LifecycleStage.DONE.value
    # The cross-off carries the sha the lane's own branch stands at, read back
    # through the codec rather than off the prose.
    record = await lane_record(port, "A", operation=loaded)
    assert parse_criterion_evidence(criterion.body).graded_sha == record.head_sha
    # The record was written under the prefix the SHIPPED file declares, which
    # is what a second process would go looking for.
    assert record.branch
    assert [
        comment.body
        for comment in port.comments
        if comment.issue_key == "A"
        and comment.body.startswith(f"[{loaded.marker_prefixes['run_state']}:")
    ]


def test_the_shipped_file_names_the_criteria_stage_the_adapter_is_built_with():
    """One answer, from the builder's own function: a lane fires on this label."""
    loaded = shipped()
    row = next(
        row
        for row in loaded.resolve_organize_mandates()
        if row.role.marks_execution_stage
    )
    assert (
        criteria_stage_label_key(loaded) == row.spec.terminal_marker_key.split(".")[1]
    )


# ---------------------------------------------------------------------------
# The acceptance preamble, in process: the real lifespan, booted from the
# shipped file and the page's own environment block, over a board the scratch
# builder built.
# ---------------------------------------------------------------------------

#: A credential in the shape boot accepts, assembled by concatenation so no
#: literal here has the shape of a real one.
TOKEN = "lin_api_" + "0" * 40
FORGE_TOKEN = "fixture-forge-token"

#: The seconds a scoped read is allowed before this case fails rather than
#: hanging a whole run. Orders above what these reads take.
READ_BOUND_SECONDS = 60

#: The one fenced `bash` block of the page an operator follows.
GUIDE = Path(__file__).resolve().parents[2] / "docs" / "running-a-scope.md"


def guide_environment() -> dict[str, str]:
    """The page's own environment, with only the three secrets substituted.

    Read out of the page rather than restated here: a case that set its own
    variables would boot a deployment nobody was told how to configure. The two
    credentials and the config path take fixture values because a page cannot
    print a real one; every other value is used exactly as printed.
    """
    block = re.search(
        r"```bash\n(.*?)```", GUIDE.read_text(encoding="utf-8"), flags=re.DOTALL
    )
    assert block is not None
    printed = dict(re.findall(r"(KODEZART_[A-Z0-9_]+)=(\S+)", block[1]))
    assert len(printed) >= 6
    return {
        **printed,
        "KODEZART_TRACKER__TOKEN": TOKEN,
        "KODEZART_GITHUB_TOKEN": FORGE_TOKEN,
        "KODEZART_OPERATION_CONFIG": str(SCOPE_EXAMPLE),
    }


def scratch_project(loaded) -> dict[str, object]:
    key = loaded.organize_scopes[0].scope.key
    return {
        "id": key,
        "name": "Scratch scope",
        "description": (
            f"The board this deployment walks.\n{SCRATCH_DECLARATION}\n"
            f"Nothing here is a record."
        ),
        "url": f"https://tracker.invalid/project/{key}",
        "initiatives": [],
        "labels": [],
    }


def scoped_run(app, loaded):
    return app.state.workflow_engine.run(
        prompt="",
        repo_path=None,
        repo_url=loaded.repos[0].url,
        base_spec=trunk_base("unused-request-default"),
        scope=loaded.organize_scopes[0].scope,
        permission_mode=PermissionMode.UNATTENDED,
        allowed_tools=[],
        cache_key="acceptance",
    )


async def first_observation(app, loaded):
    """The first walk observation of one run, and nothing after it."""
    stream = scoped_run(app, loaded)
    try:
        async with asyncio.timeout(READ_BOUND_SECONDS):
            async for event in stream:
                if isinstance(event, ScopeWalkEvent):
                    return event.observation
    finally:
        await stream.aclose()
    raise AssertionError("the walk reported no observation")


def logged(captured: str, name: str) -> list[dict[str, object]]:
    return [
        event
        for line in captured.splitlines()
        if line.strip().startswith("{")
        for event in [json.loads(line.strip())]
        if event.get("event") == name
    ]


async def test_a_scope_deployment_boots_from_the_shipped_files_and_fires_nothing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The acceptance preamble, with nothing substituted but the transport.

    A deployment configured from the shipped file and the page's own environment
    block boots, reconciles its mappings into the team, schedules the organize
    tick and nothing else, holds no checkpointer, and writes no label onto any
    issue. Its first scoped observation reports all three lanes unapproved with
    nothing dispatched and leaves the board untouched.

    Then the approval label is applied — by this test, standing for the person
    whose act it is — and the next observation reports the two root lanes ready
    with the third excluded under its live blocker. Nothing here waits for a
    fire: the lanes carry no criteria-stage marker at this head, so a fire's
    refusal would be a lane failure the walk survives.
    """
    loaded = shipped()
    project = scratch_project(loaded)
    server = ScratchBoardServer(operation=loaded, project=project)
    built = await ScratchScopeBuilder(
        caller=server,
        target=target_from(
            operation=loaded,
            team_key=next(iter(loaded.teams)),
            project=str(project["name"]),
            repo_url=loaded.repos[0].url,
        ),
        plan=scratch_scope_plan(),
    ).build()
    labels_before = {key: list(issue.labels) for key, issue in server.issues.items()}
    payload_before = json.dumps(project, sort_keys=True)

    for name, value in guide_environment().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        "kodezart.composition.tracker.make_mcp_tool_caller",
        lambda **_: server,
    )

    app = create_app()
    async with lifespan(app):
        events = capsys.readouterr().out
        reconciled = logged(events, "tracker_mappings_reconciled")
        assert len(reconciled) == 1
        assert reconciled[0]["backend"] == "linear"
        assert set(reconciled[0]["created"]) == {
            ref.describe() for ref in owned_mappings(loaded)
        }
        assert [entry.name for entry in app.state.pass_scheduler.passes] == [
            PromptKey.GROOMING_PASS.value
        ]
        assert app.state.checkpointer is None
        for name in ("scheduled_passes_not_wired", "prompt_passes_not_wired"):
            withheld = logged(events, name)
            assert len(withheld) == 1, name
            assert withheld[0]["organize_scopes_declared"] is True, name
        # Boot instates the vocabulary in the TEAM; it labels no issue and does
        # not touch the project it is about to be asked to walk.
        assert {
            key: list(issue.labels) for key, issue in server.issues.items()
        } == labels_before
        assert json.dumps(project, sort_keys=True) == payload_before

        mark = len(server.calls)
        unapproved = await first_observation(app, loaded)
        assert set(unapproved.unapproved_lanes) == set(built.lanes.values())
        assert unapproved.dispatched == ()
        assert unapproved.ready == ()
        assert not [
            name
            for name, _ in server.calls[mark:]
            if name in {"save_issue", "save_comment"}
        ]

        # The one human act, performed here because no agent may perform it.
        project["labels"].append(loaded.scope_labels["approved"])
        approved = await first_observation(app, loaded)
        assert approved.unapproved_lanes == ()
        assert set(approved.ready) == {built.lanes["A"], built.lanes["C"]}
        assert {
            item.issue_key: item.detail
            for item in approved.exclusions
            if item.clause is ExclusionClause.LIVE_BLOCKER
        } == {built.lanes["B"]: built.lanes["A"]}
