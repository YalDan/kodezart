"""PR114 real-Git exit and ref-invariance scenarios.

The named scenarios exercise cleanup and publication invariance directly.
Which exits there ARE is read off the step's own source rather than
remembered: every ``raise`` statement in the union step's modules is a site
one scenario must name, and a scenario that names one proves it drove that
site from the traceback it caught, not from the wording of a message.  The
census says which exits exist; only the scenarios are runtime proof.
"""

import ast
import asyncio
import importlib
import inspect
import traceback
from contextlib import suppress
from functools import partial
from pathlib import Path

import pytest

from kodezart.adapters.git.check_chain import SubprocessCheckChainRunner
from kodezart.chains.delivery_coordinator import ScopeUnionCoordinator
from kodezart.config.app import AppConfig
from kodezart.domain.errors import (
    CheckChainExecutionError,
    GitOperationError,
    MergeConflictError,
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

#: The union step's own modules: where every exit of verifying is written.
UNION_MODULES: tuple[str, ...] = (
    "kodezart.chains.delivery_coordinator",
    "kodezart.services.union_tick",
    "kodezart.services.union_composition",
    "kodezart.services.union_identity",
)


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


def passed_through(error: BaseException) -> frozenset[tuple[str, int]]:
    """Every (file, line) *error* left a frame through, and the errors behind it.

    A traceback holds one entry per frame the error passed through, at the
    line it left that frame by, so the line of the raise statement that sent
    it is among them however the message reads.  A cancellation awaited from
    outside its task is a fresh error whose context is the one the step
    raised, so the context and the cause are followed too, bounded by the
    errors already read.
    """
    lines: set[tuple[str, int]] = set()
    seen: set[int] = set()
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        lines.update(
            (frame.filename, frame.lineno)
            for frame in traceback.extract_tb(current.__traceback__)
        )
        pending.extend((current.__cause__, current.__context__))
    return frozenset(lines)


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

    def coordinator(self, runner: object = None) -> ScopeUnionCoordinator:
        return ScopeUnionCoordinator(
            scope_kind=PROJECT.kind,
            tracker=self.tracker,
            refs=self.tracker,
            git=self.git,
            runner=runner
            or SubprocessCheckChainRunner(
                timeout=AppConfig().union_check_step_timeout_seconds
            ),
            context=self.context,
            config=AppConfig(),
            committer_name="Union Fixture",
            committer_email="union@example.invalid",
        )

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
    with pytest.raises(UnionHeadReadError, match="roster changed") as caught:
        await fixture.coordinator(runner).verify()
    return caught.value


async def drive_cancellation(fixture) -> BaseException:
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


#: Every way verifying leaves, each with the raise site it drives: a key of
#: EXIT_SITES, or None where it returns, or where what it raises is a
#: collaborator's own error passing through.  The census below requires the
#: named sites to be every raise the step's modules write, so an exit added
#: without a scenario fails there rather than standing unwitnessed.
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
    assert fixture.git.publications == [], name
    assert await fixture.refs() == before, name
    assert fixture.git.created == fixture.git.removed, name
    assert all(not Path(path).exists() for path in fixture.git.created), name
    assert (
        await pinned.git(fixture.observer, "worktree", "list", "--porcelain")
    ).count("worktree ") == 1


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
