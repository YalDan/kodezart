"""Queue settings preserve supported sources and drive the composed queue."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from kodezart.composition.jobs import build_job_queue
from kodezart.core.config import AppConfig
from kodezart.domain.errors import QueueFullError
from kodezart.types.domain.agent import AssistantTextEvent
from tests.api.v1.test_jobs import (
    ChattyWorkflowEngine,
    GatedWorkflowEngine,
    _drain,
    _request,
    _until,
    _wait_terminal,
    _wait_truncated,
)
from tests.core.test_retired_config import _from_source

FIELDS = {
    "max_concurrent_runs_per_lane": 2,
    "max_depth_per_lane": 3,
    "terminal_retention_seconds": 120.0,
    "event_buffer_retention_seconds": 60.0,
    "event_buffer_capacity": 4,
}


@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
def test_nested_queue_settings_load_from_each_supported_source(
    source, tmp_path, monkeypatch
):
    if source == "init":
        config = AppConfig(_env_file=None, queue=FIELDS)
    elif source == "env":
        for field, value in FIELDS.items():
            monkeypatch.setenv("KODEZART_QUEUE__" + field.upper(), str(value))
        config = AppConfig(_env_file=None)
    elif source == "dotenv":
        path = tmp_path / ".env"
        path.write_text(
            "\n".join(
                f"KODEZART_QUEUE__{field.upper()}={value}"
                for field, value in FIELDS.items()
            )
        )
        config = AppConfig(_env_file=path)
    else:
        (tmp_path / "KODEZART_QUEUE").write_text(json.dumps(FIELDS))
        config = AppConfig(_env_file=None, _secrets_dir=tmp_path)
    assert config.queue.model_dump() == FIELDS


def test_queue_sources_keep_standard_precedence(tmp_path, monkeypatch):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "KODEZART_QUEUE").write_text(json.dumps(FIELDS))
    dotenv = tmp_path / ".env"
    dotenv.write_text("KODEZART_QUEUE__MAX_DEPTH_PER_LANE=5\n")
    monkeypatch.setenv("KODEZART_QUEUE__MAX_DEPTH_PER_LANE", "6")
    kwargs = {"_env_file": dotenv, "_secrets_dir": secrets}
    assert (
        AppConfig(**kwargs, queue={"max_depth_per_lane": 7}).queue.max_depth_per_lane
        == 7
    )
    assert AppConfig(**kwargs).queue.max_depth_per_lane == 6
    monkeypatch.delenv("KODEZART_QUEUE__MAX_DEPTH_PER_LANE")
    assert AppConfig(**kwargs).queue.max_depth_per_lane == 5
    assert AppConfig(_env_file=None, _secrets_dir=secrets).queue.model_dump() == FIELDS


@pytest.mark.parametrize("field", list(FIELDS))
@pytest.mark.parametrize("source", ["init", "env", "dotenv", "secret"])
def test_old_flat_queue_names_refuse(source, field, tmp_path, monkeypatch):
    with pytest.raises(ValidationError, match="Extra inputs") as caught:
        _from_source(source, "queue_" + field, "900", tmp_path, monkeypatch)
    assert "queue_" + field in str(caught.value).casefold()
    assert "input_value" not in str(caught.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_concurrent_runs_per_lane", 0),
        ("max_concurrent_runs_per_lane", 17),
        ("max_depth_per_lane", 0),
        ("max_depth_per_lane", 1025),
        ("event_buffer_capacity", 0),
        ("event_buffer_capacity", 10001),
        ("max_depth_per_lnae", 2),
    ],
)
def test_queue_bounds_and_nested_typos_refuse(field, value):
    with pytest.raises(ValidationError):
        AppConfig(_env_file=None, queue={field: value})


@pytest.mark.parametrize("workers", [1, 2])
async def test_composed_queue_honors_configured_concurrency_and_depth(
    workers, monkeypatch
):
    monkeypatch.setenv("KODEZART_QUEUE__MAX_CONCURRENT_RUNS_PER_LANE", str(workers))
    monkeypatch.setenv("KODEZART_QUEUE__MAX_DEPTH_PER_LANE", "1")
    engine = GatedWorkflowEngine()
    queue = build_job_queue(
        settings=AppConfig(_env_file=None).queue, workflow_engine=engine
    )
    await queue.start()
    try:
        for index in range(workers):
            await queue.submit(lane="lane", request=_request(str(index)))
            await _until(lambda expected=index + 1: len(engine.started) == expected)
        pending = await queue.submit(lane="lane", request=_request("pending"))
        with pytest.raises(QueueFullError):
            await queue.submit(lane="lane", request=_request("overflow"))
        assert engine.started == [str(index) for index in range(workers)]
        engine.release("0")
        await _until(lambda: "pending" in engine.started)
        assert await queue.get(job_id=pending.job_id) is not None
    finally:
        await queue.stop()


@pytest.mark.parametrize("capacity", [1, 4])
async def test_composed_queue_honors_replay_capacity(capacity):
    events = [
        AssistantTextEvent(model="fixture", text=str(index)) for index in range(3)
    ]
    queue = build_job_queue(
        settings=AppConfig(
            _env_file=None, queue={"event_buffer_capacity": capacity}
        ).queue,
        workflow_engine=ChattyWorkflowEngine(events),
    )
    await queue.start()
    try:
        record = await queue.submit(lane="lane", request=_request("run"))
        await _wait_terminal(queue, record.job_id)
        assert await _drain(queue, record.job_id) == events[-capacity:]
        terminal = await queue.get(job_id=record.job_id)
        assert terminal is not None and terminal.truncated is (capacity < len(events))
    finally:
        await queue.stop()


async def test_composed_zero_buffer_retention_drops_frames_but_retains_record():
    queue = build_job_queue(
        settings=AppConfig(
            _env_file=None, queue={"event_buffer_retention_seconds": 0}
        ).queue,
        workflow_engine=ChattyWorkflowEngine(
            [AssistantTextEvent(model="fixture", text="frame")]
        ),
    )
    await queue.start()
    try:
        record = await queue.submit(lane="lane", request=_request("run"))
        await _wait_terminal(queue, record.job_id)
        await _wait_truncated(queue, record.job_id)
        assert await _drain(queue, record.job_id) == []
        assert await queue.get(job_id=record.job_id) is not None
    finally:
        await queue.stop()


@pytest.mark.parametrize("configured", [False, True])
async def test_composed_retention_delays_drive_real_eviction(configured, monkeypatch):
    settings = AppConfig(
        _env_file=None, **({"queue": FIELDS} if configured else {})
    ).queue
    real_sleep = asyncio.sleep
    releases = {
        settings.event_buffer_retention_seconds: asyncio.Event(),
        settings.terminal_retention_seconds: asyncio.Event(),
    }
    observed = []

    async def controlled_sleep(delay, result=None):
        if delay in releases:
            observed.append(delay)
            await releases[delay].wait()
            return result
        return await real_sleep(delay, result)

    monkeypatch.setattr(asyncio, "sleep", controlled_sleep)
    queue = build_job_queue(
        settings=settings,
        workflow_engine=ChattyWorkflowEngine(
            [AssistantTextEvent(model="fixture", text="frame")]
        ),
    )
    await queue.start()
    try:
        record = await queue.submit(lane="lane", request=_request("run"))
        await _wait_terminal(queue, record.job_id)
        await _until(lambda: len(observed) == 2)
        assert set(observed) == set(releases)
        assert len(await _drain(queue, record.job_id)) == 1
        releases[settings.event_buffer_retention_seconds].set()
        await _wait_truncated(queue, record.job_id)
        assert await _drain(queue, record.job_id) == []
        assert await queue.get(job_id=record.job_id) is not None
        releases[settings.terminal_retention_seconds].set()
        await _until(lambda: record.job_id not in queue._records)
        assert await queue.get(job_id=record.job_id) is None
    finally:
        await queue.stop()
