"""All addressed artifact reads preserve actual boundary failure meaning."""

import asyncio

import pytest

from kodezart.core.errors import (
    McpCredentialRefusedError,
    McpTransportError,
    TrackerAccessDeniedError,
    TrackerUnavailableError,
)
from kodezart.domain.errors import WriteBackReadError
from kodezart.services.tracker_artifacts import read_tracker_artifact
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from tests.fakes import FakeLinearMcpServer
from tests.tracker.test_linear_mcp_tracker import tracker_over

SURFACES = (
    WritableSurface(
        kind=SurfaceKind.ISSUE_DESCRIPTION,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="issue"),
    ),
    WritableSurface(
        kind=SurfaceKind.ISSUE_GRAPH, ref=ScopeRef(kind=ScopeKind.ISSUE, key="issue")
    ),
    WritableSurface(
        kind=SurfaceKind.ISSUE_SPLIT_SET,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="issue"),
    ),
    WritableSurface(
        kind=SurfaceKind.ISSUE_LABEL_SET,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="issue"),
    ),
    WritableSurface(
        kind=SurfaceKind.CRITERION_SUB_ISSUE,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="issue"),
    ),
    WritableSurface(
        kind=SurfaceKind.CRITERION_CHILD_SET,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="issue"),
    ),
    WritableSurface(
        kind=SurfaceKind.MARKER_COMMENT,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key="issue"),
        marker="[marker]",
    ),
    WritableSurface(
        kind=SurfaceKind.CONTAINER_DESCRIPTION,
        ref=ScopeRef(kind=ScopeKind.PROJECT, key="project"),
    ),
)


class FailingMcp(FakeLinearMcpServer):
    def __init__(self, failure):
        super().__init__()
        self.failure = failure
        self.attempts = 0

    async def call_tool(self, *, name, arguments):
        self.attempts += 1
        raise self.failure


@pytest.mark.parametrize("surface", SURFACES, ids=lambda value: value.kind.value)
@pytest.mark.parametrize("failure_type", [asyncio.CancelledError, RuntimeError])
async def test_cancel_and_programming_errors_escape_each_actual_native_read(
    surface, failure_type
):
    failure = failure_type("original boundary failure")
    server = FailingMcp(failure)
    with pytest.raises(failure_type) as raised:
        await read_tracker_artifact(tracker=tracker_over(server), surface=surface)
    assert raised.value is failure
    assert server.attempts == 1


@pytest.mark.parametrize("surface", SURFACES, ids=lambda value: value.kind.value)
@pytest.mark.parametrize("denied", [False, True])
async def test_declared_native_failure_is_never_an_empty_artifact(surface, denied):
    failure = (
        McpCredentialRefusedError("credentials refused", server_name="linear")
        if denied
        else McpTransportError("read unavailable", server_name="linear")
    )
    server = FailingMcp(failure)
    expected = TrackerAccessDeniedError if denied else TrackerUnavailableError
    with pytest.raises(expected) as raised:
        await read_tracker_artifact(tracker=tracker_over(server), surface=surface)
    assert raised.value.__cause__ is failure
    assert server.attempts == 1


async def test_unsupported_status_surface_refuses_before_any_native_read():
    server = FailingMcp(RuntimeError("must never reach backend"))
    surface = WritableSurface(
        kind=SurfaceKind.CONTAINER_STATUS_UPDATE,
        ref=ScopeRef(kind=ScopeKind.PROJECT, key="project"),
    )
    with pytest.raises(WriteBackReadError, match="unavailable"):
        await read_tracker_artifact(tracker=tracker_over(server), surface=surface)
    assert server.attempts == 0
