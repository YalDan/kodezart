"""The declared delivery watch budget survives normal settings loading."""

import pytest
from pydantic import ValidationError

from kodezart.core.config import AppConfig


def test_default_watch_bound():
    assert AppConfig().delivery_max_concurrent_watches == 4


@pytest.mark.parametrize("bound", [1, 32])
def test_watch_bound_environment(monkeypatch, bound):
    monkeypatch.setenv("KODEZART_DELIVERY_MAX_CONCURRENT_WATCHES", str(bound))
    assert AppConfig().delivery_max_concurrent_watches == bound


@pytest.mark.parametrize("bound", [0, 33])
def test_invalid_watch_bound_refuses(bound):
    with pytest.raises(ValidationError):
        AppConfig(delivery_max_concurrent_watches=bound)
