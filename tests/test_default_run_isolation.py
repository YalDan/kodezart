"""A default-run test stays on this machine.

The suite's fixtures run against in-process doubles, and the default run is
the one every change is gated on. What keeps it there is a runtime fact, not
an import list: every connect a default-run test makes to an address off this
machine is refused before any packet leaves (KOD-469). These two cases pin
both sides of that line.
"""

import socket

import pytest

from tests.conftest import LiveReachError

#: A documentation address (TEST-NET-3): routable in form, reserved in fact.
OFF_MACHINE = ("203.0.113.1", 443)


def test_a_default_run_item_cannot_reach_a_non_loopback_address() -> None:
    """Both connect spellings refuse an address off this machine, naming it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        with pytest.raises(LiveReachError) as refused:
            probe.connect(OFF_MACHINE)
        assert refused.value.address == OFF_MACHINE
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        with pytest.raises(LiveReachError) as refused:
            probe.connect_ex(OFF_MACHINE)
        assert refused.value.address == OFF_MACHINE


def test_a_default_run_item_still_reaches_loopback() -> None:
    """A listening socket on this machine is reached as before."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.connect(listener.getsockname())
            accepted, _ = listener.accept()
            with accepted:
                assert accepted.getpeername() == probe.getsockname()
