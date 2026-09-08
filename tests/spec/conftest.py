"""Opt-in live conformance uses the existing configured tracker transport."""

import json
import os
from pathlib import Path

import pytest

from kodezart.adapters.toml_operation_config import load_operation_config
from kodezart.composition.tracker import build_tracker, make_mcp_tool_caller
from kodezart.core.backoff import RetryPolicy
from kodezart.core.config import AppConfig


@pytest.fixture
def live_model_snapshot():
    path = os.environ.get("KODEZART_MODEL_SNAPSHOT")
    if path is None:
        pytest.fail("live model comparison requires KODEZART_MODEL_SNAPSHOT")
    return json.loads(Path(path).read_text())


@pytest.fixture
async def live_model_tracker(live_model_snapshot):
    config = AppConfig()
    if config.operation_config is None or config.tracker.token is None:
        pytest.fail("live model comparison requires operation config and tracker token")
    operation = load_operation_config(Path(config.operation_config))
    caller = make_mcp_tool_caller(
        settings=config.tracker, token=config.tracker.token.get_secret_value()
    )
    await caller.open()
    try:
        tracker, _ = build_tracker(
            backend=config.tracker.backend,
            retry=RetryPolicy(
                attempts=config.tracker.max_retries + 1,
                initial_delay=config.tracker.retry_backoff_factor,
            ),
            operation=operation,
            caller=caller,
        )
        yield tracker
    finally:
        await caller.close()
