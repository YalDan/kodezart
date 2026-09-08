"""Configured routing reaches the actual scoped first-entry validator."""

from unittest.mock import AsyncMock

import pytest

from kodezart.domain.errors import (
    ScopedExecutionUnavailableError,
    TrackerFirePreparationError,
)
from kodezart.types.domain.operation import OperationConfig
from tests.tracker.marker_config import MARKER_PREFIXES
from tests.tracker.test_addressed_preloop import BASE, ISSUE, REPOSITORY
from tests.tracker.test_addressed_preloop import prepared as prepared


def bind(prepared, route):
    data = prepared.operation.model_dump()
    if route == "implicit":
        data["repos"] = data["repos"][:1]
    else:
        data["teams"]["board"]["repository"] = REPOSITORY
    prepared.operation = OperationConfig.model_validate(data)


@pytest.mark.parametrize("route", ["implicit", "explicit"])
@pytest.mark.parametrize("marker", ["missing", "foreign", "moves"])
async def test_bound_route_reaches_native_validation_without_marker_authority(
    prepared, route, marker, monkeypatch
):
    bind(prepared, route)
    marker_read = AsyncMock(wraps=prepared.tracker.recorded_repository)
    monkeypatch.setattr(prepared.tracker, "recorded_repository", marker_read)
    if marker == "missing":
        prepared.server.comments = [
            row
            for row in prepared.server.comments
            if MARKER_PREFIXES["repository"] not in row.body
        ]
        prepared.fake.recorded_repositories.clear()
    else:
        prepared.repository("https://forge.invalid/foreign/repository")
    if marker == "moves":

        async def move():
            prepared.repository("https://forge.invalid/later/repository")

        prepared.executor.during = move
    with pytest.raises(ScopedExecutionUnavailableError, match="ruling and loop"):
        await prepared.drive()
    assert len(prepared.executor.calls) == 1
    marker_read.assert_not_awaited()
    assert prepared.executor.calls[0]["session_id"] is None
    assert prepared.workspace.calls[0][0] == "acquire"
    assert prepared.workspace.calls[-1] == ("release", "/tmp/fake-workspace")
    prepared.no_writes()


@pytest.mark.parametrize("route", ["implicit", "explicit"])
@pytest.mark.parametrize("change", ["base", "head", "team"])
async def test_bound_route_still_refuses_changed_native_authority(
    prepared, route, change
):
    from kodezart.types.domain.branch import trunk_base

    bind(prepared, route)

    async def move():
        if change == "base":
            prepared.base(trunk_base("changed/base"))
        elif change == "head":
            prepared.git._remote_branch_shas[BASE.base_branch] = "b" * 40
        else:
            prepared.server.issues[ISSUE].team = "foreign-team"
            prepared.fake.issues[ISSUE] = prepared.fake.issues[ISSUE].model_copy(
                update={"team_key": "foreign-team"}
            )

    prepared.executor.during = move
    with pytest.raises(TrackerFirePreparationError, match="changed during preparation"):
        await prepared.drive()
    assert len(prepared.executor.calls) == 1
    prepared.no_writes()


@pytest.mark.parametrize("route", ["explicit", "recorded"])
@pytest.mark.parametrize(
    "change", ["binding", "team-removed", "repositories", "name", "key", "scope"]
)
async def test_loaded_routing_facts_cannot_change_during_validation(
    prepared, change, route
):
    if route == "explicit":
        bind(prepared, "explicit")

    async def move():
        if change == "binding":
            prepared.operation.teams["board"] = prepared.operation.teams[
                "board"
            ].model_copy(update={"repository": prepared.operation.repos[1].url})
        elif change == "team-removed":
            del prepared.operation.teams["board"]
        elif change == "scope":
            prepared.operation.teams["board"] = prepared.operation.teams[
                "board"
            ].model_copy(update={"scope": ("foreign-container",)})
        elif change in {"name", "key"}:
            prepared.operation.teams["board"] = prepared.operation.teams[
                "board"
            ].model_copy(update={change: "foreign-team"})
        else:
            prepared.operation.repos.pop(0)

    prepared.executor.during = move
    with pytest.raises(TrackerFirePreparationError, match="routing changed"):
        await prepared.drive()
    assert len(prepared.executor.calls) == 1
    assert prepared.workspace.calls[-1] == ("release", "/tmp/fake-workspace")
    prepared.no_writes()
