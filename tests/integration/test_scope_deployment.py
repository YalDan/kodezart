"""The shipped scope operation file, walked and booted as an operator has it.

`tests/integration/test_scope_runtime.py` is about what a walk does; this
module is about whether the file a person copies can be walked at all. Every
member the walk needs and the file lacks shows up here as a lane failure, which
is how the file's contents are settled rather than guessed.
"""

import ast
import asyncio
import json
import re
from pathlib import Path

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.chains.scope_walker import read_scope_ready
from kodezart.composition.tracker import criteria_stage_label_key
from kodezart.config.organize import OrganizeSettings
from kodezart.domain.criterion_evidence import parse_criterion_evidence
from kodezart.domain.errors import ScopeNotApprovedError, WorkspaceError
from kodezart.domain.run_alarm_record import MARKER_PURPOSE
from kodezart.main import create_app, lifespan
from kodezart.services.scope_approval import scope_approved
from kodezart.services.tracker_boot import owned_mappings
from kodezart.types.domain.branch import trunk_base
from kodezart.types.domain.operation import LifecycleStage
from kodezart.types.domain.organize import split_label_key
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
from tests.services.test_prompt_passes import HEARTBEAT_PASS
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

    The board is labelled with the criteria-stage KEY the file's own mandate
    table names and answers under the file's own marker prefixes, and both are
    asserted below before the walk so neither is a constant this module chose.
    What the key MAPS to is not exercised here — the port compares the key
    itself — and that mapping is pinned through the real adapter by
    `tests/tools/test_scratch_scope.py::test_the_shipped_scope_config_answers_each_adapter_point_of_need`.
    A purpose the walk resolves and the file does not declare would be contained
    at the lane boundary and named in `failed_lanes`, which is why that
    assertion comes first — it is how this file's marker list was settled rather
    than guessed.

    Over the forge-less origin the module's other walks use, because the shared
    forge double answers for one hardcoded address and pointing a shipped
    example at that address would be a worse file. Nothing this case is about
    is decided by the origin: the labels, the markers and the states are.
    """
    loaded = shipped()
    repos = WalkRepos()
    port = board(lanes=("A",), operation=loaded)
    # What the board actually took from the file, rather than what the helper is
    # believed to take: a board labelled with this module's own fallback key, or
    # a port answering under some other operation's prefixes, would walk green
    # and say nothing about the shipped file.
    assert criteria_stage_label_key(loaded) in port.issues["A"].issue_labels
    assert port.marker_prefixes == loaded.marker_prefixes
    harness = resumable(
        repos=repos,
        port=port,
        operation=loaded,
        origin=ORIGIN,
        # The file declares run stages, so the run's entry builds their owner
        # and the owner needs its bounds. The lane already carries every stage
        # marker, so each stage is complete on arrival and opens no session —
        # what this case is about starts at the walk.
        organize=OrganizeSettings(max_admission_rounds=2, max_convergence_rounds=2),
    )
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
        criteria_stage_label_key(loaded)
        == split_label_key(row.spec.terminal_marker_key)[1]
    )


def test_the_shipped_file_declares_no_table_the_scope_path_never_reads() -> None:
    """The header's claim about the two absent tables, asserted rather than read.

    Both fields default to an empty mapping, so a table added to the file would
    load, boot and walk while the header above it said there was none.
    """
    loaded = shipped()
    assert loaded.run_event_states == {}
    assert loaded.queue_states == {}


#: Every purpose a marker prefix can be asked for is named in the source by one
#: of three shapes. The floor below keeps an empty derivation from making the
#: subset assertion say nothing.
PURPOSE_FLOOR: frozenset[str] = frozenset(
    {"run_state", "run_event", "ruling", "amendment"}
)

SRC = Path(__file__).resolve().parents[2] / "src"


def _called_name(node: ast.expr) -> str:
    """The bare name a call's target ends in: ``self._prefix`` is ``_prefix``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _string(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def marker_purposes_read_under_src() -> set[str]:
    """Every marker purpose the source can ask for, off the syntax tree.

    Three shapes, because the code asks in three ways: a `purpose=` keyword on
    any call; the sole positional argument of a call whose function name ends in
    `_prefix`; and a module-level name ending in `_PURPOSE`.

    A purpose named some fourth way is a blind spot stated here rather than
    hidden. It would make this guard accept a declared member nothing reads,
    which is the direction that costs a reader a false promise and not a run.
    """
    found: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "purpose" and (value := _string(keyword.value)):
                    found.add(value)
            if (
                _called_name(node.func).endswith("_prefix")
                and len(node.args) == 1
                and not node.keywords
                and (value := _string(node.args[0]))
            ):
                found.add(value)
        for statement in tree.body:
            if not isinstance(statement, ast.Assign):
                continue
            value = _string(statement.value)
            if value is None:
                continue
            if any(
                isinstance(target, ast.Name) and target.id.endswith("_PURPOSE")
                for target in statement.targets
            ):
                found.add(value)
    return found


def test_every_marker_purpose_the_shipped_file_declares_is_one_the_code_reads() -> None:
    """A declared purpose nothing asks for is a member the header promises is used.

    A subset guard on purpose. The other direction — every purpose the walk
    needs is declared — is not derivable from the source, because which
    purposes a given deployment reaches depends on what it schedules; the walk
    test above is what settles that, by failing the lane.
    """
    derived = marker_purposes_read_under_src()
    assert PURPOSE_FLOOR <= derived, sorted(PURPOSE_FLOOR - derived)
    declared = set(shipped().marker_prefixes)
    assert declared <= derived, sorted(declared - derived)


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

#: The three the page cannot print a usable value for. Every other variable the
#: block carries is used exactly as printed.
SUBSTITUTED: frozenset[str] = frozenset(
    {
        "KODEZART_TRACKER__TOKEN",
        "KODEZART_GITHUB_TOKEN",
        "KODEZART_OPERATION_CONFIG",
    }
)


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
    # Anchored on `export` and matched to the end of its line, because a
    # placeholder like `<the tracker credential>` is not one word and a
    # whitespace-bounded capture would take `<the` for the value.
    printed = dict(
        re.findall(r"^export (KODEZART_[A-Z0-9_]+)=(.+)$", block[1], flags=re.MULTILINE)
    )
    # Substituted, never added: each of the three has to be a name the page
    # itself prints, or this helper would configure a deployment the page never
    # told an operator about.
    assert SUBSTITUTED <= set(printed), sorted(SUBSTITUTED - set(printed))
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
    block boots, reconciles its mappings into the team, schedules the passes that
    read its one scope table — the observation tick that watches each lane's run
    shape, the organize tick and the standing scopes' heartbeat — and nothing
    else, holds no checkpointer, and writes no label onto any issue. Its first
    scoped run is refused by type before a member is read, because nobody has
    approved the project yet, and it leaves the board untouched.

    Then the approval label is applied — by this test, standing for the person
    whose act it is — and the same run is admitted: it passes the approval
    question and reaches for the repository its organize stages author
    against, which the file's example remote does not answer. What the
    admitted run would take its first tick from is read here through the
    deployment's own dialled adapter: the two root lanes ready with the third
    held by its live blocker. Nothing here waits for a fire, and no session is
    opened at all.
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
    # What the builder built, before anything is asserted about it: the lane-set
    # assertions below are all set comparisons, and an empty plan would satisfy
    # every one of them.
    assert set(built.lanes) == {"A", "B", "C"}
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
            "supervisor",
            PromptKey.GROOMING_PASS.value,
            HEARTBEAT_PASS,
        ]
        # The observation tick records each lane's alarm under a configured
        # prefix and refuses that lane by name without one, so a file that
        # schedules the tick and declares no prefix is a file that stops at its
        # first observed lane.
        assert MARKER_PURPOSE in loaded.marker_prefixes
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
        # Before the label there is nothing to observe: the addressed scope
        # carries no approval, so the run is refused at its entry rather than
        # walked and reported empty one lane at a time.
        scope = loaded.organize_scopes[0].scope
        with pytest.raises(ScopeNotApprovedError) as refused:
            _ = await first_observation(app, loaded)
        assert refused.value.ref == scope
        assert not [
            name
            for name, _ in server.calls[mark:]
            if name in {"save_issue", "save_comment"}
        ]

        # The one human act, performed here because no agent may perform it.
        project["labels"].append(loaded.scope_labels["approved"])
        # The label is what admits the run. The entry's own question now says
        # yes, so the next thing the run asks for is the repository its
        # organize stages author against — which this deployment's example
        # remote does not answer, and which is as far as a case substituting
        # nothing but the transport can drive it.
        assert await scope_approved(ref=scope, tracker=app.state.tracker)
        with pytest.raises(WorkspaceError):
            _ = await first_observation(app, loaded)

        # The reading the admitted run takes its first tick from, read through
        # this deployment's own dialled adapter: the two root lanes ready and
        # the third held by its live blocker, off the board the builder built.
        reading = await read_scope_ready(ref=scope, tracker=app.state.tracker)
        assert reading.unapproved == ()
        assert {lane.issue.issue_key for lane in reading.ready} == {
            built.lanes["A"],
            built.lanes["C"],
        }
        assert {
            blocked.issue_key: blocked.blocker_keys for blocked in reading.blocked
        } == {built.lanes["B"]: (built.lanes["A"],)}
