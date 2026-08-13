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
    TrackerBootValidationError,
    TrackerEnsureConflictError,
)
from kodezart.core.protocols import ManagedMcpToolCaller
from kodezart.main import create_app, lifespan
from kodezart.services.pass_scheduler import PassScheduler
from tests.fakes import ManagedFakeLinearMcpServer
from tests.tracker.conftest import (
    APPROVER,
    BYSTANDER,
    DOCUMENT_KEY,
    DOCUMENT_TITLE,
    QUEUE_STATE_LABELS,
    fixture_server,
)

TOKEN = "fixture-tracker-token"

#: A cadence no default would produce, and long enough that no pass fires
#: inside a test: what is asserted is the wiring of the knob, not a tick.
UNUSUAL_INTERVAL = 607.0


def _operation_toml(
    *,
    approver: str = APPROVER,
    queue_states: dict[str, str] | None = None,
    document_title: str = DOCUMENT_TITLE,
    document_id: str | None = DOCUMENT_KEY,
) -> str:
    """An operation config naming the fixture workspace's own entities.

    ``document_id`` at ``None`` is the fresh-workspace shape: the operation
    names the checkpoint document and boot adopts whatever id the workspace
    assigns it.
    """
    labels = dict(QUEUE_STATE_LABELS if queue_states is None else queue_states)
    rendered = "\n".join(f'{name} = "{label}"' for name, label in labels.items())
    declared_id = "" if document_id is None else f'\nid = "{document_id}"'
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

[teams.engineering]
name = "fixture-team"
key = "ENG"

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
name = "{document_title}"{declared_id}

[records.run_log]
system = "knowledge"
name = "Run log"
id = "record-1"
append_only = true

[knowledge]

[endpoints]
"""


@pytest.fixture
def server() -> ManagedFakeLinearMcpServer:
    """A managed fake MCP server carrying the shared fixture workspace."""
    source = fixture_server()
    managed = ManagedFakeLinearMcpServer()
    managed.issues = source.issues
    managed.documents = source.documents
    managed.history = source.history
    managed.users = source.users
    managed.teams = source.teams
    managed.labels = source.labels
    managed.label_containers = source.label_containers
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

    assert "queue:terminal" in wired.labels
    assert wired.tool_calls("create_issue_label") == [
        {"name": "queue:terminal", "teamId": "fixture-team"},
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

    assert wired.tool_calls("save_document") == [{"title": title}]
    assert wired.documents[adopted].title == title
    reconciled = [
        event
        for event in _events(capsys.readouterr().out)
        if event.get("event") == "tracker_mappings_reconciled"
    ]
    assert (
        f"document '{title}' -> (assigned by the workspace)" in reconciled[0]["created"]
    )


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


async def test_a_label_the_workspace_holds_elsewhere_aborts_boot_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wired: ManagedFakeLinearMcpServer,
) -> None:
    """AC-4 / R7(c): an ensure that would ALTER a definition never writes.

    The workspace already holds the declared label, but in another team's
    container.  Adopting it would repurpose a value that team means
    something by, and creating it would be the cross-container write R3
    forbids — so boot aborts naming the entry instead.
    """
    contested = "queue:terminal"
    wired.labels.append(contested)
    wired.label_containers[contested] = "some-other-team"
    _configure(
        monkeypatch,
        tmp_path,
        _operation_toml(queue_states={**QUEUE_STATE_LABELS, "done": contested}),
    )

    with pytest.raises(TrackerEnsureConflictError) as caught:
        async with lifespan(create_app()):
            pass

    assert contested in caught.value.entry
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
        "KODEZART_TRACKER_SCHEDULER_PASS_INTERVAL_SECONDS",
        str(UNUSUAL_INTERVAL),
    )
    _configure(monkeypatch, tmp_path, _operation_toml())
    app = create_app()
    assert app.state.config.tracker_scheduler_pass_interval_seconds == UNUSUAL_INTERVAL

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
