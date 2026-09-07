"""M0 — the bare deployment: no tracker, no operation, the endpoint serves.

The tracker layer is an optional capability of the service, never a
prerequisite of the core endpoint (KOD-160).  This drives the REAL
``lifespan`` with nothing dialled and asserts the whole of what "bare"
promises in one place: the process boots, the HTTP surface serves a run
from submission to terminal answer, no pass is scheduled, and every
absence is a named logged state rather than a silent one.

The run is shaped to refuse in the WORKSPACE step, and that shape is the
whole reason this is a CI act.  What is being established is that the
surface serves bare — accepts, queues, executes and answers — not that a
run succeeds; a run that got past the workspace would open a real engine
session, which the gate excludes by design.  The live half, a full agent
query answered through this same bare boot, is recorded evidence on the
tracker (the 2026-08-31 paired engine probe).
"""

import asyncio
import json
import os

import httpx
import pytest

from kodezart.main import create_app, lifespan
from kodezart.types.domain.job import JobState
from kodezart.types.domain.outcome import WorkflowOutcome

#: A forge-less origin with no repository behind it.  The workspace step
#: refuses on the clone, before the executor is reached, so the fire
#: travels the whole surface without a session ever being opened.
UNRESOLVABLE_ORIGIN = "file:///nonexistent/kodezart-bare-mode-no-such-repo.git"

#: How long the queued run may take to reach TERMINAL through the HTTP
#: surface.  Generous against a loaded machine and finite against a hang.
TERMINAL_TIMEOUT = 30.0


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


def _named(events: list[dict[str, object]], name: str) -> list[dict[str, object]]:
    return [event for event in events if event.get("event") == name]


async def _terminal_status(
    client: httpx.AsyncClient,
    job_id: str,
) -> dict[str, object]:
    """The status endpoint's body once it reports *job_id* TERMINAL.

    Read through HTTP rather than off the registry: what this proves is
    that the endpoint answers for a job the bare service accepted, so
    reaching into the queue for the same fact would prove something else.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TERMINAL_TIMEOUT
    while True:
        response = await client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        assert isinstance(payload, dict)
        if payload["state"] == JobState.TERMINAL.value:
            return payload
        if loop.time() >= deadline:
            msg = f"job {job_id} never reached TERMINAL"
            raise AssertionError(msg)
        await asyncio.sleep(0)


async def test_the_bare_boot_serves_a_run_and_names_every_absence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Health, a fire, its terminal answer — and four absences, all named."""
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

            fired = await client.post(
                "/api/v1/agent/fire",
                json={"prompt": "serve this bare", "repoUrl": UNRESOLVABLE_ORIGIN},
            )
            assert fired.status_code == 202
            accepted = fired.json()
            job_id = accepted["jobId"]
            assert accepted["state"] == JobState.QUEUED.value
            assert accepted["statusUrl"] == f"/api/v1/jobs/{job_id}"

            status = await _terminal_status(client, job_id)
            assert status["jobId"] == job_id
            # The run this deployment could execute: it went through the
            # real queue, failed where its origin made it fail, and the
            # endpoint says so rather than reporting a null fate.
            assert status["outcome"] == WorkflowOutcome.engine_error.value

        # The agent surface is mounted, not gated on the tracker: the
        # bare service is the original one-shot endpoint, whole.
        paths = {getattr(route, "path", "") for route in app.routes}
        assert "/api/v1/agent/query" in paths
        assert "/api/v1/agent/workflow" in paths
        assert "/api/v1/agent/fire" in paths

    events = _events(capsys.readouterr().out)
    tracker_absent = _named(events, "tracker_not_configured")
    assert len(tracker_absent) == 1
    assert tracker_absent[0]["operation_config_present"] is False
    assert tracker_absent[0]["tracker_token_present"] is False

    passes_absent = _named(events, "scheduled_passes_not_wired")
    assert len(passes_absent) == 1
    # The forge's absence is named here too, as the flag the wiring gate
    # actually reads — three presences, none inferred from an empty
    # schedule.
    assert passes_absent[0]["tracker_present"] is False
    assert passes_absent[0]["operation_config_present"] is False
    assert passes_absent[0]["delivery_probe_present"] is False

    prompt_passes_absent = _named(events, "prompt_passes_not_wired")
    assert len(prompt_passes_absent) == 1
    assert prompt_passes_absent[0]["operation_config_present"] is False

    assert len(_named(events, "knowledge_capability_unconfigured")) == 1

    # The constraint this run is shaped by, asserted rather than assumed:
    # the refusal happened in the workspace step, so no engine session was
    # opened anywhere in it.
    assert _named(events, "agent_service_workspace_acquire_failed")
