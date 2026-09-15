"""Native entry translates tracker-port failures without losing their cause."""

from typing import Literal

import pytest

from kodezart.chains.criteria import TrackerCriteria
from kodezart.core.errors import TrackerAccessDeniedError, TrackerUnavailableError
from kodezart.domain.errors import FireSpecEntryError
from tests.chains.test_native_fire import SUBJECT, tracker


@pytest.mark.parametrize("boundary", ["read_spec", "read_current"])
@pytest.mark.parametrize(
    "error_type", [TrackerUnavailableError, TrackerAccessDeniedError]
)
async def test_native_entry_preserves_tracker_port_failure(
    monkeypatch: pytest.MonkeyPatch,
    boundary: Literal["read_spec", "read_current"],
    error_type: type[TrackerUnavailableError] | type[TrackerAccessDeniedError],
) -> None:
    port = tracker()
    source = TrackerCriteria(tracker=port)
    spec = await source.read_spec(issue_key=SUBJECT)
    failure = error_type("the current tracker read cannot be authorized")

    async def unavailable(**_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(
        port,
        "read_fire_spec" if boundary == "read_spec" else "scope_issues",
        unavailable,
    )
    with pytest.raises(FireSpecEntryError) as raised:
        if boundary == "read_spec":
            await source.read_spec(issue_key=SUBJECT)
        else:
            await source.read_current(spec=spec)

    assert raised.value.issue_key == SUBJECT
    assert raised.value.__cause__ is failure
