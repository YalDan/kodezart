"""The scope status writer, driven over the in-process MCP server (KOD-484).

The role is not on the tracker port, so the port sweep the argument suite
drives never reaches it; it is driven here directly, over the same fake
server the scope reads run against — which already holds a project and an
initiative and is itself the caller.
"""

from collections.abc import Mapping

import pytest

from kodezart.adapters.linear.status_update import LinearScopeStatusWriter
from kodezart.core.errors import TrackerAccessDeniedError, TrackerUnavailableError
from kodezart.domain.errors import ScopeStatusError
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_terminal import STATUS_UPDATE_SCOPE_KINDS
from tests.fakes import FakeLinearMcpServer
from tests.tracker.test_scope_reads import (
    INITIATIVE,
    MILESTONE,
    PROJECT,
    ScopeMcpServer,
)

BODY = "Scope outcome: scope_converged"
ISSUE = ScopeRef(kind=ScopeKind.ISSUE, key="FIX-1")


def failing(
    *,
    credential_refused_after: Mapping[str, int] | None = None,
    transport_failures: Mapping[str, int] | None = None,
    transient_failures: Mapping[str, int] | None = None,
) -> FakeLinearMcpServer:
    """A server holding the fixture project and one scripted failure."""
    return FakeLinearMcpServer(
        projects={PROJECT.key: {"id": PROJECT.key}},
        credential_refused_after=credential_refused_after,
        transport_failures=transport_failures,
        transient_failures=transient_failures,
    )


@pytest.mark.parametrize(
    ("ref", "argument"),
    [(PROJECT, "project"), (INITIATIVE, "initiative")],
    ids=["project", "initiative"],
)
async def test_one_call_addresses_the_container_by_the_requests_own_key(
    ref: ScopeRef, argument: str
) -> None:
    """The scope's own key is the target; nothing is read to resolve it first."""
    server = ScopeMcpServer()

    await LinearScopeStatusWriter(caller=server).post_status_update(ref=ref, body=BODY)

    assert [tool for tool, _ in server.calls] == ["save_status_update"]
    assert server.calls[0][1] == {"type": argument, argument: ref.key, "body": BODY}
    assert server.status_updates == [(argument, ref.key, BODY)]


async def test_no_health_and_no_identifier_ride_along() -> None:
    """A second vocabulary for the same fact, and an edit of an existing post."""
    server = ScopeMcpServer()

    await LinearScopeStatusWriter(caller=server).post_status_update(
        ref=PROJECT, body=BODY
    )

    sent = server.calls[0][1]
    assert set(sent) == {"type", "project", "body"}


@pytest.mark.parametrize("ref", [MILESTONE, ISSUE], ids=["milestone", "issue"])
async def test_a_kind_with_no_status_surface_refuses_before_any_call(
    ref: ScopeRef,
) -> None:
    server = ScopeMcpServer()

    with pytest.raises(ScopeStatusError, match="carries no status update"):
        await LinearScopeStatusWriter(caller=server).post_status_update(
            ref=ref, body=BODY
        )

    assert server.calls == []


@pytest.mark.parametrize("body", ["", "   \n\t "], ids=["empty", "blank"])
async def test_a_body_with_nothing_in_it_refuses_before_any_call(body: str) -> None:
    server = ScopeMcpServer()

    with pytest.raises(ScopeStatusError, match="states nothing"):
        await LinearScopeStatusWriter(caller=server).post_status_update(
            ref=PROJECT, body=body
        )

    assert server.calls == []


async def test_a_refused_credential_becomes_the_tracker_access_refusal() -> None:
    server = failing(credential_refused_after={"save_status_update": 0})

    with pytest.raises(TrackerAccessDeniedError):
        await LinearScopeStatusWriter(caller=server).post_status_update(
            ref=PROJECT, body=BODY
        )

    assert server.status_updates == []


@pytest.mark.parametrize(
    "knob",
    ["transport_failures", "transient_failures"],
    ids=["transport", "transient"],
)
async def test_a_transport_or_transient_failure_becomes_tracker_unavailable(
    knob: str,
) -> None:
    """One attempt only: a create the transport lost may already have landed."""
    server = failing(**{knob: {"save_status_update": 1}})

    with pytest.raises(TrackerUnavailableError):
        await LinearScopeStatusWriter(caller=server).post_status_update(
            ref=PROJECT, body=BODY
        )

    assert [tool for tool, _ in server.calls] == ["save_status_update"]
    assert server.status_updates == []


async def test_a_target_the_workspace_does_not_hold_is_a_tool_error() -> None:
    server = ScopeMcpServer()
    absent = ScopeRef(kind=ScopeKind.PROJECT, key="no-such-project")

    with pytest.raises(TrackerUnavailableError):
        await LinearScopeStatusWriter(caller=server).post_status_update(
            ref=absent, body=BODY
        )

    assert server.status_updates == []


def test_the_adapters_targets_are_the_kinds_the_terminal_posts_for() -> None:
    """One mapping, two consumers: the service's branch and the adapter's."""
    from kodezart.adapters.linear.status_update import _TARGET_ARGUMENT

    assert set(_TARGET_ARGUMENT) == STATUS_UPDATE_SCOPE_KINDS
