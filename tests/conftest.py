"""Shared async test fixtures — no mocking, full chain exercised."""

import ipaddress
import logging
import os
import socket
from collections.abc import AsyncGenerator, Callable, Generator, Iterator

import pytest
import structlog
from httpx import ASGITransport, AsyncClient

from kodezart.adapters.git.branch_merger import GitBranchMerger
from kodezart.adapters.git.service import SubprocessGitService
from kodezart.config.app import AppConfig
from kodezart.main import create_app
from kodezart.services.agent_service import AgentService
from kodezart.types.domain.agent import AssistantTextEvent, ResultEvent
from tests.fakes import (
    SUPPRESS_ALL_SKILLS,
    FakeAgentExecutor,
    FakeWorkspaceProvider,
)

# The suite is HERMETIC: it never reads the developer's environment or a
# working-directory .env. Either one carrying a real deployment turned
# green runs red — a configured operator could not run the gate beside
# their own service, which is the opposite of the fresh-environment
# requirement (KOD-168). Applied at import, before any test constructs a
# config; a test that needs a value sets it itself, and the env-file
# behaviour tests pass their own file explicitly.
for _ambient in [name for name in os.environ if name.startswith("KODEZART_")]:
    del os.environ[_ambient]
AppConfig.model_config["env_file"] = None
# An HTTP client honours a proxy named in the environment, and a proxy on a
# loopback port would relay an in-process request off this machine past the
# socket guard below, which sees only the loopback connect to the proxy.
for _proxy in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_proxy, None)
    os.environ.pop(_proxy.lower(), None)


@pytest.fixture(autouse=True)
def _restore_logging_configuration() -> Iterator[None]:
    """A boot test must not leave handlers bound to its closed capture stream."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    levels = {
        name: logging.getLogger(name).level
        for name in ("", "uvicorn.access", "uvicorn.error")
    }
    configuration = structlog.get_config()
    try:
        yield
    finally:
        root.handlers = handlers
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)
        structlog.configure(**configuration)


@pytest.fixture(scope="session", autouse=True)
def _git_test_identity() -> None:
    """Provide a git identity to subprocess git commands invoked by tests.

    Tests shell out to `git commit` in tmp repos; CI runners have no global
    git config, so without this they fail with "Author identity unknown".
    """
    os.environ.setdefault("GIT_AUTHOR_NAME", "kodezart-test")
    os.environ.setdefault("GIT_AUTHOR_EMAIL", "test@kodezart-test.invalid")
    os.environ.setdefault("GIT_COMMITTER_NAME", "kodezart-test")
    os.environ.setdefault("GIT_COMMITTER_EMAIL", "test@kodezart-test.invalid")


#: The marker classes the collection gate deselects from the default run.
#: Read by the census as well as by the gate below: a test newly carrying
#: one of these is a test that stops running.
GATED_MARKERS: dict[str, str] = {
    "live": "live tests need external credentials or CLI (run with: pytest -m live)",
    "postgres": (
        "postgres tests need a database at KODEZART_TEST_POSTGRES_URL "
        "(run with: pytest -m postgres)"
    ),
}


class LiveReachError(Exception):
    """A default-run test opened a connection to an address off this machine."""

    def __init__(self, address: object) -> None:
        super().__init__(
            f"a default-run test reached the non-loopback address {address!r}"
        )
        self.address = address


def _loopback(address: object) -> bool:
    """Whether an internet socket *address* names this machine and no other."""
    if not isinstance(address, tuple) or not address:
        return False
    host = str(address[0]).split("%", 1)[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


#: Every refusal the guard made since the current item began, in order. A
#: refusal the code under test caught and carried on from is still a test
#: that tried to leave this machine, so it fails that item at teardown; a
#: test that means to be refused takes its own record with take_refusals().
_REFUSALS: list[LiveReachError] = []

#: The two socket methods the guard wraps, as the socket module ships them.
_GUARDED_METHODS = ("connect", "connect_ex")
_UNGUARDED: dict[str, Callable[[socket.socket, object], object]] = {}


def _guarded[R](
    original: Callable[[socket.socket, object], R],
) -> Callable[[socket.socket, object], R]:
    """*original*, refusing before any packet an address off this machine."""

    def connect(self: socket.socket, address: object) -> R:
        if self.family != socket.AF_UNIX and not _loopback(address):
            refused = LiveReachError(address)
            _REFUSALS.append(refused)
            raise refused
        return original(self, address)

    return connect


def _guard(*, on: bool) -> None:
    """Put the guard on the socket class, or put the shipped methods back."""
    for name, original in _UNGUARDED.items():
        setattr(socket.socket, name, _guarded(original) if on else original)


def guarded() -> bool:
    """Whether both socket methods are wrapped by the guard right now."""
    return all(
        getattr(socket.socket, name) is not original
        for name, original in _UNGUARDED.items()
    )


def take_refusals() -> list[LiveReachError]:
    """The refusals made since the current item began, taken so none fails it."""
    taken = list(_REFUSALS)
    _REFUSALS.clear()
    return taken


def leaves_the_machine(item: pytest.Item) -> bool:
    """Whether *item* may reach off this machine: it carries a gated mark.

    That set is ``GATED_MARKERS`` itself, read here rather than listed again.
    """
    return any(item.get_closest_marker(name) for name in GATED_MARKERS)


def pytest_configure(config: pytest.Config) -> None:
    """Guard the whole session, collection and wide-scope fixtures included.

    Every default-run test stays on this machine: the fakes, never a
    workspace. A Unix socket or a loopback address is reached as before; a
    connect anywhere else raises ``LiveReachError`` before the call is made
    (KOD-469). Installed once, before collection, so module import, and the
    setup of a session-, package- or module-scoped fixture, run under it as
    a test body does.

    What it does not reach, in code terms: a child process, because the
    patch is on this interpreter's socket class; name resolution, which
    connects nothing through it; and ``sendto`` on a datagram socket, which
    sends without a connect.
    """
    _ = config
    _UNGUARDED.update({name: getattr(socket.socket, name) for name in _GUARDED_METHODS})
    _guard(on=True)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Put the socket class back as the session found it."""
    _ = config
    _guard(on=False)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, object, object]:
    """Lift the guard for exactly the item a gated mark lets leave the machine.

    Setup, call and teardown of that item run unguarded, and the guard is
    back before the next item begins. Every item starts with no refusal on
    record, so what the teardown check reads is that item's own.
    """
    _ = nextitem
    _REFUSALS.clear()
    if not leaves_the_machine(item):
        return (yield)
    _guard(on=False)
    try:
        return (yield)
    finally:
        _guard(on=True)


@pytest.fixture(autouse=True)
def _no_swallowed_refusal() -> Iterator[None]:
    """Fail an item whose code under test caught a refusal and carried on."""
    yield
    swallowed = take_refusals()
    if swallowed:
        pytest.fail(f"a refused off-machine connect was swallowed: {swallowed}")


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    marker_expr = config.getoption("-m", default="")
    for marker, reason in GATED_MARKERS.items():
        if marker in marker_expr:
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
async def client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def subprocess_git_service() -> SubprocessGitService:
    """Shared SubprocessGitService configured with the default ``origin`` remote.

    Absorbs the explicit ``remote="origin"`` kwarg sites the inherited
    19aca0d sprinkled across the adapter test files; collapses the
    repeat-construction pattern.  Tests that need a non-default remote
    still construct directly with a different remote name.
    """
    return SubprocessGitService(remote="origin")


@pytest.fixture
def git_branch_merger(
    subprocess_git_service: SubprocessGitService,
) -> GitBranchMerger:
    """Shared GitBranchMerger constructed atop the shared git fixture.

    Uses ``FakeWorkspaceProvider`` so the fixture is usable from tests
    that exercise the four-status decision tree against fakes without
    spinning up real bare clones.  Tests that need a real workspace
    provider (e.g. the new REAL-bare-clone integration test) construct
    a ``GitBranchMerger`` directly.
    """
    return GitBranchMerger(
        git=subprocess_git_service,
        workspace=FakeWorkspaceProvider(),
        remote="origin",
    )


@pytest.fixture
async def agent_client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    app.state.skills = SUPPRESS_ALL_SKILLS
    app.state.agent_service = AgentService(
        git_base_url="https://github.com",
        executor=FakeAgentExecutor(
            events=[
                AssistantTextEvent(text="analysis complete", model="test-model"),
                ResultEvent(
                    subtype="result",
                    duration_ms=100,
                    duration_api_ms=80,
                    is_error=False,
                    num_turns=1,
                    session_id="test-session",
                ),
            ]
        ),
        workspace=FakeWorkspaceProvider(),
        persister=None,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
