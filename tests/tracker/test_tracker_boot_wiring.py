"""The composition root dials the tracker, reconciles it, and schedules.

Every test here drives the REAL ``lifespan`` — the shipped adapter over the
in-process fake MCP server, the shipped boot service, the shipped ordering.
The only thing substituted is the transport factory, which is the one seam
that would otherwise need a live workspace.  Delete the wiring from
``main`` and every assertion below fails.
"""

import ast
import inspect
import json
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.composition.prompts import boot_prompts
from kodezart.core.config import AppConfig
from kodezart.core.errors import (
    PassKnowledgeCapabilityError,
    TrackerBootValidationError,
    TrackerEnsureConflictError,
)
from kodezart.core.protocols import ManagedMcpToolCaller
from kodezart.main import create_app, lifespan
from kodezart.services.pass_scheduler import PassScheduler
from tests.fakes import FakeMcpDocument, ManagedFakeLinearMcpServer
from tests.tracker.conftest import (
    APPROVER,
    BYSTANDER,
    DOCUMENT_KEY,
    DOCUMENT_TITLE,
    FOREIGN_TEAM,
    QUEUE_STATE_LABELS,
    fixture_server,
)

TOKEN = "fixture-tracker-token"

#: A cadence no default would produce, and long enough that no pass fires
#: inside a test: what is asserted is the wiring of the knob, not a tick.
UNUSUAL_INTERVAL = 607.0

#: The tracker-side run-log destination the fixture workspace holds.  A
#: record is EXTERNAL — its id is declared, never adopted — so the workspace
#: has to carry a document under it for a boot over this config to resolve.
RUN_LOG_KEY = "run-log-1"
RUN_LOG_TITLE = "Run log"

#: The single board every case here runs on unless it needs two.
ONE_TEAM: dict[str, str] = {"engineering": "fixture-team"}

#: Two boards of the fixture workspace, both declared.  An operation
#: declaring several teams needs its queue vocabulary on EACH of them —
#: a queue member lives inside a team on this backend — so the
#: reconciliation resolves the vocabulary once per board (KOD-167).
TWO_TEAMS: dict[str, str] = {**ONE_TEAM, "platform": FOREIGN_TEAM}


def _operation_toml(
    *,
    approver: str = APPROVER,
    queue_states: dict[str, str] | None = None,
    teams: dict[str, str] | None = None,
    document_title: str = DOCUMENT_TITLE,
    document_id: str | None = DOCUMENT_KEY,
    document_container: str | None = "engineering",
    record_id: str = RUN_LOG_KEY,
    knowledge: dict[str, str] | None = None,
) -> str:
    """An operation config naming the fixture workspace's own entities.

    ``document_id`` at ``None`` is the fresh-workspace shape: the operation
    names the checkpoint document and boot adopts whatever id the workspace
    assigns it.  ``document_container`` is the declared team creation files
    it under (KOD-166); ``None`` is the undeclared shape the refusal case
    boots with.

    ``record_id`` is the tracker-side run log's, which the operation
    declares rather than adopts: a record destination is EXTERNAL, so boot
    RESOLVES the id it names and refuses on one the workspace does not hold.

    ``teams`` maps the operation's own key for a board to the name the
    workspace holds it under, which is how many boards this operation
    dispatches from — and therefore whether its queue vocabulary is
    scoped to one team or to the workspace.

    ``knowledge`` is the what-lives-where map. Declaring one under a
    deployment that grants no session the knowledge store is a preflight
    refusal, which is how the case below reaches one.
    """
    knowledge_map = "\n".join(
        f'{key} = "{title}"' for key, title in (knowledge or {}).items()
    )
    labels = dict(QUEUE_STATE_LABELS if queue_states is None else queue_states)
    rendered = "\n".join(f'{name} = "{label}"' for name, label in labels.items())
    boards = "\n".join(
        f'[teams.{key}]\nname = "{name}"\nkey = "{key[:3].upper()}"'
        for key, name in (ONE_TEAM if teams is None else teams).items()
    )
    declared_id = "" if document_id is None else f'\nid = "{document_id}"'
    declared_container = (
        "" if document_container is None else f'\ncontainer = "{document_container}"'
    )
    return f"""
operation_name = "fixture"
workspace = "fixture-workspace"
agent_identities = []
initiatives = []

[[principals]]
tracker_user = "{approver}"
roles = ["approver", "principal", "assignee"]
handle = "@approver"

[[principals]]
tracker_user = "{BYSTANDER}"
roles = ["principal"]
handle = "@bystander"

{boards}

[queue_states]
{rendered}

[workflow_states]
in_progress = "In Progress"
in_review = "In Review"
done = "Done"

[[repos]]
url = "https://example.invalid/repo"
trunk = "main"

[[repos.checks]]
name = "check"
command = "make check"

[documents.checkpoint]
system = "tracker"
name = "{document_title}"{declared_id}{declared_container}

[records.fire_prep]
system = "tracker"
name = "{RUN_LOG_TITLE}"
id = "{record_id}"
append_only = true

[knowledge]
{knowledge_map}

[endpoints]
"""


@pytest.fixture
def server() -> ManagedFakeLinearMcpServer:
    """A managed fake MCP server carrying the shared fixture workspace.

    Plus the run-log document, which lives here rather than in the shared
    fixture because only the composition-root cases declare a record
    destination: the conformance suite is about the port, and a document
    added there would be one every adapter had to answer for.
    """
    source = fixture_server()
    managed = ManagedFakeLinearMcpServer()
    managed.issues = source.issues
    managed.documents = {
        **source.documents,
        RUN_LOG_KEY: FakeMcpDocument(
            id=RUN_LOG_KEY,
            title=RUN_LOG_TITLE,
            content="one row per pass",
        ),
    }
    managed.users = source.users
    managed.teams = source.teams
    managed.labels = source.labels
    managed.team_labels = source.team_labels
    managed.statuses = source.statuses
    managed.state_types = source.state_types
    managed.actor = source.actor
    return managed


@pytest.fixture
def wired(
    monkeypatch: pytest.MonkeyPatch,
    server: ManagedFakeLinearMcpServer,
) -> Iterator[ManagedFakeLinearMcpServer]:
    """Substitute ONLY the transport factory; everything else is production."""

    def factory(*, config: AppConfig, token: str) -> ManagedMcpToolCaller:
        assert token == TOKEN
        return server

    monkeypatch.setattr("kodezart.composition.tracker.make_mcp_tool_caller", factory)
    yield server


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    body: str,
    *,
    token: str | None = TOKEN,
) -> None:
    path = tmp_path / "operation.toml"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setenv("KODEZART_OPERATION_CONFIG", str(path))
    if token is not None:
        monkeypatch.setenv("KODEZART_TRACKER_TOKEN", token)


def _events(captured: str) -> list[dict[str, object]]:
    return [
        json.loads(line.strip())
        for line in captured.splitlines()
        if line.strip().startswith("{")
    ]


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _dispatch_entries(scheduler: PassScheduler) -> list[tuple[str, float]]:
    """``(name, interval)`` for the DISPATCH passes the scheduler carries.

    The two cases below are about the dispatch half — one pass per
    repository, on the dispatch interval, and none of them without a
    delivery probe — so each reads the dispatch entries rather than the
    whole schedule, which also carries the prompt passes (KOD-60 R25).
    """
    return [
        (entry.name, entry.interval_seconds)
        for entry in scheduler.passes
        if entry.name.startswith("dispatch:")
    ]


def _first_call_line(name: str, *, inside: object = lifespan) -> int:
    """Where *name* is first called inside *inside*, the shipped function."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(inside)))
    return next(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node.func) == name
    )


def test_the_prompt_registry_is_bound_after_the_tracker_is_reconciled() -> None:
    """KOD-57 R9.4's ordering, held against the source that has to carry it.

    Every other case here observes an OUTCOME, and the ordering has none
    that a test can see: a registry bound to the declared copy renders a
    placeholder only when a pass ticks, which is one interval away from
    boot and off every test's path. So this reads the composition root
    itself. Moving the dial back below the registry load reddens it, which
    is the only thing that would.

    The registry load now lives in ``boot_prompts``, so the ordering is
    read in two hops rather than one: the root dials before it loads, and
    the load is what binds. Asserting only the first hop would let the
    binding move out from under the ordering it depends on.
    """
    assert _first_call_line("boot_tracker") < _first_call_line("boot_prompts")
    assert _first_call_line("bindings_for", inside=boot_prompts) > 0


async def test_boot_wires_the_tracker_and_owns_its_session_lifetime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """AC-4: a reconcilable config leaves a live port on the app."""
    _configure(monkeypatch, tmp_path, _operation_toml())
    app = create_app()
    async with lifespan(app):
        assert isinstance(app.state.tracker, LinearMcpTracker)
        assert wired.opens == 1
        assert wired.closes == 0
    assert wired.closes == 1


async def test_one_unresolvable_principal_aborts_boot_naming_that_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """AC-4: the process refuses to serve rather than mis-target a write."""
    _configure(monkeypatch, tmp_path, _operation_toml(approver="ghost"))
    app = create_app()
    with pytest.raises(TrackerBootValidationError) as caught:
        async with lifespan(app):
            pass
    assert caught.value.unresolved == ("user 'approver+assignee+principal' -> 'ghost'",)
    # The session opened for the check does not leak past the failure.
    assert wired.closes == 1


async def test_an_absent_queue_label_is_created_at_boot_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """The config INSTATES what the operation owns: the measured failure, closed."""
    declared = {**QUEUE_STATE_LABELS, "done": "queue:terminal"}
    assert "queue:terminal" not in wired.labels
    _configure(monkeypatch, tmp_path, _operation_toml(queue_states=declared))
    app = create_app()
    async with lifespan(app):
        pass

    # Made inside the declared team, which is where a queue member lives on
    # this backend — never at workspace level beside it.
    assert wired.team_labels["fixture-team-id"] == ["queue:terminal"]
    assert "queue:terminal" not in wired.labels
    # `teamId` takes the team's UUID and nothing else — the live server
    # answers a name with "teamId must be a UUID" and a 400 (KOD-143). The
    # adapter resolves the declared team NAME through the teams listing.
    assert wired.tool_calls("create_issue_label") == [
        {"name": "queue:terminal", "teamId": "fixture-team-id"},
    ]
    reconciled = [
        event
        for event in _events(capsys.readouterr().out)
        if event.get("event") == "tracker_mappings_reconciled"
    ]
    assert len(reconciled) == 1
    assert reconciled[0]["created"] == ["queue_state 'done' -> 'queue:terminal'"]


async def test_a_second_boot_over_the_same_workspace_adopts_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """Creation is idempotent: an established workspace is adopted unchanged."""
    declared = {**QUEUE_STATE_LABELS, "done": "queue:terminal"}
    _configure(monkeypatch, tmp_path, _operation_toml(queue_states=declared))
    async with lifespan(create_app()):
        pass
    capsys.readouterr()
    async with lifespan(create_app()):
        pass

    assert len(wired.tool_calls("create_issue_label")) == 1
    reconciled = [
        event
        for event in _events(capsys.readouterr().out)
        if event.get("event") == "tracker_mappings_reconciled"
    ]
    assert reconciled[0]["created"] == []
    assert "queue_state 'done' -> 'queue:terminal'" in reconciled[0]["adopted"]


async def test_a_declared_document_the_workspace_lacks_is_created_at_boot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """AC-4 arm (b): the read-side document half, and the manual step it removes.

    The operation names its checkpoint document and declares no id, which
    is the only shape a FRESH workspace can be configured with — a
    server-assigned id cannot be written down before the document exists.
    Boot creates it, adopts the id it is given, and writes that id back
    into the config every later consumer reads.
    """
    title = "Fire run checkpoint"
    assert title not in {document.title for document in wired.documents.values()}
    _configure(
        monkeypatch,
        tmp_path,
        _operation_toml(document_title=title, document_id=None),
    )
    app = create_app()
    async with lifespan(app):
        adopted = app.state.operation_config.documents["checkpoint"].id

    assert wired.tool_calls("save_document") == [
        {"title": title, "team": "fixture-team"}
    ]
    assert wired.documents[adopted].title == title
    reconciled = [
        event
        for event in _events(capsys.readouterr().out)
        if event.get("event") == "tracker_mappings_reconciled"
    ]
    assert (
        f"document '{title}' -> (assigned by the workspace)" in reconciled[0]["created"]
    )


async def test_an_id_less_document_with_no_container_refuses_boot_naming_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """Creation with nowhere to file the document aborts BEFORE the vendor.

    The live backend refuses a container-less create outright (KOD-166),
    so boot refuses on the declaration gap itself — naming what to declare
    — rather than letting the transport retry a deterministic refusal.
    Nothing is written.
    """
    title = "Fire run checkpoint"
    _configure(
        monkeypatch,
        tmp_path,
        _operation_toml(
            document_title=title,
            document_id=None,
            document_container=None,
        ),
    )

    with pytest.raises(TrackerEnsureConflictError) as caught:
        async with lifespan(create_app()):
            pass

    assert "container" in str(caught.value)
    assert wired.tool_calls("save_document") == []


async def test_a_document_the_workspace_already_carries_is_adopted_not_duplicated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """A second boot over an established workspace writes no document at all."""
    _configure(monkeypatch, tmp_path, _operation_toml(document_id=None))
    app = create_app()
    async with lifespan(app):
        adopted = app.state.operation_config.documents["checkpoint"].id

    assert adopted == DOCUMENT_KEY
    assert wired.tool_calls("save_document") == []


async def test_a_declared_document_id_the_workspace_lacks_aborts_boot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """A pinned id that resolves to nothing is named, never quietly replaced."""
    _configure(monkeypatch, tmp_path, _operation_toml(document_id="ghost-document"))

    with pytest.raises(TrackerEnsureConflictError) as caught:
        async with lifespan(create_app()):
            pass

    assert "ghost-document" in caught.value.entry
    assert wired.tool_calls("save_document") == []
    assert wired.closes == 1


async def test_two_boards_each_holding_the_whole_vocabulary_boot_and_adopt_both(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """KOD-167: the measured boot failure, as a boot that succeeds.

    Two declared teams, each carrying its OWN team-scoped copy of every
    queue member and no workspace-level copy anywhere — the shape a live
    two-team operation is in, where the second board's labels belong to
    another delivery loop and cannot be moved.  Reconciliation used to read
    one vocabulary for the whole operation, find the member defined in two
    containers it had not declared, and abort with
    ``declared None, found 'Duckburg', 'kodezart'``.

    Resolving per board adopts both copies and writes nothing.
    """
    wired.labels.clear()
    wired.team_labels = {
        f"{team}-id": list(QUEUE_STATE_LABELS.values())
        for team in ("fixture-team", FOREIGN_TEAM)
    }
    _configure(monkeypatch, tmp_path, _operation_toml(teams=TWO_TEAMS))

    async with lifespan(create_app()):
        pass

    assert wired.tool_calls("create_issue_label") == []
    reconciled = [
        event
        for event in _events(capsys.readouterr().out)
        if event.get("event") == "tracker_mappings_reconciled"
    ]
    adopted = reconciled[0]["adopted"]
    assert isinstance(adopted, list)
    # Once per declared board: the adoption records what each board holds,
    # so one entry per member would mean a board went unread.
    assert adopted.count("queue_state 'done' -> 'queue:done'") == len(TWO_TEAMS)


async def test_a_board_missing_one_member_is_given_its_own_and_the_other_stands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """The instate half of KOD-167, scoped to the board that lacks it.

    The other board's copy is neither moved nor adopted on this board's
    behalf: it is that loop's definition of the member, and this board gets
    one of its own inside its own container.
    """
    wired.labels.clear()
    present = [label for label in QUEUE_STATE_LABELS.values() if label != "queue:done"]
    wired.team_labels = {
        "fixture-team-id": present,
        f"{FOREIGN_TEAM}-id": list(QUEUE_STATE_LABELS.values()),
    }
    _configure(monkeypatch, tmp_path, _operation_toml(teams=TWO_TEAMS))

    async with lifespan(create_app()):
        pass

    assert wired.tool_calls("create_issue_label") == [
        {"name": "queue:done", "teamId": "fixture-team-id"},
    ]
    assert "queue:done" in wired.team_labels["fixture-team-id"]
    assert wired.team_labels[f"{FOREIGN_TEAM}-id"] == list(QUEUE_STATE_LABELS.values())


async def test_the_live_mixed_board_shape_boots_and_adopts_everything(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """The workspace as measured 2026-09-01, whole: mixed levels, two boards.

    Most of the vocabulary is ONE workspace-level label that both boards'
    listings echo, and one member (``done``) exists as a separate copy per
    board.  Both facts are live and they coexist, so a classification that
    can only express one of them refuses a healthy workspace: reading each
    board's listing whole made the echoed members look board-owned and
    aborted on the approval label.

    Nothing here is written — every declared member already resolves on
    every declared board, by one route or the other.
    """
    wired.labels = [
        label for label in QUEUE_STATE_LABELS.values() if label != "queue:done"
    ]
    wired.team_labels = {
        f"{team}-id": ["queue:done"] for team in ("fixture-team", FOREIGN_TEAM)
    }
    _configure(monkeypatch, tmp_path, _operation_toml(teams=TWO_TEAMS))

    async with lifespan(create_app()):
        pass

    assert wired.tool_calls("create_issue_label") == []


async def test_a_label_defined_at_both_levels_aborts_boot_naming_both(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """The surviving refusal: a definition at each level is undecidable.

    A workspace-level label serves every board and a team's own label
    serves that board, so a member defined at BOTH levels has two
    definitions and nothing says which one a write on that board resolves
    to.  Boot names every container it found and writes nothing rather than
    picking one (KOD-167).

    The board's copy is its OWN — a second label under a second id, not
    the workspace's echoed into its listing.  That distinction is the
    whole difference between this case and the healthy one above, and it
    is decided by id: by name the two are indistinguishable.
    """
    contested = QUEUE_STATE_LABELS["done"]
    wired.team_labels = {"fixture-team-id": [contested]}
    _configure(monkeypatch, tmp_path, _operation_toml())

    with pytest.raises(TrackerEnsureConflictError) as caught:
        async with lifespan(create_app()):
            pass

    assert contested in caught.value.entry
    assert "workspace" in str(caught.value)
    assert "fixture-team" in str(caught.value)
    assert wired.tool_calls("create_issue_label") == []
    assert wired.closes == 1


async def test_two_declared_queue_states_claiming_one_label_abort_boot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """The second raise site: the config contradicts itself before any write."""
    declared = {**QUEUE_STATE_LABELS, "decision": QUEUE_STATE_LABELS["done"]}
    _configure(monkeypatch, tmp_path, _operation_toml(queue_states=declared))

    with pytest.raises(TrackerEnsureConflictError) as caught:
        async with lifespan(create_app()):
            pass

    assert QUEUE_STATE_LABELS["done"] in caught.value.entry
    assert wired.tool_calls("create_issue_label") == []


async def test_without_a_credential_no_tracker_is_wired_and_boot_says_so(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """Three states, none silent: the absent half is named, never inferred."""
    monkeypatch.delenv("KODEZART_TRACKER_TOKEN", raising=False)
    _configure(monkeypatch, tmp_path, _operation_toml(), token=None)
    app = create_app()
    async with lifespan(app):
        assert app.state.tracker is None

    unconfigured = [
        event
        for event in _events(capsys.readouterr().out)
        if event.get("event") == "tracker_not_configured"
    ]
    assert len(unconfigured) == 1
    assert unconfigured[0]["operation_config_present"] is True
    assert unconfigured[0]["tracker_token_present"] is False
    assert wired.opens == 0


async def test_boot_starts_a_scheduler_carrying_one_dispatch_pass_per_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """AC-20: the scheduler is constructed, driven and stopped by the root.

    The interval asserted here is a value no default would produce, so the
    assertion is about the knob's consumer and not about a coincidence.
    """
    monkeypatch.setenv("KODEZART_GITHUB_TOKEN", "fixture-forge-token")
    monkeypatch.setenv(
        "KODEZART_DISPATCH_PASS_INTERVAL_SECONDS",
        str(UNUSUAL_INTERVAL),
    )
    _configure(monkeypatch, tmp_path, _operation_toml())
    app = create_app()
    assert app.state.config.dispatch_pass_interval_seconds == UNUSUAL_INTERVAL

    async with lifespan(app):
        scheduler: PassScheduler = app.state.pass_scheduler
        assert scheduler.running
        assert _dispatch_entries(scheduler) == [
            ("dispatch:https://example.invalid/repo", UNUSUAL_INTERVAL),
        ]
    assert not scheduler.running


async def test_without_a_delivery_probe_no_pass_is_scheduled_and_boot_says_so(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """Three states, none silent: an unwired half is named, not inferred."""
    monkeypatch.delenv("KODEZART_GITHUB_TOKEN", raising=False)
    _configure(monkeypatch, tmp_path, _operation_toml())
    app = create_app()
    async with lifespan(app):
        assert _dispatch_entries(app.state.pass_scheduler) == []

    unwired = [
        event
        for event in _events(capsys.readouterr().out)
        if event.get("event") == "scheduled_passes_not_wired"
    ]
    assert len(unwired) == 1
    assert unwired[0]["tracker_present"] is True
    assert unwired[0]["delivery_probe_present"] is False


async def test_a_preflight_refusal_strands_no_queue_and_no_open_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """KOD-164: a refusal costs the boot it refuses and nothing else.

    The scheduled passes' boot checks used to fire from inside the dispatch
    wiring, which the root reaches only after it has started the job queue
    and opened the tracker's MCP transport — so a refusal aborted the
    lifespan with a running worker pool and a live vendor session, neither
    of which the shutdown path was ever going to reach. Hoisted ahead of
    both, the refusal leaves no queue on the app at all and hands the
    transport back on the way out.

    The refusal itself is the ordinary one: an operation declaring a
    knowledge map under a deployment that grants that store to no session.
    """
    _configure(
        monkeypatch,
        tmp_path,
        _operation_toml(knowledge={"constitution": "Operating Constitution"}),
    )
    app = create_app()

    with pytest.raises(PassKnowledgeCapabilityError) as caught:
        async with lifespan(app):
            pass

    assert "knowledge.constitution" in str(caught.value)
    assert wired.closes == 1
    assert not hasattr(app.state, "job_queue")
    assert not hasattr(app.state, "pass_scheduler")
