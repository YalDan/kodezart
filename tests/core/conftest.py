"""Fixtures shared by the configuration suites in this directory."""

import os

import pytest


@pytest.fixture
def _pristine_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every KODEZART_ variable so only the test speaks.

    A default is only a default when nothing else is speaking, and a
    developer's exported variable would otherwise make these suites agree
    with whatever is already configured.
    """
    for name in list(os.environ):
        if name.startswith("KODEZART_"):
            monkeypatch.delenv(name)
