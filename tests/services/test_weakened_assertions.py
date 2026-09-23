"""The mark service asked directly, over real Git objects and a real board.

The end-to-end cases drive this through the native writer's guard, where the
gate passes everything and every designation is readable. The three arms that
cannot be reached that way — a gate that rewrites the mark, a designation the
two commits cannot be compared over, and a roster that designates nothing —
are driven here, with the comparison made against commits this module writes
itself.
"""

import pytest

from kodezart.adapters.git.source_reader import SubprocessGitSourceReader
from kodezart.domain.amendment import (
    AssertionWeakenedError,
    NativeWriteRefusalError,
)
from kodezart.domain.fire_spec import criterion_field_bodies
from kodezart.services.weakened_assertions import WeakenedAssertionMarks
from kodezart.types.domain.agent import RulingProtectedTestRef
from kodezart.types.domain.gating import (
    ContentClass,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
)
from kodezart.types.domain.scope import ScopeKind, ScopeRef
from kodezart.types.domain.surface import SurfaceKind, WritableSurface
from kodezart.types.domain.tracker import WorkflowStateKind
from tests.chains.test_native_fire import SUBJECT, tracker
from tests.fakes import PassThroughGate
from tests.services.test_native_amendments import git
from tests.services.test_scope_terminal import RewritingGate

HOLDER = "the-writing-job"
FIRST = "tests/test_first_boundary.py"
SECOND = "tests/test_second_boundary.py"
#: Two designated tests, each asserting two conditions, so a case can drop one
#: assertion from each and still tell the two marks apart by their own text.
BODIES = {
    FIRST: "def test_first():\n    assert answer() == 42\n    assert answer() > 0\n",
    SECOND: "def test_second():\n    assert seen() == 'a'\n    assert seen() != ''\n",
}
NAMES = {FIRST: "test_first", SECOND: "test_second"}


@pytest.fixture
async def repository(tmp_path):
    """One repository whose HEAD holds both designated tests, and its first sha."""
    repo = tmp_path / "repo"
    repo.mkdir()
    await git(repo, "init", "-b", "main")
    await git(repo, "config", "user.name", "Boundary test")
    await git(repo, "config", "user.email", "boundary@example.invalid")
    for path, body in BODIES.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "the designated boundary tests")
    return repo, await git(repo, "rev-parse", "HEAD")


def designation(path):
    return RulingProtectedTestRef(
        source_ref=f"record/{NAMES[path]}", path=path, qualified_name=NAMES[path]
    )


async def commit_weakened(repo, paths):
    """Drop each named test's second assertion and commit; return the new sha."""
    for path in paths:
        (repo / path).write_text("".join(BODIES[path].splitlines(keepends=True)[:2]))
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "weaken the designated tests")
    return await git(repo, "rev-parse", "HEAD")


def marks(tracker_port, *, gate=None, lease_seconds=900.0):
    return WeakenedAssertionMarks(
        tracker=tracker_port,
        source=SubprocessGitSourceReader(),
        gate=PassThroughGate() if gate is None else gate,
        lease_seconds=lease_seconds,
    )


async def refuse(service, repo, start_sha, commit_sha, designated):
    await service.refuse_weakening(
        repo_path=str(repo),
        lane_key=SUBJECT,
        start_sha=start_sha,
        commit_sha=commit_sha,
        designated=designated,
        holder=HOLDER,
        visibility=RepoVisibility.PUBLIC,
    )


def criterion_children(port):
    return {
        key: issue
        for key, issue in port.issues.items()
        if issue.parent_key == SUBJECT and "criterion" in issue.issue_labels
    }


async def test_a_gate_that_alters_the_mark_refuses_before_any_mint(repository):
    """An altered mark is a different claim, so nothing reaches the board.

    The refusal is this writer's own and carries no key, because the gate
    answered before any child could exist to name.
    """
    repo, start = repository
    port = tracker()
    before = criterion_children(port)
    head = await commit_weakened(repo, [FIRST])
    service = marks(
        port, gate=RewritingGate(verdict=GateVerdict.CLEAN, content="redacted")
    )

    with pytest.raises(AssertionWeakenedError) as caught:
        await refuse(service, repo, start, head, (designation(FIRST),))

    assert caught.value.marks == ()
    assert caught.value.lane_key == SUBJECT
    assert criterion_children(port) == before


@pytest.mark.parametrize(
    "reference,failure",
    [
        (
            RulingProtectedTestRef(
                source_ref="record/absent",
                path="tests/test_absent.py",
                qualified_name="test_absent",
            ),
            NativeWriteRefusalError,
        ),
        (
            RulingProtectedTestRef(
                source_ref="record/unresolved",
                path=FIRST,
                qualified_name="test_no_such_definition",
            ),
            NativeWriteRefusalError,
        ),
    ],
    ids=["missing file", "missing definition"],
)
async def test_an_uncomparable_designation_refuses_without_a_mark(
    repository, reference, failure
):
    """A designation the commits cannot be compared over fails closed.

    Nothing is known about the named test's assertions in either commit, so
    the refusal stands with no mark: an unreadable designation is not
    evidence that the assertions survived, and the caller's publication dies
    with it either way.
    """
    repo, start = repository
    port = tracker()
    before = criterion_children(port)
    head = await commit_weakened(repo, [FIRST])

    with pytest.raises(failure) as caught:
        await refuse(marks(port), repo, start, head, (reference,))

    assert not isinstance(caught.value, AssertionWeakenedError)
    assert criterion_children(port) == before


async def test_nothing_designated_reads_no_git_object_and_mints_nothing(repository):
    """An empty roster asks the repository nothing at all."""
    repo, start = repository
    port = tracker()
    before = criterion_children(port)
    head = await commit_weakened(repo, [FIRST])
    gate = PassThroughGate()

    class NoReader:
        async def resolve_commit(self, *, cwd, ref):
            raise AssertionError("an empty roster resolves no commit")

        async def read_source(self, *, cwd, commit_sha, path):
            raise AssertionError("an empty roster reads no source")

        async def find_source(self, *, cwd, commit_sha, filename):
            raise AssertionError("an empty roster searches no tree")

    service = WeakenedAssertionMarks(
        tracker=port, source=NoReader(), gate=gate, lease_seconds=900.0
    )

    await refuse(service, repo, start, head, ())

    assert gate.calls == []
    assert criterion_children(port) == before


async def test_two_tests_that_each_lost_an_assertion_mint_two_marks_under_one_lease(
    repository,
):
    """Two losses are two obligations, minted under one lease on one child set.

    The lease the board demands is the lane's own criterion child set held by
    the writing job, so the two children existing at all is the proof that
    both mints ran under it; what the lease was at each call is read off the
    board at the moment of the call rather than after the fact. The refusal
    carries both keys, in the order they were minted.
    """
    repo, start = repository
    port = tracker()
    before = criterion_children(port)
    head = await commit_weakened(repo, [FIRST, SECOND])
    gate = PassThroughGate()
    surface = WritableSurface(
        kind=SurfaceKind.CRITERION_CHILD_SET,
        ref=ScopeRef(kind=ScopeKind.ISSUE, key=SUBJECT),
    )
    held: list[list[WritableSurface]] = []
    original = port.create_criterion_if_absent

    async def recorded(**kwargs):
        held.append(
            [key for key, lease in port.leases.items() if lease.holder == HOLDER]
        )
        return await original(**kwargs)

    port.create_criterion_if_absent = recorded

    with pytest.raises(AssertionWeakenedError) as caught:
        await refuse(
            marks(port, gate=gate),
            repo,
            start,
            head,
            (designation(FIRST), designation(SECOND)),
        )

    minted = {
        key: issue
        for key, issue in criterion_children(port).items()
        if key not in before
    }
    assert len(minted) == 2
    assert caught.value.marks == tuple(minted)
    assert held == [[surface], [surface]]
    assert all(
        issue.state_kind is WorkflowStateKind.UNSTARTED for issue in minted.values()
    )
    # One mark per lost assertion, each naming its own test, its own record and
    # the condition that went; the assertion that survived appears in neither.
    checks = sorted(
        criterion_field_bodies(issue.body, field="Check")[0]
        for issue in minted.values()
    )
    assert len(checks) == 2
    for check, (path, lost, kept) in zip(
        checks,
        (
            (FIRST, "answer() > 0", "answer() == 42"),
            (SECOND, "seen() != ''", "seen() == 'a'"),
        ),
        strict=True,
    ):
        assert f"`{path}::{NAMES[path]}`" in check
        assert f"`record/{NAMES[path]}`" in check
        assert f"`{lost}`" in check
        assert kept not in check
        assert start not in check and head not in check
    assert gate.content_classes == [ContentClass.DERIVED] * 4
    assert (
        gate.destinations
        == [
            OutboundDestination.TRACKER_DESCRIPTION,
            OutboundDestination.TRACKER_TITLE,
        ]
        * 2
    )
