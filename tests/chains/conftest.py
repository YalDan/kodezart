"""Fixtures the three graph suites share.

The floor cases in this directory all measure the same thing — the gap
between two attempts at one node — and all have the same second
explanation to rule out.
"""

import random

import pytest


@pytest.fixture
def unjittered_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the randomness out of the vendor's back-off for one case.

    ``RetryPolicy`` sleeps ``interval + random.uniform(0, 1)`` between
    attempts, so up to a second of the gap between two attempts is the
    policy's own jitter and NOT the floor.  A case measuring a floor
    smaller than a second while the jitter is live is not measuring the
    floor at all: it passes with the floor unwrapped, which is how the
    wiring came to be unproven in the first place — measured 2026-09-04,
    the engine's own case passed with all fifteen floored nodes
    unwrapped.  Fixed at zero, the policy contributes ``initial_interval``
    and nothing else.
    """
    monkeypatch.setattr(random, "uniform", lambda _low, _high: 0.0)
