"""A default-run test stays on this machine.

The suite's fixtures run against in-process doubles, and the default run is
the one every change is gated on. What keeps it there is a runtime fact, not
an import list: every connect a default-run test makes to an address off this
machine is refused before any packet leaves (KOD-469), for the whole session,
wide-scope fixture setup included. These cases pin both sides of that line,
the exemption a gated mark earns, and the refusal a fixture's setup meets.

The guard is in-process: a child process, name resolution and ``sendto`` on
a datagram socket are outside it, as ``tests/conftest.py`` states.
"""

import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.conftest import (
    GATED_MARKERS,
    LiveReachError,
    guarded,
    leaves_the_machine,
    pytest_runtest_protocol,
    take_refusals,
)

#: A documentation address (TEST-NET-3): routable in form, reserved in fact.
OFF_MACHINE = ("203.0.113.1", 443)

#: How long a loopback connect may take before the test says it hung.
LOOPBACK_SECONDS = 5.0


def _has_ipv6_loopback() -> bool:
    """Whether this host can bind the IPv6 loopback address at all."""
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


def reaches(probe: socket.socket, listener: socket.socket, address: object) -> None:
    """*probe* connects to *listener* at *address*, and the listener sees it."""
    probe.settimeout(LOOPBACK_SECONDS)
    probe.connect(address)
    accepted, _ = listener.accept()
    with accepted:
        assert accepted.getpeername() == probe.getsockname()


def refused_then_reaches_loopback(connect_name: str) -> None:
    """One non-blocking probe is refused off the machine, then reaches loopback.

    Non-blocking, so a guard that let the call through could not hold the
    test on the network's timeout. The loopback connect succeeding on the
    same socket is what shows the refused call never ran: a socket whose
    connect had begun answers a second one with an error, not a connection.
    """
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener,
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe,
    ):
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        probe.setblocking(False)
        with pytest.raises(LiveReachError) as refused:
            getattr(probe, connect_name)(OFF_MACHINE)
        assert refused.value.address == OFF_MACHINE
        assert take_refusals() == [refused.value]
        reaches(probe, listener, listener.getsockname())


def test_a_default_run_item_cannot_reach_a_non_loopback_address() -> None:
    """Both connect spellings refuse an address off this machine, naming it."""
    refused_then_reaches_loopback("connect")
    refused_then_reaches_loopback("connect_ex")


def test_a_default_run_item_still_reaches_loopback() -> None:
    """A listening socket on this machine is reached as before."""
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener,
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe,
    ):
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        reaches(probe, listener, listener.getsockname())


def test_localhost_by_name_is_this_machine() -> None:
    """The name ``localhost`` is loopback, and is reached as an address is."""
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener,
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe,
    ):
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        reaches(probe, listener, ("localhost", listener.getsockname()[1]))


#: The IPv6 loopback case, generated only where the host has an IPv6
#: loopback to bind: a host without one has no such address to reach, and
#: the case is then absent from the run rather than skipped in it.
IPV6_LOOPBACK = ["::1"] if _has_ipv6_loopback() else []


@pytest.mark.parametrize("host", IPV6_LOOPBACK)
def test_the_ipv6_loopback_is_this_machine(host: str) -> None:
    """``::1`` is loopback, and is reached as the IPv4 one is."""
    with (
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as listener,
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe,
    ):
        listener.bind((host, 0))
        listener.listen(1)
        reaches(probe, listener, (host, listener.getsockname()[1]))


def test_a_unix_socket_is_this_machine() -> None:
    """A Unix socket names no host, and is reached as before."""
    with tempfile.TemporaryDirectory(prefix="kz") as directory:
        path = str(Path(directory) / "s")
        with (
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener,
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe,
        ):
            listener.bind(path)
            listener.listen(1)
            probe.settimeout(LOOPBACK_SECONDS)
            probe.connect(path)
            accepted, _ = listener.accept()
            with accepted:
                assert accepted.family == socket.AF_UNIX


def live_reach_in(error: BaseException | None) -> LiveReachError | None:
    """The refusal *error* is, or carries, however a client wrapped it.

    Through a cause or context chain, and through the exception group a
    task group raises when the connect it ran failed.
    """
    if error is None or isinstance(error, LiveReachError):
        return error
    if isinstance(error, BaseExceptionGroup):
        for inner in error.exceptions:
            found = live_reach_in(inner)
            if found is not None:
                return found
    return live_reach_in(error.__cause__ or error.__context__)


async def test_an_http_client_request_off_the_machine_is_refused() -> None:
    """The async HTTP client the adapters use meets the same refusal."""
    async with httpx.AsyncClient(timeout=LOOPBACK_SECONDS) as client:
        with pytest.raises(
            (LiveReachError, httpx.HTTPError, BaseExceptionGroup)
        ) as raised:
            await client.get(f"https://{OFF_MACHINE[0]}/")
    refused = live_reach_in(raised.value)
    assert refused is not None
    assert take_refusals() == [refused]


@pytest.fixture(scope="module")
def reached_at_module_scope() -> Iterator[LiveReachError | None]:
    """A module-scoped fixture whose setup tries to leave the machine."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setblocking(False)
        try:
            probe.connect_ex(OFF_MACHINE)
        except LiveReachError as refused:
            yield refused
            return
    yield None


def test_a_wide_scope_fixture_setup_is_refused(
    reached_at_module_scope: LiveReachError | None,
) -> None:
    """The guard is on before a module fixture sets up, not only in a body."""
    assert reached_at_module_scope is not None
    assert reached_at_module_scope.address == OFF_MACHINE
    assert take_refusals() == [reached_at_module_scope]


class MarkedItem:
    """The one thing the guard asks of an item: which marks it carries."""

    def __init__(self, *marks: str) -> None:
        self.marks = marks

    def get_closest_marker(self, name: str) -> str | None:
        return name if name in self.marks else None


def guarded_while_running(item: MarkedItem) -> bool:
    """Whether the guard is on while *item* runs; it must be on again after."""
    protocol = pytest_runtest_protocol(item=item, nextitem=None)
    next(protocol)
    during = guarded()
    with pytest.raises(StopIteration):
        protocol.send(None)
    assert guarded()
    return during


def test_an_item_with_a_gated_mark_runs_unguarded() -> None:
    """Each gated mark, read from the same table the gate reads, lifts it.

    One case over the table rather than one case per mark: a case named for
    a gated mark would be deselected by the gate it is about.
    """
    assert GATED_MARKERS
    for mark in GATED_MARKERS:
        assert leaves_the_machine(MarkedItem(mark)), mark
        assert not guarded_while_running(MarkedItem(mark)), mark


def test_an_unmarked_item_runs_guarded() -> None:
    """No mark, or a mark that is not gated, leaves the guard on."""
    assert guarded()
    for item in (MarkedItem(), MarkedItem("asyncio")):
        assert not leaves_the_machine(item)
        assert guarded_while_running(item)
