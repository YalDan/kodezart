"""The container-status role, driven over the in-process MCP server (KOD-484).

The role is not on the tracker port, so the port sweep the argument suite
drives never reaches it; it is driven here directly, over the same fake
server the scope reads run against — which already holds a project and an
initiative and is itself the caller.

Both members are driven here: the one write, and the read the terminal makes
before it (KOD-879).
"""

from collections.abc import Mapping

import pytest

from kodezart.adapters.linear.status_update import LinearScopeStatusUpdates
from kodezart.core.errors import (
    TrackerAccessDeniedError,
    TrackerProtocolError,
    TrackerUnavailableError,
)
from kodezart.domain.errors import ScopeStatusError
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.scope_terminal import STATUS_UPDATE_SCOPE_KINDS
from tests.fakes import FakeLinearMcpServer
from tests.tracker.connected_app_status_update_contract import (
    CONNECTED_APP_STATUS_UPDATES_ENVELOPE,
)
from tests.tracker.test_scope_reads import (
    INITIATIVE,
    MILESTONE,
    OTHER_PROJECT,
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

    await LinearScopeStatusUpdates(caller=server).post_status_update(ref=ref, body=BODY)

    assert [tool for tool, _ in server.calls] == ["save_status_update"]
    assert server.calls[0][1] == {"type": argument, argument: ref.key, "body": BODY}
    assert server.status_updates == [(argument, ref.key, BODY)]


async def test_no_health_and_no_identifier_ride_along() -> None:
    """A second vocabulary for the same fact, and an edit of an existing post."""
    server = ScopeMcpServer()

    await LinearScopeStatusUpdates(caller=server).post_status_update(
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
        await LinearScopeStatusUpdates(caller=server).post_status_update(
            ref=ref, body=BODY
        )

    assert server.calls == []


@pytest.mark.parametrize("body", ["", "   \n\t "], ids=["empty", "blank"])
async def test_a_body_with_nothing_in_it_refuses_before_any_call(body: str) -> None:
    server = ScopeMcpServer()

    with pytest.raises(ScopeStatusError, match="states nothing"):
        await LinearScopeStatusUpdates(caller=server).post_status_update(
            ref=PROJECT, body=body
        )

    assert server.calls == []


async def test_a_refused_credential_becomes_the_tracker_access_refusal() -> None:
    server = failing(credential_refused_after={"save_status_update": 0})

    with pytest.raises(TrackerAccessDeniedError):
        await LinearScopeStatusUpdates(caller=server).post_status_update(
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
        await LinearScopeStatusUpdates(caller=server).post_status_update(
            ref=PROJECT, body=BODY
        )

    assert [tool for tool, _ in server.calls] == ["save_status_update"]
    assert server.status_updates == []


async def test_a_target_the_workspace_does_not_hold_is_a_tool_error() -> None:
    server = ScopeMcpServer()
    absent = ScopeRef(kind=ScopeKind.PROJECT, key="no-such-project")

    with pytest.raises(TrackerUnavailableError):
        await LinearScopeStatusUpdates(caller=server).post_status_update(
            ref=absent, body=BODY
        )

    assert server.status_updates == []


def test_the_adapters_targets_are_the_kinds_the_terminal_posts_for() -> None:
    """One mapping, two consumers: the service's branch and the adapter's."""
    from kodezart.adapters.linear.status_update import _TARGET_ARGUMENT

    assert set(_TARGET_ARGUMENT) == STATUS_UPDATE_SCOPE_KINDS


# ---------------------------------------------------------------------------
# KOD-879 — the one read beside the write: what the container already carries,
# newest first, so a report equal to the newest one is not posted twice.
# ---------------------------------------------------------------------------

EARLIER = "Scope outcome: scope_stopped_short"


async def test_the_read_lists_the_containers_updates_newest_first() -> None:
    """The order is the adapter's own, not the position the answer arrived in.

    The server answers its items OLDEST first with an ascending creation
    instant apiece, so a reader that returned the list as it came would put
    the oldest report first and compare the terminal's vector against a
    superseded one.
    """
    server = ScopeMcpServer()
    role = LinearScopeStatusUpdates(caller=server)
    await role.post_status_update(ref=PROJECT, body=EARLIER)
    await role.post_status_update(ref=PROJECT, body=BODY)

    bodies = await role.status_update_bodies(ref=PROJECT)

    assert list(bodies) == [BODY, EARLIER]
    assert [tool for tool, _ in server.calls][-1] == "get_status_updates"
    assert server.calls[-1][1] == {
        "type": "project",
        "project": PROJECT.key,
        "orderBy": "createdAt",
    }


async def test_the_listing_answers_the_addressed_container_and_no_other() -> None:
    """The double holds every container's updates, and lists one container's.

    A double answering its whole store would let the adapter read as
    addressed while the target it sent went nowhere, and the terminal would
    compare its vector against a neighbouring project's newest report.
    """
    other = ScopeRef(kind=ScopeKind.PROJECT, key=OTHER_PROJECT)
    server = ScopeMcpServer()
    role = LinearScopeStatusUpdates(caller=server)
    await role.post_status_update(ref=other, body=EARLIER)
    await role.post_status_update(ref=PROJECT, body=BODY)

    bodies = await role.status_update_bodies(ref=PROJECT)

    assert list(bodies) == [BODY]
    assert list(await role.status_update_bodies(ref=other)) == [EARLIER]


async def test_the_servers_listing_answers_the_measured_envelope() -> None:
    """The double answers the shape that was measured, by member name.

    The adapter is written against the measured listing, so a double
    answering some other shape would let it pass over a payload the backend
    never sends — and the measurement is only worth recording if something
    is held to it.
    """
    server = ScopeMcpServer()
    await LinearScopeStatusUpdates(caller=server).post_status_update(
        ref=PROJECT, body=BODY
    )

    listing = server._tool_get_status_updates(
        {"type": "project", "project": PROJECT.key}
    )

    assert CONNECTED_APP_STATUS_UPDATES_ENVELOPE["listing"] <= set(listing)
    (item,) = listing["statusUpdates"]
    assert CONNECTED_APP_STATUS_UPDATES_ENVELOPE["item"] <= set(item)


async def test_a_container_carrying_nothing_reads_empty() -> None:
    """Empty is an answer: a container nothing was posted on is not a refusal."""
    assert (
        await LinearScopeStatusUpdates(caller=ScopeMcpServer()).status_update_bodies(
            ref=PROJECT
        )
        == ()
    )


@pytest.mark.parametrize("ref", [MILESTONE, ISSUE], ids=["milestone", "issue"])
async def test_a_kind_with_no_status_surface_refuses_the_read_before_any_call(
    ref: ScopeRef,
) -> None:
    """One fact about the kind, so the read refuses where the write does."""
    server = ScopeMcpServer()

    with pytest.raises(ScopeStatusError, match="carries no status update"):
        await LinearScopeStatusUpdates(caller=server).status_update_bodies(ref=ref)

    assert server.calls == []


async def test_a_refused_credential_on_the_read_is_the_tracker_access_refusal() -> None:
    server = failing(credential_refused_after={"get_status_updates": 0})

    with pytest.raises(TrackerAccessDeniedError):
        await LinearScopeStatusUpdates(caller=server).status_update_bodies(ref=PROJECT)


@pytest.mark.parametrize(
    "knob",
    ["transport_failures", "transient_failures"],
    ids=["transport", "transient"],
)
async def test_a_failed_read_becomes_tracker_unavailable(knob: str) -> None:
    server = failing(**{knob: {"get_status_updates": 1}})

    with pytest.raises(TrackerUnavailableError):
        await LinearScopeStatusUpdates(caller=server).status_update_bodies(ref=PROJECT)


class ListingServer:
    """A caller answering one scripted payload for the listing tool."""

    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.calls: list[str] = []

    async def call_tool(self, *, name: str, arguments: Mapping[str, object]) -> object:
        self.calls.append(name)
        return self._payload


@pytest.mark.parametrize(
    "payload",
    [
        {"hasNextPage": False, "cursor": None},
        {"statusUpdates": [{"body": BODY}]},
        {"statusUpdates": [{"createdAt": "2026-09-21T00:00:00Z"}]},
        {"statusUpdates": "not a list"},
    ],
    ids=["no listing member", "no instant", "no body", "not a list"],
)
async def test_an_answer_without_the_listing_member_is_a_protocol_error(
    payload: object,
) -> None:
    """The shape is refused rather than read as a container carrying nothing.

    Read as empty, a payload the adapter cannot parse would say "no report is
    there yet" and the report would be posted again on every walk — the one
    confusion this criterion exists to end.
    """
    server = ListingServer(payload)

    with pytest.raises(TrackerProtocolError, match="declared shape"):
        await LinearScopeStatusUpdates(caller=server).status_update_bodies(ref=PROJECT)

    assert server.calls == ["get_status_updates"]
