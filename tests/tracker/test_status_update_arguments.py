"""Both status-update calls send only declared arguments (KOD-143, KOD-484).

The declaration is the connected-app one, with the standing that module
states: observed through a connected app rather than the operation's own
credential, no create performed, create envelope unmeasured. The sent side
is OBSERVED — the shipped role is driven and the arguments it actually
handed the transport are read back — not scanned out of the source.

The same three claims are made of the create and of the listing the terminal
reads through (KOD-879), because they are two calls on one declaration.
"""

import pytest

from kodezart.adapters.linear.status_update import LinearScopeStatusUpdates
from kodezart.types.domain.scope import ScopeRef
from tests.tracker.connected_app_status_update_contract import (
    CONNECTED_APP_STATUS_UPDATE_ARGUMENTS,
    CONNECTED_APP_STATUS_UPDATE_REQUIRED,
    CONNECTED_APP_STATUS_UPDATE_TYPES,
)
from tests.tracker.test_scope_reads import INITIATIVE, PROJECT, ScopeMcpServer

TOOL = "save_status_update"
READ_TOOL = "get_status_updates"


async def sent_arguments(ref: ScopeRef) -> list[dict[str, object]]:
    server = ScopeMcpServer()
    await LinearScopeStatusUpdates(caller=server).post_status_update(
        ref=ref, body="Scope outcome: scope_converged"
    )
    return [dict(arguments) for tool, arguments in server.calls if tool == TOOL]


async def read_arguments(ref: ScopeRef) -> list[dict[str, object]]:
    server = ScopeMcpServer()
    await LinearScopeStatusUpdates(caller=server).status_update_bodies(ref=ref)
    return [dict(arguments) for tool, arguments in server.calls if tool == READ_TOOL]


@pytest.mark.parametrize("ref", [PROJECT, INITIATIVE], ids=["project", "initiative"])
async def test_every_argument_sent_is_one_the_tool_declares(ref: ScopeRef) -> None:
    declared = CONNECTED_APP_STATUS_UPDATE_ARGUMENTS[TOOL]
    calls = await sent_arguments(ref)

    assert calls, "the writer made no call to answer for"
    for arguments in calls:
        assert set(arguments) <= declared, arguments


@pytest.mark.parametrize("ref", [PROJECT, INITIATIVE], ids=["project", "initiative"])
async def test_every_required_argument_of_the_tool_is_sent(ref: ScopeRef) -> None:
    required = CONNECTED_APP_STATUS_UPDATE_REQUIRED[TOOL]

    for arguments in await sent_arguments(ref):
        assert required <= set(arguments), arguments


@pytest.mark.parametrize("ref", [PROJECT, INITIATIVE], ids=["project", "initiative"])
async def test_the_type_sent_is_a_declared_value_and_names_its_own_target(
    ref: ScopeRef,
) -> None:
    """``type`` and the argument carrying the target are one name."""
    for arguments in await sent_arguments(ref):
        kind = arguments["type"]
        assert kind in CONNECTED_APP_STATUS_UPDATE_TYPES
        assert arguments[str(kind)] == ref.key
        assert CONNECTED_APP_STATUS_UPDATE_TYPES - {kind} <= (
            CONNECTED_APP_STATUS_UPDATE_TYPES - set(arguments)
        )


# ---------------------------------------------------------------------------
# KOD-879 — the same three claims about the listing call the terminal reads
# through, against the listing tool's own declaration.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ref", [PROJECT, INITIATIVE], ids=["project", "initiative"])
async def test_every_argument_the_read_sends_is_one_the_tool_declares(
    ref: ScopeRef,
) -> None:
    declared = CONNECTED_APP_STATUS_UPDATE_ARGUMENTS[READ_TOOL]
    calls = await read_arguments(ref)

    assert calls, "the reader made no call to answer for"
    for arguments in calls:
        assert set(arguments) <= declared, arguments


@pytest.mark.parametrize("ref", [PROJECT, INITIATIVE], ids=["project", "initiative"])
async def test_every_required_argument_of_the_read_is_sent(ref: ScopeRef) -> None:
    required = CONNECTED_APP_STATUS_UPDATE_REQUIRED[READ_TOOL]

    for arguments in await read_arguments(ref):
        assert required <= set(arguments), arguments


@pytest.mark.parametrize("ref", [PROJECT, INITIATIVE], ids=["project", "initiative"])
async def test_the_read_names_its_own_target(ref: ScopeRef) -> None:
    """``type`` and the argument carrying the target are one name here too."""
    for arguments in await read_arguments(ref):
        kind = arguments["type"]
        assert kind in CONNECTED_APP_STATUS_UPDATE_TYPES
        assert arguments[str(kind)] == ref.key
        assert CONNECTED_APP_STATUS_UPDATE_TYPES - {kind} <= (
            CONNECTED_APP_STATUS_UPDATE_TYPES - set(arguments)
        )
