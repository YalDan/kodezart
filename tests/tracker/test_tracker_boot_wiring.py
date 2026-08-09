"""The composition root dials the tracker and reconciles it before serving.

Every test here drives the REAL ``lifespan`` — the shipped adapter over the
in-process fake MCP server, the shipped boot service, the shipped ordering.
The only thing substituted is the transport factory, which is the one seam
that would otherwise need a live workspace.  Delete the wiring from
``main`` and every assertion below fails.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from kodezart.adapters.linear_mcp_tracker import LinearMcpTracker
from kodezart.core.config import AppConfig
from kodezart.core.errors import TrackerBootValidationError
from kodezart.core.protocols import ManagedMcpToolCaller
from kodezart.main import create_app, lifespan
from tests.fakes import ManagedFakeLinearMcpServer
from tests.tracker.conftest import (
    APPROVER,
    BYSTANDER,
    QUEUE_STATE_LABELS,
    fixture_server,
)

TOKEN = "fixture-tracker-token"


def _operation_toml(
    *,
    approver: str = APPROVER,
    queue_states: dict[str, str] | None = None,
) -> str:
    """An operation config naming the fixture workspace's own entities."""
    labels = dict(QUEUE_STATE_LABELS if queue_states is None else queue_states)
    rendered = "\n".join(f'{name} = "{label}"' for name, label in labels.items())
    return f"""
operation_name = "fixture"
workspace = "fixture-workspace"
agent_identities = []
initiatives = []

[[principals]]
tracker_user = "{approver}"
role = "approver"
handle = "@approver"

[[principals]]
tracker_user = "{BYSTANDER}"
role = "principal"
handle = "@bystander"

[teams]
engineering = "fixture-team"

[queue_states]
{rendered}

[workflow_states]
in_progress = "In Progress"
in_review = "In Review"
done = "Done"

[[repos]]
url = "https://example.invalid/repo"

[[repos.check_commands]]
name = "check"
command = "make check"

[documents.checkpoint]
system = "tracker"
id = "doc-1"

[records.run_log]
system = "knowledge"
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

    monkeypatch.setattr("kodezart.main.make_mcp_tool_caller", factory)
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
    assert caught.value.unresolved == ("user 'approver' -> 'ghost'",)
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
