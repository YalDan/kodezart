"""PR114 real-Git exit and ref-invariance scenarios.

The named scenarios exercise cleanup and publication invariance directly.
Which exits there ARE is read off the step's own source and off what it
asks, rather than remembered.  Every ``raise`` statement in the union
step's modules is a site one scenario must name, and a scenario that names
one proves it drove that site from the traceback it caught, not from the
wording of a message.  Every member the step asks of a port it is handed is
a collaborator failure one row must plant, from its first call, and the
row proves the planted error is the one that left.  The census says which
exits exist; only the scenarios are runtime proof.
"""

import ast
import asyncio
import copy
import importlib
import inspect
import operator
import sys
import traceback
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from functools import partial
from pathlib import Path
from types import CodeType, FunctionType

import pytest

from kodezart.adapters.git.check_chain import SubprocessCheckChainRunner
from kodezart.chains.delivery_coordinator import ScopeUnionCoordinator
from kodezart.config.app import AppConfig
from kodezart.core import protocols
from kodezart.domain.errors import (
    CheckChainExecutionError,
    GitOperationError,
    MergeConflictError,
    TransientAPIError,
    UnionHeadReadError,
    UnionUnstableError,
)
from kodezart.services.union_composition import UnionComposition
from kodezart.types.domain.operation import CheckStep
from kodezart.types.domain.union import UnionLaneHead, UnionOutcome
from kodezart.types.domain.union_tick import UnionTickContext
from tests.chains.test_delivery_coordinator import (
    OPENED_ORDER,
    PROJECT,
    RaisingRunner,
    Scope,
    work_ref,
)
from tests.chains.union_holdings import (
    UNION_MODULES,
    allowed_as,
    declared,
    held_by,
    ports_of,
)
from tests.fakes import FakeScopePlanReader, FakeWorkRefReader, role_view
from tests.name_resolution import definitions
from tests.services import test_union_composition as pinned

INDEPENDENT_EDITS = {lane: (f"{lane}.txt", lane) for lane in OPENED_ORDER}
CONFLICTING_EDITS = {
    "z": ("api.py", "def build(timeout):\n    return timeout\n"),
    "a": ("api.py", "def build(credentials):\n    return credentials\n"),
}
COMPOSED_CHECK = pinned.entry().checks[0].command

#: A declared chain whose one step exits non-zero, so the composed result is
#: RED with its checks observed: the composed return carrying a failure.
FAILING_CHECKS = (CheckStep(name="gate", command="false"),)

#: Every port the step is constructed with, by parameter, read off its
#: constructor's annotations.
PORT_PARAMETERS: dict[str, type] = ports_of(ScopeUnionCoordinator)


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


class Forwarded:
    """A member read through the recorder: it can be called, and nothing else.

    The port's own bound method would hand back the unrecorded port through
    ``__self__``, so the recorder hands back this instead.  Every name that
    is not a dunder is refused, so its slot is not a way back either; a
    dunder read resolves on this class, which defines no ``__self__``,
    ``__func__``, ``__wrapped__`` or ``__closure__``; and its state is not
    handed out for copying or pickling.
    """

    __slots__ = ("_call",)

    def __init__(self, call: Callable[..., object]) -> None:
        object.__setattr__(self, "_call", call)

    def __getattribute__(self, name: str) -> object:
        if _is_dunder(name):
            return object.__getattribute__(self, name)
        raise AttributeError(name)

    def __getstate__(self) -> object:
        raise TypeError("a member read through the recorder is not copied")

    def __call__(self, *args: object, **kwargs: object) -> object:
        return object.__getattribute__(self, "_call")(*args, **kwargs)


class Asked:
    """A port as the step holds it, recording every member read off it.

    Each read of a name that is not a dunder is recorded, then forwarded to
    the port, so the record keys on the read that executed rather than on how
    it was spelled: a dot, ``hasattr``, ``getattr`` with an assembled name,
    through an alias or with starred arguments, ``operator.attrgetter`` or
    ``methodcaller``, and ``port.__getattribute__(name)`` — a dunder read
    resolves on the proxy itself, and its ``__getattribute__`` is the
    recorder.  A member that can be called comes back as ``Forwarded``, never
    as the port's own bound method, so no read through it (``__self__``,
    ``__func__``, a closure cell) reaches the unrecorded port; a member that
    cannot be called comes back as the port holds it.  A member named in
    *failing* is still recorded, and comes back failing with the error its
    entry makes (``COLLABORATOR_FAILURES``).  The proxy's own state
    is not handed out for copying or pickling either.  Out of reach, and so
    stated: ``object.__getattribute__`` on the slots of the proxy or of what
    it hands back (``test_reading_the_recorders_own_slots_is_not_recorded``),
    ``type(port).__dict__``, which reads the proxy's class, ``gc`` and
    ``eval``/``exec``.
    """

    __slots__ = ("_failing", "_names", "_port")

    def __init__(
        self,
        port: object,
        *,
        failing: Mapping[str, Callable[[], BaseException]] | None = None,
    ) -> None:
        object.__setattr__(self, "_port", port)
        object.__setattr__(self, "_names", [])
        object.__setattr__(self, "_failing", {} if failing is None else failing)

    def __getattribute__(self, name: str) -> object:
        if _is_dunder(name):
            return object.__getattribute__(self, name)
        object.__getattribute__(self, "_names").append(name)
        member = getattr(object.__getattribute__(self, "_port"), name)
        planted = object.__getattribute__(self, "_failing").get(name)
        if planted is not None:
            return Forwarded(_raising(member, planted))
        return Forwarded(member) if callable(member) else member

    def __getstate__(self) -> object:
        raise TypeError("a recorded port is not copied")


def _raising(
    member: object, planted: Callable[[], BaseException]
) -> Callable[..., object]:
    """*member*, failing the way its port fails: when awaited, if it is awaited."""
    if inspect.iscoroutinefunction(member):

        async def fail_when_awaited(*_: object, **__: object) -> object:
            raise planted()

        return fail_when_awaited

    def fail(*_: object, **__: object) -> object:
        raise planted()

    return fail


def asked_of(proxy: Asked) -> tuple[str, ...]:
    """Every member name read off *proxy*, in the order it was read."""
    names: list[str] = object.__getattribute__(proxy, "_names")
    return tuple(names)


def _raised(node: ast.Raise) -> str:
    """What a raise statement raises, and the reason it gives when it gives one.

    A bare ``raise`` re-raises what it is handling and is spelled ``raise``.
    A reason written as one literal is quoted as that text; one computed from
    locals is quoted as the expression that computes it.
    """
    if node.exc is None:
        return "raise"
    exc = node.exc
    if not isinstance(exc, ast.Call):
        return ast.unparse(exc)
    raised = ast.unparse(exc.func)
    for keyword in exc.keywords:
        if keyword.arg == "reason":
            value = keyword.value
            text = (
                value.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
                else ast.unparse(value)
            )
            return f"{raised}: {text}"
    return raised


def _left_by(node: ast.Raise, parents: dict[int, ast.AST]) -> frozenset[int]:
    """The lines an error sent by *node* leaves its frame from.

    A raise with an operand sends a new error from its own line.  A bare
    ``raise`` sends the error it is handling on with that error's own
    traceback, which still reads the line of the guarded body the error left
    first, so its lines are the body of the ``try`` whose handler it sits in.
    """
    if node.exc is not None:
        return frozenset({node.lineno})
    current: ast.AST = node
    while not isinstance(current, ast.ExceptHandler):
        current = parents[id(current)]
    guarded = parents[id(current)]
    assert isinstance(guarded, ast.Try | ast.TryStar)
    return frozenset(
        line
        for statement in guarded.body
        for line in range(statement.lineno, (statement.end_lineno or 0) + 1)
    )


def exit_sites() -> dict[str, tuple[str, frozenset[int]]]:
    """Every raise statement in the union step's modules: site -> (file, lines).

    Keyed by the function the statement sits in and what it raises, so a row
    names the exit in the code's own words.  Two statements with one key would
    make a row ambiguous, so that fails here.  Bounded by the syntax trees of
    ``UNION_MODULES``; a raise inside a collaborator the step calls (the
    runner, the planner, the git port) is that collaborator's, and a row that
    drives one names no site.
    """
    sites: dict[str, tuple[str, frozenset[int]]] = {}
    for name in UNION_MODULES:
        module = importlib.import_module(name)
        tree = ast.parse(inspect.getsource(module))
        where = definitions(tree)
        parents = {
            id(child): node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise):
                site = f"{where[id(node)]}: {_raised(node)}"
                assert site not in sites, site
                sites[site] = (
                    str(inspect.getsourcefile(module)),
                    _left_by(node, parents),
                )
    return sites


EXIT_SITES: dict[str, tuple[str, frozenset[int]]] = exit_sites()


def _returned(node: ast.Return) -> str:
    """What a return statement hands back: the callee it awaits or calls, or
    the expression itself."""
    value: ast.expr | None = node.value
    if isinstance(value, ast.Await):
        value = value.value
    if isinstance(value, ast.Call):
        value = value.func
    return "None" if value is None else ast.unparse(value)


def return_sites() -> dict[str, tuple[CodeType, frozenset[int]]]:
    """Every return statement of a ``verify`` method in the union step's modules.

    site -> (that method's code, the statement's lines).  Keyed by the method
    and what the statement hands back, so a row names the return in the
    code's own words; two statements with one key would make a row
    ambiguous, so that fails here.  A method is resolved by object, off the
    module it is defined in.  Bounded by the syntax trees of
    ``UNION_MODULES``; a return inside a function nested in ``verify`` is
    that function's, not a way ``verify`` returns.
    """
    sites: dict[str, tuple[CodeType, frozenset[int]]] = {}
    for name in UNION_MODULES:
        module = importlib.import_module(name)
        tree = ast.parse(inspect.getsource(module))
        where = definitions(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Return):
                continue
            owner = where[id(node)]
            if not owner.endswith(".verify"):
                continue
            site = f"{owner}: {_returned(node)}"
            assert site not in sites, site
            method: object = module
            for part in owner.split("."):
                method = inspect.getattr_static(method, part)
            assert isinstance(method, FunctionType), owner
            sites[site] = (
                method.__code__,
                frozenset(range(node.lineno, (node.end_lineno or 0) + 1)),
            )
    return sites


RETURN_SITES: dict[str, tuple[CodeType, frozenset[int]]] = return_sites()


@contextmanager
def returns_taken() -> Iterator[set[str]]:
    """The return sites whose statement ran while the block ran.

    Read with ``sys.monitoring`` line events on the ``verify`` methods' own
    code, so which return a call took is a fact of what executed rather than
    of what the result looks like.  The tool slot is released however the
    block leaves.
    """
    by_code: dict[CodeType, list[tuple[str, frozenset[int]]]] = {}
    for site, (code, lines) in RETURN_SITES.items():
        by_code.setdefault(code, []).append((site, lines))
    taken: set[str] = set()
    monitoring = sys.monitoring
    tool = monitoring.PROFILER_ID

    def ran(code: CodeType, line: int) -> None:
        taken.update(site for site, lines in by_code.get(code, ()) if line in lines)

    monitoring.use_tool_id(tool, "union returns")
    try:
        monitoring.register_callback(tool, monitoring.events.LINE, ran)
        for code in by_code:
            monitoring.set_local_events(tool, code, monitoring.events.LINE)
        yield taken
    finally:
        for code in by_code:
            monitoring.set_local_events(tool, code, 0)
        monitoring.register_callback(tool, monitoring.events.LINE, None)
        monitoring.free_tool_id(tool)


def errors_behind(error: BaseException) -> list[BaseException]:
    """*error* and every error behind it, by cause or by context.

    Bounded by the errors already read, each read once.
    """
    found: list[BaseException] = []
    seen: set[int] = set()
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        pending.extend((current.__cause__, current.__context__))
    return found


def passed_through(error: BaseException) -> frozenset[tuple[str, int]]:
    """Every (file, line) *error* left a frame through, and the errors behind it.

    A traceback holds one entry per frame the error passed through, at the
    line it left that frame by, so the line of the raise statement that sent
    it is among them however the message reads.  A cancellation awaited from
    outside its task is a fresh error whose context is the one the step
    raised, so the context and the cause are followed too.
    """
    return frozenset(
        (frame.filename, frame.lineno)
        for current in errors_behind(error)
        for frame in traceback.extract_tb(current.__traceback__)
    )


class Fixture:
    """A whole delivery world: author repository, remote, and the observer."""

    def __init__(
        self,
        *,
        scope: Scope,
        git: pinned.ObservedGit,
        context: UnionTickContext,
        author: Path,
        remote: Path,
        observer: Path,
    ) -> None:
        self.scope = scope
        self.git = git
        self.context = context
        self.author = author
        self.remote = remote
        self.observer = observer
        self.tracker = scope.tracker()
        self.handed: list[tuple[str, Asked]] = []
        self.built: list[ScopeUnionCoordinator] = []
        self.harness: list[object] = []
        self.planted: list[tuple[str, BaseException]] = []

    def coordinator(
        self,
        runner: object = None,
        *,
        failing: Mapping[str, tuple[str, ...]] | None = None,
    ) -> ScopeUnionCoordinator:
        """The production step, each port it is handed wrapped in a recorder.

        *failing* names, by parameter, the members that port fails on from
        their first call, with the error ``PLANTED_ERRORS`` gives it.
        """
        ports = {
            # Each port as its role's double over the board ``self.tracker``
            # seeds: the step holds exactly the roles it is typed on.
            "tracker": role_view(FakeScopePlanReader, self.tracker),
            "refs": role_view(FakeWorkRefReader, self.tracker),
            "git": self.git,
            "runner": runner
            or SubprocessCheckChainRunner(
                timeout=AppConfig().union_check_step_timeout_seconds
            ),
        }
        assert set(ports) == set(PORT_PARAMETERS), sorted(ports)
        planted = {
            name: {
                member: partial(self._plant, name, member)
                for member in (failing or {}).get(name, ())
            }
            for name in ports
        }
        # What a row plants is the harness, not something the step holds.
        self.harness.extend(planted.values())
        handed = {
            name: Asked(port, failing=planted[name]) for name, port in ports.items()
        }
        self.handed.extend(handed.items())
        step = ScopeUnionCoordinator(
            scope_kind=PROJECT.kind,
            **handed,
            context=self.context,
            config=AppConfig(),
            committer_name="Union Fixture",
            committer_email="union@example.invalid",
        )
        self.built.append(step)
        return step

    def _plant(self, parameter: str, member: str) -> BaseException:
        error = PLANTED_ERRORS[parameter](member)
        self.planted.append((member, error))
        return error

    def refused_holdings(self) -> list[str]:
        """The class of everything a step built here holds that it may not hold.

        Walked when called, so after a drive it is what each step holds once
        it has verified, whatever it attached to itself while verifying.  The
        recorders the ports were handed in are this fixture's own, so each is
        walked through and not judged: the port inside it is judged instead.
        What a scenario put in the step's reach on purpose is named in
        ``harness``, by identity, and is neither judged nor walked into.
        """
        proxies = {id(proxy) for _, proxy in self.handed}
        return [
            f"{type(value).__module__}.{type(value).__qualname__}"
            for step in self.built
            for value in held_by(step, besides=self.harness)
            if id(value) not in proxies and allowed_as(value) is None
        ]

    def asked(self, parameter: str) -> frozenset[str]:
        """Every member read off the port handed as *parameter*, on any step."""
        return frozenset(
            member
            for name, proxy in self.handed
            if name == parameter
            for member in asked_of(proxy)
        )

    def undeclared_reads(self) -> dict[str, list[str]]:
        """Each port's reads of a member the port it is typed as does not declare."""
        return {
            name: sorted(self.asked(name) - declared(port))
            for name, port in PORT_PARAMETERS.items()
            if self.asked(name) - declared(port)
        }

    def unrowed_asks(self) -> dict[str, list[str]]:
        """Each port's reads of a member no collaborator-failure row fails."""
        rowed = {
            (parameter, members[-1]) for parameter, members in COLLABORATOR_FAILURES
        }
        return {
            name: sorted(
                member for member in self.asked(name) if (name, member) not in rowed
            )
            for name in PORT_PARAMETERS
            if any((name, member) not in rowed for member in self.asked(name))
        }

    def sha(self, lane: str) -> str:
        return self.scope.by_lane[lane].head_sha

    def with_checks(self, checks: object) -> None:
        """Replace the declared chain on the repository this scope composes."""
        self.context = self.context.model_copy(
            update={"repo": self.context.repo.model_copy(update={"checks": checks})}
        )

    async def refs(self) -> tuple[str, ...]:
        """Every ref in every repository — what any publication would move."""
        return tuple(
            [
                await pinned.git(path, "show-ref")
                for path in (self.author, self.remote, self.observer)
            ]
        )

    async def merge_graph_order(self, scratch_sha: str) -> tuple[str, ...]:
        """The lanes the composed commit actually merged, oldest merge first.

        Read out of the merge graph the scratch tree left behind rather than
        out of the port double's call log, so an order recorded correctly and
        composed differently is still caught.
        """
        by_sha = {head.head_sha: lane for lane, head in self.scope.by_lane.items()}
        merged: list[str] = []
        cursor = scratch_sha
        while True:
            parents = (
                await pinned.git(self.observer, "show", "-s", "--format=%P", cursor)
            ).split()
            if len(parents) != 2:
                return tuple(reversed(merged))
            merged.append(by_sha[parents[1]])
            cursor = parents[0]


async def make_repository(
    root: Path, *, edits: dict[str, tuple[str, str]]
) -> tuple[Path, str, tuple[UnionLaneHead, ...]]:
    """One branch per lane off a shared base, in the opened order."""
    repo = root / "repo"
    repo.mkdir(parents=True)
    await pinned.git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base\n")
    await pinned.git(repo, "add", ".")
    await pinned.git(repo, "commit", "-m", "base")
    base = await pinned.git(repo, "rev-parse", "HEAD")
    heads: list[UnionLaneHead] = []
    for lane in OPENED_ORDER:
        path, text = edits[lane]
        await pinned.git(repo, "checkout", "-b", f"work/{lane}", base)
        (repo / path).write_text(text)
        await pinned.git(repo, "add", ".")
        await pinned.git(repo, "commit", "-m", lane)
        heads.append(
            UnionLaneHead(
                lane_key=lane,
                branch=f"work/{lane}",
                head_sha=await pinned.git(repo, "rev-parse", "HEAD"),
            )
        )
    await pinned.git(repo, "checkout", "main")
    return repo, base, tuple(heads)


async def build_delivery(
    root: Path,
    *,
    edits: dict[str, tuple[str, str]] | None = None,
    git: pinned.ObservedGit | None = None,
) -> Fixture:
    """The production wiring over a real repository, remote and observer."""
    root.mkdir(parents=True, exist_ok=True)
    author, base, heads = await make_repository(root, edits=edits or INDEPENDENT_EDITS)
    remote, observer = root / "remote.git", root / "observer.git"
    await pinned.git(root, "clone", "--bare", str(author), str(remote))
    await pinned.git(
        root, "clone", "--bare", "--origin", "upstream", str(remote), str(observer)
    )
    # Settle the observer's remote-tracking refs through the same port the
    # union step fetches with. Fetching is a read the step is entitled to
    # make; leaving its first one until then would put a legitimate read
    # inside any before/after comparison of the world's refs.
    await pinned.SubprocessGitService(remote="upstream").fetch(str(observer))
    return Fixture(
        scope=Scope(heads),
        git=git or pinned.ObservedGit(),
        context=UnionTickContext(
            scope_key=PROJECT.key,
            repo_path=str(observer),
            base_sha=base,
            git_remote="upstream",
            repo=pinned.entry(COMPOSED_CHECK).model_copy(
                update={"url": remote.as_uri()}
            ),
        ),
        author=author,
        remote=remote,
        observer=observer,
    )


class RecordingPublisher(pinned.ObservedGit):
    """The real git port, remembering every publication it was asked for.

    It still publishes.  A double that refused would show only that its
    refusal was reached, and a best-effort publish that swallowed the
    refusal would leave every ref where it was; a port that publishes and
    remembers being asked gives two witnesses that cannot cover for each
    other.  The record is appended before the call, so an attempt is on it
    whether or not it landed.
    """

    def __init__(self) -> None:
        super().__init__()
        self.publications: list[tuple[str, str, str]] = []

    async def push(self, cwd: str, branch: str) -> None:
        self.publications.append(("push", cwd, branch))
        await super().push(cwd, branch)

    async def merge_branch(self, cwd: str, source_branch: str) -> None:
        self.publications.append(("merge_branch", cwd, source_branch))
        await super().merge_branch(cwd, source_branch)

    async def delete_remote_branch(self, cwd: str, remote: str, branch: str) -> None:
        # The remote belongs in the record: "which ref would have gone" is the
        # fact, and the branch name alone does not say which repository's.
        self.publications.append(("delete_remote_branch", cwd, f"{remote}/{branch}"))
        await super().delete_remote_branch(cwd, remote, branch)


class PathlessConflict(RecordingPublisher):
    """A refused merge git named no conflicting path for."""

    async def merge_scratch_head(self, **kwargs: object) -> None:
        raise MergeConflictError(
            "the scratch head could not be merged",
            source_branch=str(kwargs["head_sha"]),
            paths=(),
        )


class MovingHeads(RecordingPublisher):
    """A port whose remote head read answers with a different commit every time.

    The tick requires two matching head reads around its fetch, so heads
    that move on every read exhaust its bounded attempts and it refuses
    without composing.  The movement is in the OBSERVATION and not in the
    world: every real ref stays exactly where it was, which is the fact the
    case around this double measures on that exit.
    """

    def __init__(self) -> None:
        super().__init__()
        self.head_reads = 0

    async def remote_branch_sha(self, cwd: str, remote: str, branch: str) -> str | None:
        # Read the real branch first, so an absent branch is still reported
        # absent rather than answered with a commit name nothing carries.
        if await super().remote_branch_sha(cwd, remote, branch) is None:
            return None
        self.head_reads += 1
        return f"{self.head_reads:040x}"


class BlockedCreate(RecordingPublisher):
    """A git port that parks inside worktree creation until released."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def create_worktree(self, *args: object, **kwargs: object) -> None:
        await super().create_worktree(*args, **kwargs)
        self.entered.set()
        await self.release.wait()


class MovingAfterComposing(RecordingPublisher):
    """Heads that read true until a scratch tree has been composed, then move.

    The other way to the same refusal: the reads around the fetch agree, the
    step composes and checks a real scratch tree, and only the read that
    would authorize returning it disagrees.  Every later read moves, so the
    bounded attempts run out after a composition, not before one.
    """

    def __init__(self) -> None:
        super().__init__()
        self.head_reads = 0

    async def remote_branch_sha(self, cwd: str, remote: str, branch: str) -> str | None:
        sha = await super().remote_branch_sha(cwd, remote, branch)
        if sha is None or not self.created:
            return sha
        self.head_reads += 1
        return f"{self.head_reads:040x}"


class UnreadableHeads(RecordingPublisher):
    """A remote whose head read fails as a git command fails."""

    async def remote_branch_sha(self, cwd: str, remote: str, branch: str) -> str | None:
        raise GitOperationError(f"the head of {branch} did not read")


class UnfetchableRemote(RecordingPublisher):
    """A remote whose heads read but whose fetch fails as a git command fails."""

    async def fetch(self, repo_path: str) -> None:
        raise GitOperationError(f"{repo_path} could not fetch")


class SubstitutedObjects(RecordingPublisher):
    """A repository whose replacement namespace names a substitution."""

    async def has_replace_refs(self, cwd: str) -> bool:
        return True


class UnreadableReplacements(RecordingPublisher):
    """A repository whose replacement namespace does not read."""

    async def has_replace_refs(self, cwd: str) -> bool:
        raise GitOperationError(f"the replacement namespace of {cwd} did not read")


async def drive_green(fixture) -> None:
    result = await fixture.coordinator().verify()
    assert result.outcome is UnionOutcome.GREEN


async def drive_merge_conflict(fixture) -> None:
    result = await fixture.coordinator().verify()
    assert result.outcome is UnionOutcome.RED
    assert result.merge_conflict is not None


async def drive_failing_chain(fixture) -> None:
    """The composed return, carrying a chain that ran and failed."""
    fixture.with_checks(FAILING_CHECKS)
    result = await fixture.coordinator().verify()
    assert result.outcome is UnionOutcome.RED
    assert result.checks is not None
    assert result.merge_conflict is None


async def refused(
    fixture, error: type[BaseException], match: str | None
) -> BaseException:
    """Verify, require the refusal named, and hand it back for its site."""
    with pytest.raises(error, match=match) as caught:
        await fixture.coordinator().verify()
    return caught.value


async def drive_pathless_conflict(fixture) -> BaseException:
    return await refused(fixture, MergeConflictError, "could not be merged")


async def drive_unclassifiable_chain(fixture) -> BaseException:
    repeated = CheckStep(name="gate", command="true")
    fixture.with_checks((repeated, repeated))
    return await refused(fixture, CheckChainExecutionError, None)


async def drive_undeclared_chain(fixture) -> BaseException:
    fixture.with_checks(())
    return await refused(fixture, CheckChainExecutionError, "no check chain")


async def drive_unobservable_chain(fixture) -> BaseException:
    with pytest.raises(CheckChainExecutionError) as caught:
        await fixture.coordinator(RaisingRunner()).verify()
    return caught.value


async def drive_unstable_heads(fixture) -> BaseException:
    """The refusal reached before anything is composed."""
    error = await refused(fixture, UnionUnstableError, None)
    assert fixture.git.created == []
    return error


async def drive_unstable_after_composing(fixture) -> BaseException:
    """The same refusal, reached after a scratch tree really existed."""
    error = await refused(fixture, UnionUnstableError, None)
    assert fixture.git.created != []
    return error


async def drive_no_participating_lane(fixture) -> BaseException:
    """A scope whose every member is a criterion: nothing to compose."""
    fixture.tracker.scope_memberships[PROJECT] = tuple(
        f"{lane}-check" for lane in OPENED_ORDER
    )
    return await refused(fixture, UnionHeadReadError, "no participating lane")


async def drive_no_deliverable_ref(fixture) -> BaseException:
    fixture.tracker.recorded_work_refs[OPENED_ORDER[0]] = []
    return await refused(fixture, UnionHeadReadError, "records no deliverable ref")


async def drive_two_deliverable_refs(fixture) -> BaseException:
    lane = OPENED_ORDER[0]
    fixture.tracker.recorded_work_refs[lane].append(
        work_ref(lane, f"work/{lane}-again", fixture.sha(lane))
    )
    return await refused(fixture, UnionHeadReadError, "more than one deliverable")


async def drive_absent_branch(fixture) -> BaseException:
    """A lane whose recorded branch the remote does not carry."""
    lane = OPENED_ORDER[0]
    fixture.tracker.recorded_work_refs[lane] = [
        work_ref(lane, "work/absent-from-the-remote", fixture.sha(lane))
    ]
    return await refused(fixture, UnionHeadReadError, "absent on the remote")


async def drive_unreadable_head(fixture) -> BaseException:
    return await refused(fixture, UnionHeadReadError, "no readable commit identity")


async def drive_unfetchable_remote(fixture) -> BaseException:
    return await refused(fixture, UnionHeadReadError, "could not be fetched")


async def drive_substituted_objects(fixture) -> BaseException:
    return await refused(fixture, UnionHeadReadError, "substitutes Git objects")


async def drive_unreadable_replacements(fixture) -> BaseException:
    return await refused(fixture, UnionHeadReadError, "namespace is unreadable")


async def drive_cached_reuse(
    fixture,
    *,
    outcome: UnionOutcome = UnionOutcome.GREEN,
    conflict: bool = False,
    checks: tuple[CheckStep, ...] | None = None,
) -> None:
    """One coordinator asked twice while no head moves, so the second ask reuses.

    Reuse is a third way verifying returns and the one no scenario reached: it
    composes nothing, so a publication placed on it sits behind every witness
    the composing rows carry.  The record and the refs are read again
    immediately before the second ask, so what this row measures is that ask by
    itself rather than the pair of them.  The first answer is any cached kind —
    green, a merge conflict or a failing chain — because the step caches every
    one of them and answers the second ask from each the same way.
    """
    if checks is not None:
        fixture.with_checks(checks)
    coordinator = fixture.coordinator()
    first = await coordinator.verify()
    assert first.outcome is outcome
    assert (first.merge_conflict is not None) is conflict
    published, before = list(fixture.git.publications), await fixture.refs()

    second = await coordinator.verify()

    # The SAME result object is what says the second ask took the reuse return
    # rather than composing a second result that merely compares equal.
    assert second is first
    assert fixture.git.publications == published
    assert await fixture.refs() == before


async def drive_roster_change(fixture) -> BaseException:
    """Membership moves once the composed chain has run, before the last check.

    The mutation is made from the chain the step itself runs, which is the
    one point inside a measurement that is after the roster was read and
    before the roster is read back, so the refusal is the coordinator's own.
    """

    class MovingRosterRunner(SubprocessCheckChainRunner):
        async def run_chain(self, *, cwd, steps):
            result = await super().run_chain(cwd=cwd, steps=steps)
            fixture.tracker.scope_memberships[PROJECT] = (OPENED_ORDER[-1],)
            return result

    runner = MovingRosterRunner(
        timeout=AppConfig().union_check_step_timeout_seconds,
    )
    # The runner reaches the fixture to move the roster: the harness, not a
    # collaborator the step chose.
    fixture.harness.append(fixture)
    with pytest.raises(UnionHeadReadError, match="roster changed") as caught:
        await fixture.coordinator(runner).verify()
    return caught.value


async def drive_cancellation(fixture) -> BaseException:
    # The two events are how this case parks and releases the port; they
    # are the harness, not something the step chose to hold.
    fixture.harness.extend((fixture.git.entered, fixture.git.release))
    task = asyncio.create_task(fixture.coordinator().verify())
    try:
        await asyncio.wait_for(fixture.git.entered.wait(), 10)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        fixture.git.release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, 10)
    return caught.value


#: Every way verifying leaves by a raise the step's modules write, and the
#: named ways it returns, each with the raise site it drives: a key of
#: EXIT_SITES, or None where it returns, or where what it raises is a
#: collaborator's own error passing through.  The census below requires the
#: named sites to be every raise the step's modules write, so an exit added
#: without a scenario fails there rather than standing unwitnessed.  Leaving
#: because a collaborator failed is COLLABORATOR_FAILURES, below.
EXIT_SCENARIOS = (
    ("a green union", None, RecordingPublisher, drive_green, None),
    (
        "a merge conflict",
        CONFLICTING_EDITS,
        RecordingPublisher,
        drive_merge_conflict,
        None,
    ),
    ("a failing chain", None, RecordingPublisher, drive_failing_chain, None),
    (
        "a conflict naming no path",
        None,
        PathlessConflict,
        drive_pathless_conflict,
        "UnionComposition.verify: raise",
    ),
    (
        "a chain that cannot be classified",
        None,
        RecordingPublisher,
        drive_unclassifiable_chain,
        "UnionComposition.verify: CheckChainExecutionError: '; '.join(failures)",
    ),
    (
        "a repository declaring no chain",
        None,
        RecordingPublisher,
        drive_undeclared_chain,
        "UnionComposition.verify: CheckChainExecutionError: no check chain is declared",
    ),
    (
        "a chain that cannot be observed",
        None,
        RecordingPublisher,
        drive_unobservable_chain,
        None,
    ),
    (
        "cancellation while composing",
        None,
        BlockedCreate,
        drive_cancellation,
        "UnionComposition.verify: asyncio.CancelledError",
    ),
    (
        "heads that will not hold still",
        None,
        MovingHeads,
        drive_unstable_heads,
        "UnionTick.verify: UnionUnstableError",
    ),
    (
        "heads that move once composed",
        None,
        MovingAfterComposing,
        drive_unstable_after_composing,
        "UnionTick.verify: UnionUnstableError",
    ),
    (
        "a roster that changed underneath",
        None,
        RecordingPublisher,
        drive_roster_change,
        "ScopeUnionCoordinator.verify.validate_roster: UnionHeadReadError: "
        "the scope union roster changed during verification",
    ),
    (
        "a scope with no participating lane",
        None,
        RecordingPublisher,
        drive_no_participating_lane,
        "ScopeUnionCoordinator._roster: UnionHeadReadError: "
        "the scope contains no participating lane to compose",
    ),
    (
        "a lane recording no deliverable ref",
        None,
        RecordingPublisher,
        drive_no_deliverable_ref,
        "ScopeUnionCoordinator._lane_branch: UnionHeadReadError: "
        "f'{reason}: {issue_key}'",
    ),
    (
        "a lane recording two deliverable refs",
        None,
        RecordingPublisher,
        drive_two_deliverable_refs,
        "ScopeUnionCoordinator._lane_branch: UnionHeadReadError: "
        "f'{reason}: {issue_key}'",
    ),
    (
        "a branch absent from the remote",
        None,
        RecordingPublisher,
        drive_absent_branch,
        "UnionTick._read_heads: UnionHeadReadError: "
        "the planned branch is absent on the remote",
    ),
    (
        "a head that does not read",
        None,
        UnreadableHeads,
        drive_unreadable_head,
        "UnionTick._read_heads: UnionHeadReadError: "
        "the planned branch has no readable commit identity",
    ),
    (
        "a remote that does not fetch",
        None,
        UnfetchableRemote,
        drive_unfetchable_remote,
        "UnionTick._fetch: UnionHeadReadError: "
        "the configured remote could not be fetched",
    ),
    (
        "a repository substituting objects",
        None,
        SubstitutedObjects,
        drive_substituted_objects,
        "require_union_object_identity: UnionHeadReadError: "
        "the repository substitutes Git objects behind union commit names",
    ),
    (
        "a replacement namespace that does not read",
        None,
        UnreadableReplacements,
        drive_unreadable_replacements,
        "require_union_object_identity: UnionHeadReadError: "
        "the repository's Git replacement namespace is unreadable",
    ),
    (
        "unchanged heads asked twice",
        None,
        RecordingPublisher,
        drive_cached_reuse,
        None,
    ),
    (
        "a merge conflict asked twice",
        CONFLICTING_EDITS,
        RecordingPublisher,
        partial(drive_cached_reuse, outcome=UnionOutcome.RED, conflict=True),
        None,
    ),
    (
        "a failing chain asked twice",
        None,
        RecordingPublisher,
        partial(drive_cached_reuse, outcome=UnionOutcome.RED, checks=FAILING_CHECKS),
        None,
    ),
)


def test_every_raise_the_step_writes_is_an_exit_some_scenario_drives() -> None:
    """The exit table is the step's own raise statements, read, not remembered.

    Not parametrised, so an empty census fails here by itself: a scan that
    found no raise would otherwise make every row's site claim vacuous.
    """
    assert EXIT_SITES, "the union modules write no raise statement"
    named = {row[4] for row in EXIT_SCENARIOS if row[4] is not None}
    assert named == set(EXIT_SITES), sorted(named ^ set(EXIT_SITES))


@pytest.mark.parametrize(
    "name, edits, publisher, drive, site",
    EXIT_SCENARIOS,
    ids=[row[0] for row in EXIT_SCENARIOS],
)
async def test_named_union_exit_preserves_real_refs_and_removes_scratch(
    tmp_path,
    name,
    edits,
    publisher,
    drive,
    site,
):
    fixture = await build_delivery(tmp_path / "world", edits=edits, git=publisher())
    before = await fixture.refs()

    raised = await drive(fixture)

    if site is not None:
        assert raised is not None, name
        path, lines = EXIT_SITES[site]
        assert {(path, line) for line in lines} & passed_through(raised), (name, site)
    assert fixture.undeclared_reads() == {}, name
    assert fixture.unrowed_asks() == {}, name
    assert fixture.built, name
    assert allowed_as(fixture.git) == "port", name
    assert fixture.refused_holdings() == [], name
    assert fixture.git.publications == [], name
    assert await fixture.refs() == before, name
    assert fixture.git.created == fixture.git.removed, name
    assert all(not Path(path).exists() for path in fixture.git.created), name
    assert (
        await pinned.git(fixture.observer, "worktree", "list", "--porcelain")
    ).count("worktree ") == 1


#: Every way verifying leaves because a port it asks failed: each member the
#: step asks of a port, failing from its first call.  Which members those are
#: is not remembered here.  Every exit, failure and return row requires that
#: each member the step asked on it has a row in this table
#: (``Fixture.unrowed_asks``), so a member the step starts asking fails there
#: until a row makes it fail.  ``is_repo`` is asked only once creating the
#: scratch tree has failed, so its row fails both, and the row is about the
#: last one.  Stated bound: a member failing on a later call than its first
#: (a second lane's merge, the head read after composing) is not a row.
COLLABORATOR_FAILURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tracker", ("require_scope_plan_reads",)),
    ("tracker", ("scope_issues",)),
    # ``read_planning_issue`` is asked only for a blocker outside the scope,
    # which this board has none of, since the plan is built from one read
    # of the family (KOD-1241, 2026-09-24).
    ("tracker", ("read_criteria",)),
    ("refs", ("work_refs",)),
    ("git", ("remote_branch_sha",)),
    ("git", ("has_replace_refs",)),
    ("git", ("fetch",)),
    ("git", ("create_worktree",)),
    ("git", ("create_worktree", "is_repo")),
    ("git", ("merge_scratch_head",)),
    ("git", ("current_sha",)),
    ("git", ("remove_worktree",)),
    ("runner", ("run_chain",)),
)

#: What each port fails with when a row plants its failure: the kind of error
#: its implementations raise.
PLANTED_ERRORS: dict[str, Callable[[str], BaseException]] = {
    "tracker": lambda member: TransientAPIError(f"{member}: the tracker failed"),
    "refs": lambda member: TransientAPIError(f"{member}: the ref read failed"),
    "git": lambda member: GitOperationError(f"{member}: git failed"),
    "runner": lambda member: CheckChainExecutionError(
        cwd="", step_name=None, reason=f"{member}: the runner failed"
    ),
}

#: What verifying can leave by when a planted failure is the error behind it:
#: the planted error itself, or the step's refusal raised from it.
LEAVING_ERRORS: tuple[type[BaseException], ...] = (
    TransientAPIError,
    GitOperationError,
    CheckChainExecutionError,
    UnionHeadReadError,
)


def test_every_failure_row_plants_a_member_its_port_declares() -> None:
    """Not parametrised: the table is not empty, and each row is well formed.

    Each row names a port the step is handed, members that port declares,
    and a port with an error to fail with.
    """
    assert COLLABORATOR_FAILURES
    assert set(PLANTED_ERRORS) == set(PORT_PARAMETERS)
    for parameter, members in COLLABORATOR_FAILURES:
        assert members, parameter
        assert set(members) <= declared(PORT_PARAMETERS[parameter]), members


@pytest.mark.parametrize(
    "parameter, members",
    COLLABORATOR_FAILURES,
    ids=[
        f"{parameter}.{'+'.join(members)}"
        for parameter, members in COLLABORATOR_FAILURES
    ],
)
async def test_a_failing_collaborator_leaves_every_ref_and_publishes_nothing(
    tmp_path, parameter, members
):
    """Verifying leaves when a port it asks fails, and publishes nothing then either.

    The row proves its exit from the error it caught: the error planted on
    the row's last member is that error or behind it.  On that exit no port
    was asked a member it does not declare, every member asked has a row,
    the step holds nothing it may not hold, nothing was asked to publish and
    every ref is where it was.
    """
    fixture = await build_delivery(tmp_path / "world", git=RecordingPublisher())
    before = await fixture.refs()

    with pytest.raises(LEAVING_ERRORS) as caught:
        await fixture.coordinator(failing={parameter: members}).verify()

    planted = {id(error) for member, error in fixture.planted if member == members[-1]}
    assert planted, members
    assert planted & {id(error) for error in errors_behind(caught.value)}, members
    assert fixture.undeclared_reads() == {}, members
    assert fixture.unrowed_asks() == {}, members
    assert fixture.refused_holdings() == [], members
    assert fixture.git.publications == [], members
    assert await fixture.refs() == before, members


async def test_the_ports_the_step_is_handed_record_what_it_asks(tmp_path):
    """Guards the record every scenario above reads: it is wired, and it is live.

    Not parametrised.  The ports are read off the constructor and pinned, so
    an empty read fails here; a green verify must record a read on each of
    them, so a fixture that stopped wrapping them fails here rather than
    passing every scenario vacuously; and what a declared read hands back is
    not the port's own bound method, whose ``__self__`` is the port.
    """
    assert PORT_PARAMETERS == {
        "tracker": protocols.ScopePlanReader,
        "refs": protocols.WorkRefReader,
        "git": protocols.GitService,
        "runner": protocols.CheckChainRunner,
    }
    fixture = await build_delivery(tmp_path / "world")

    await drive_green(fixture)

    for name, port in PORT_PARAMETERS.items():
        assert fixture.asked(name), name
        assert fixture.asked(name) <= declared(port), name
    assert getattr(Asked(fixture.git).fetch, "__self__", None) is None


#: The member the spellings below read, assembled so no scan could see it.
ASSEMBLED = "open" + "_pr_for_head"

#: ``getattr`` under another name, as an aliased read spells it.
LOOK = getattr

#: Every ordinary spelling of reading a member by name.  The record keys on
#: the read that executed, so each must land on it the same way.
READ_SPELLINGS = (
    ("a dot", lambda port: port.open_pr_for_head),
    ("hasattr", lambda port: hasattr(port, ASSEMBLED)),
    ("getattr with an assembled name", lambda port: getattr(port, ASSEMBLED, None)),
    ("getattr through an alias", lambda port: LOOK(port, ASSEMBLED)),
    ("a starred getattr", lambda port: getattr(*(port, ASSEMBLED, None))),
    ("operator.attrgetter", lambda port: operator.attrgetter(ASSEMBLED)(port)),
    ("operator.methodcaller", lambda port: operator.methodcaller(ASSEMBLED)(port)),
    ("__getattribute__", lambda port: port.__getattribute__(ASSEMBLED)),
)


@pytest.mark.parametrize(
    "spelling, read", READ_SPELLINGS, ids=[row[0] for row in READ_SPELLINGS]
)
def test_a_read_by_any_spelling_is_on_the_record(spelling, read) -> None:
    """Each spelling a static scan misses, read through the recorder."""
    proxy = Asked(RecordingPublisher())

    with suppress(AttributeError):
        read(proxy)

    assert ASSEMBLED in asked_of(proxy), spelling


#: Every way back to the port through what the recorder hands out: one
#: attribute past a declared read, or its state.  Each is an ordinary
#: spelling, so each must be refused before it reaches the unrecorded port.
ESCAPES = (
    ("a declared method's __self__", lambda port: port.fetch.__self__),
    ("a declared method's __func__", lambda port: port.fetch.__func__),
    ("a declared method's __wrapped__", lambda port: port.fetch.__wrapped__),
    (
        "a declared method's closure",
        lambda port: port.fetch.__closure__[0].cell_contents,
    ),
    ("the forwarded member's own slot", lambda port: port.fetch._call),
    ("the forwarded member's state", lambda port: port.fetch.__getstate__()),
    ("a copy of the forwarded member", lambda port: copy.copy(port.fetch)),
    ("the recorder's state", lambda port: port.__getstate__()),
    ("a copy of the recorder", lambda port: copy.copy(port)),
)


@pytest.mark.parametrize("escape, read", ESCAPES, ids=[row[0] for row in ESCAPES])
def test_nothing_the_recorder_hands_out_leads_back_to_the_port(escape, read) -> None:
    """Each route one hop past a declared read is refused before the port."""
    proxy = Asked(RecordingPublisher())

    with pytest.raises((AttributeError, TypeError)):
        read(proxy)

    assert asked_of(proxy) in {(), ("fetch",)}, escape


def test_reading_the_recorders_own_slots_is_not_recorded() -> None:
    """The recorder's stated limit, held as unseen: ``object.__getattribute__``.

    Reading the proxy's slot past its own ``__getattribute__`` reaches the
    port and leaves nothing on the record.  That is deliberate evasion, out
    of reach and named in the recorder's docstring.
    """
    port = RecordingPublisher()
    proxy = Asked(port)

    assert object.__getattribute__(proxy, "_port") is port
    assert asked_of(proxy) == ()


async def test_a_read_the_port_does_not_declare_is_reported(tmp_path) -> None:
    """Guards every scenario's ``undeclared_reads() == {}``: it can report one.

    Not parametrised.  One undeclared read through the git port a step was
    handed, and the report names it, on that port and on no other.
    """
    fixture = await build_delivery(tmp_path / "world")
    fixture.coordinator()

    assert fixture.undeclared_reads() == {}

    getattr(dict(fixture.handed)["git"], "close_pull_request", None)

    assert fixture.undeclared_reads() == {"git": ["close_pull_request"]}


async def test_a_member_no_failure_row_fails_is_reported(tmp_path) -> None:
    """Guards every row's ``unrowed_asks() == {}``: it can report one.

    Not parametrised.  One read of a member the git port declares and no row
    fails, and the report names it, on that port alone.
    """
    fixture = await build_delivery(tmp_path / "world")
    fixture.coordinator()

    assert fixture.unrowed_asks() == {}

    getattr(dict(fixture.handed)["git"], "push", None)

    assert fixture.unrowed_asks() == {"git": ["push"]}


#: A branch no repository in the fixture has, so the merge and the deletion
#: planted below are refused by git the moment they are tried: the control is
#: about what reaches the record, and a publication that LANDED would change
#: the world the other cases measure.
MISSING_BRANCH = "no/such/branch"


async def test_every_planted_publication_kind_is_on_the_record(tmp_path, monkeypatch):
    """Both witnesses above are live, for each publication the port declares.

    Planted on the method the green path calls once over the scratch tree,
    through the very port the step holds, in the best-effort shape the
    record's refutation used; the swallow hides nothing from a port that
    remembers being asked, and the ref the push moved is on the remote.

    All three publishing methods get a control, not just the push: a record
    that only ever proves it can see a push says nothing about a merge or a
    deletion arriving by the same route.  The merge and the deletion name a
    branch that does not exist, so each is refused and each is on the record
    anyway — which is the refused-publication property, exercised here on the
    two kinds a swallowing caller would use.
    """
    fixture = await build_delivery(tmp_path / "world", git=RecordingPublisher())
    before = await fixture.refs()
    scratch_sha = UnionComposition._scratch_sha

    async def publishing(self, worktree):
        with suppress(Exception):
            await self._git.push(worktree, "union")
        with suppress(Exception):
            await self._git.merge_branch(worktree, MISSING_BRANCH)
        with suppress(Exception):
            await self._git.delete_remote_branch(worktree, "upstream", MISSING_BRANCH)
        return await scratch_sha(self, worktree)

    monkeypatch.setattr(UnionComposition, "_scratch_sha", publishing)

    result = await fixture.coordinator().verify()

    assert result.outcome is UnionOutcome.GREEN
    assert [(kind, branch) for kind, _, branch in fixture.git.publications] == [
        ("push", "union"),
        ("merge_branch", MISSING_BRANCH),
        ("delete_remote_branch", f"upstream/{MISSING_BRANCH}"),
    ]
    assert all(cwd in fixture.git.created for _, cwd, _ in fixture.git.publications)
    assert await fixture.refs() != before
    assert "refs/heads/union" in await pinned.git(fixture.remote, "show-ref")
    assert f"refs/heads/{MISSING_BRANCH}" not in await pinned.git(
        fixture.remote, "show-ref"
    )
    assert fixture.git.removed == fixture.git.created


async def test_a_publication_git_refuses_is_on_the_record_all_the_same(tmp_path):
    """The record is appended BEFORE the call, and that is what makes it a witness.

    A refused publication moves no ref, so the ref comparison above is blind
    to it: the only thing that can say the step asked is the record.  Append
    it after the call instead and both witnesses go blind together on exactly
    the publication a best-effort caller is likeliest to leave behind.
    """
    fixture = await build_delivery(tmp_path / "world")
    tree = fixture.observer
    before = await fixture.refs()
    publisher = RecordingPublisher()

    with pytest.raises(GitOperationError):
        await publisher.push(str(tree), "bad..ref")

    assert publisher.publications == [("push", str(tree), "bad..ref")]
    assert await fixture.refs() == before
