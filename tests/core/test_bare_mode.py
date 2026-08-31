"""M0 — the bare deployment: no tracker, no operation, the endpoint serves.

The tracker layer is an optional capability of the service, never a
prerequisite of the core endpoint (KOD-160).  This drives the REAL
``lifespan`` with nothing dialled and asserts the whole of what "bare"
promises in one place: the process boots, the HTTP surface serves, no
pass is scheduled, and every absence is a named logged state rather
than a silent one.

The live half — a full agent query answered through this same bare
boot — is recorded evidence on the tracker (the 2026-08-31 paired
engine probe), deliberately not a CI act: a real query spawns a real
engine session, which the gate excludes by design.
"""

import json
import os

import httpx
import pytest

from kodezart.main import create_app, lifespan


def _bare(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every ``KODEZART_`` variable the invoking shell carries.

    Bare means the shipped defaults and nothing else — a developer's
    ambient tracker credential would silently turn this into a wired
    boot and the assertions below into claims about the wrong mode.
    """
    for name in [key for key in os.environ if key.startswith("KODEZART_")]:
        monkeypatch.delenv(name)


def _events(captured: str) -> list[dict[str, object]]:
    return [
        json.loads(line.strip())
        for line in captured.splitlines()
        if line.strip().startswith("{")
    ]


async def test_the_bare_boot_serves_and_names_every_absence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Health answers, job status refuses by name, and nothing is silent."""
    _bare(monkeypatch)
    app = create_app()
    async with lifespan(app):
        assert app.state.tracker is None
        assert app.state.operation_config is None

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://bare",
        ) as client:
            health = await client.get("/api/v1/health")
            assert health.status_code == 200
            assert health.json()["data"]["healthy"] is True

            missing = await client.get("/api/v1/jobs/job-does-not-exist")
            assert missing.status_code == 404

        # The agent surface is mounted, not gated on the tracker: the
        # bare service is the original one-shot endpoint, whole.
        paths = {getattr(route, "path", "") for route in app.routes}
        assert "/api/v1/agent/query" in paths
        assert "/api/v1/agent/workflow" in paths
        assert "/api/v1/agent/fire" in paths

    events = _events(capsys.readouterr().out)
    tracker_absent = [
        event for event in events if event.get("event") == "tracker_not_configured"
    ]
    assert len(tracker_absent) == 1
    assert tracker_absent[0]["operation_config_present"] is False
    assert tracker_absent[0]["tracker_token_present"] is False

    passes_absent = [
        event for event in events if event.get("event") == "scheduled_passes_not_wired"
    ]
    assert len(passes_absent) == 1
    # The forge's absence is named here too, as the flag the wiring gate
    # actually reads — three presences, none inferred from an empty
    # schedule.
    assert passes_absent[0]["tracker_present"] is False
    assert passes_absent[0]["operation_config_present"] is False
    assert passes_absent[0]["delivery_probe_present"] is False
