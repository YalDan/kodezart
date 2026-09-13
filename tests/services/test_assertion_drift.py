"""Real committed source changes produce evidence even when both suites pass."""

import asyncio
import os
import subprocess
import sys

import pytest

from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.core.protocols import GitSourceReader
from kodezart.domain.errors import AssertionComparisonError, GitSourceReadError
from kodezart.services.assertion_drift import AssertionDriftDetector
from kodezart.types.domain.assertion_drift import ProtectedTestRef

PATH = "tests/test_contract.py"
SOURCE = "owning-ruling/native-comment"
PROTECTED = ProtectedTestRef(
    source_ref=SOURCE, path=PATH, qualified_name="test_contract"
)


def git(cwd, *args):
    return (
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
        .stdout.decode()
        .strip()
    )


def source(value):
    return (
        f"def implementation():\n    return {value}\n\n"
        f"def test_contract():\n    assert implementation() == {value}\n"
    )


def commit(repo, body):
    (repo / PATH).write_text(body)
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "fixture source")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "tests").mkdir()
    return repo


def run_protected_test(repo):
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", PATH],
        cwd=repo,
        env={
            **os.environ,
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "1 passed" in completed.stdout


async def compare(repo, graded, **changes):
    return await AssertionDriftDetector(git=SubprocessGitSourceReader()).compare(
        repo_path=str(repo),
        graded_sha=graded,
        **{"head_ref": "HEAD", "protected_tests": (PROTECTED,), **changes},
    )


async def test_both_revisions_are_green_but_expected_value_drift_emits_claim(repo):
    graded = commit(repo, source(1))
    run_protected_test(repo)
    head = commit(repo, source(2))
    run_protected_test(repo)
    before = (
        git(repo, "status", "--porcelain"),
        git(repo, "symbolic-ref", "HEAD"),
        (repo / PATH).read_bytes(),
    )
    (claim,) = await compare(repo, graded)
    assert claim.protected_test == PROTECTED
    assert claim.graded_sha == graded and claim.head_sha == head
    assert claim.graded_blob_sha == git(repo, "rev-parse", f"{graded}:{PATH}")
    assert claim.head_blob_sha == git(repo, "rev-parse", f"{head}:{PATH}")
    assert claim.before[0].expression == "implementation() == 1"
    assert claim.after[0].expression == "implementation() == 2"
    assert before == (
        git(repo, "status", "--porcelain"),
        git(repo, "symbolic-ref", "HEAD"),
        (repo / PATH).read_bytes(),
    )
    assert type(claim).model_validate_json(claim.model_dump_json()) == claim


async def test_adding_another_test_does_not_change_the_protected_assertion(repo):
    graded = commit(repo, source(1))
    commit(repo, source(1) + "\ndef test_added():\n    assert True\n")
    assert await compare(repo, graded) == ()


async def test_uncommitted_test_bytes_cannot_replace_either_git_object(repo):
    graded = commit(repo, source(1))
    head = commit(repo, source(2))
    (repo / PATH).write_text(source(1))
    (claim,) = await compare(repo, graded)
    assert claim.head_sha == head
    assert claim.after[0].expression == "implementation() == 2"
    assert (repo / PATH).read_text() == source(1)


async def test_head_is_pinned_before_source_reads_even_when_branch_advances(repo):
    graded = commit(repo, source(1))
    head = commit(repo, source(2))

    class AdvancingReader(SubprocessGitSourceReader):
        moved = False

        async def read_source(self, **kwargs):
            if not self.moved:
                self.moved = True
                commit(repo, source(3))
            return await super().read_source(**kwargs)

    (claim,) = await AssertionDriftDetector(git=AdvancingReader()).compare(
        repo_path=str(repo),
        graded_sha=graded,
        head_ref="HEAD",
        protected_tests=(PROTECTED,),
    )
    assert claim.head_sha == head
    assert claim.after[0].expression == "implementation() == 2"
    assert git(repo, "rev-parse", "HEAD") != head


async def test_git_replace_objects_cannot_rewrite_the_graded_evidence(repo):
    graded = commit(repo, source(1))
    head = commit(repo, source(2))
    git(repo, "replace", graded, head)
    (claim,) = await compare(repo, graded)
    assert claim.before[0].expression == "implementation() == 1"
    assert claim.after[0].expression == "implementation() == 2"


@pytest.mark.parametrize(
    "damage",
    [
        "missing-file",
        "missing-test",
        "duplicate-test",
        "bad-python",
        "no-original-assertions",
    ],
)
async def test_unreadable_or_ambiguous_protection_never_becomes_a_clean_result(
    repo, damage
):
    graded = commit(
        repo,
        "def test_contract():\n    pass\n"
        if damage == "no-original-assertions"
        else source(1),
    )
    if damage == "missing-file":
        (repo / PATH).unlink()
        git(repo, "add", "--all")
        git(repo, "commit", "-qm", "remove source")
    elif damage == "missing-test":
        commit(repo, "def another():\n    assert True\n")
    elif damage == "duplicate-test":
        commit(repo, source(1) + "\ndef test_contract():\n    assert True\n")
    elif damage == "bad-python":
        commit(repo, "this is not Python : !!!")
    with pytest.raises(
        GitSourceReadError if damage == "missing-file" else AssertionComparisonError
    ):
        await compare(repo, graded)


async def test_removed_assertion_in_existing_test_is_a_deviation_claim(repo):
    graded = commit(repo, source(1))
    commit(repo, "def test_contract():\n    pass\n")
    (claim,) = await compare(repo, graded)
    assert len(claim.before) == 1 and claim.after == ()


async def test_empty_comparison_and_same_commit_are_explicit_quiet_inputs(repo):
    graded = commit(repo, source(1))
    assert await compare(repo, graded, protected_tests=()) == ()
    assert await compare(repo, graded, head_ref=graded) == ()


async def test_bare_repository_needs_no_worktree_to_compare(repo, tmp_path):
    graded = commit(repo, source(1))
    commit(repo, source(2))
    bare = tmp_path / "bare.git"
    git(tmp_path, "clone", "--bare", str(repo), str(bare))
    assert len(await compare(bare, graded)) == 1


@pytest.mark.parametrize("graded", ["HEAD", "abcd", "-h", "f" * 40])
async def test_invalid_or_missing_graded_commit_never_reads_current_head_as_default(
    repo, graded
):
    commit(repo, source(1))
    with pytest.raises((AssertionComparisonError, GitSourceReadError)):
        await compare(repo, graded)


async def test_duplicate_reference_refuses_without_reading(repo):
    class NeverRead:
        async def resolve_commit(self, **_kwargs):
            raise AssertionError("no read")

    with pytest.raises(AssertionComparisonError, match="duplicate"):
        await AssertionDriftDetector(git=NeverRead()).compare(
            repo_path=str(repo),
            graded_sha="a" * 40,
            head_ref="HEAD",
            protected_tests=(PROTECTED, PROTECTED),
        )


async def test_native_read_preserves_exact_bytes_and_refuses_symlink_or_tree(repo):
    raw = b'\n# coding: latin-1\n\nvalue = "caf\xe9"\r\n\n'
    (repo / PATH).write_bytes(raw)
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "raw source")
    sha = git(repo, "rev-parse", "HEAD")
    reader = SubprocessGitSourceReader()
    assert isinstance(reader, GitSourceReader)
    blob = await reader.read_source(cwd=str(repo), commit_sha=sha, path=PATH)
    assert blob.content == raw
    with pytest.raises(GitSourceReadError):
        await reader.read_source(
            cwd=str(repo), commit_sha=git(repo, "rev-parse", "HEAD^{tree}"), path=PATH
        )
    (repo / PATH).unlink()
    (repo / PATH).symlink_to("../../outside.py")
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "symlink")
    with pytest.raises(GitSourceReadError, match="regular file"):
        await reader.read_source(
            cwd=str(repo), commit_sha=git(repo, "rev-parse", "HEAD"), path=PATH
        )


@pytest.mark.parametrize("special", ["tests/test_[a].py", ":(glob)test_contract.py"])
async def test_literal_path_identity_and_option_ref_are_not_reinterpreted(
    repo, special
):
    commit(repo, source(1))
    (repo / special).write_bytes(b"literal\n")
    (repo / "tests/test_a.py").write_bytes(b"other\n")
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "literal paths")
    reader = SubprocessGitSourceReader()
    blob = await reader.read_source(
        cwd=str(repo), commit_sha=git(repo, "rev-parse", "HEAD"), path=special
    )
    assert blob.path == special and blob.content == b"literal\n"
    with pytest.raises(GitSourceReadError):
        await reader.resolve_commit(cwd=str(repo), ref="--help")


async def test_cancellation_propagates_without_a_partial_claim(repo, monkeypatch):
    graded = commit(repo, source(1))

    async def canceled(**_kwargs):
        raise asyncio.CancelledError

    reader = SubprocessGitSourceReader()
    monkeypatch.setattr(reader, "read_source", canceled)
    with pytest.raises(asyncio.CancelledError):
        await AssertionDriftDetector(git=reader).compare(
            repo_path=str(repo),
            graded_sha=graded,
            head_ref="HEAD",
            protected_tests=(PROTECTED,),
        )


@pytest.mark.parametrize("phase", ["spawn", "communicate"])
async def test_native_read_settles_process_after_repeated_cancellation(
    tmp_path, monkeypatch, phase
):
    original_spawn = asyncio.create_subprocess_exec
    entered = asyncio.Event()
    proceed = asyncio.Event()
    processes = []

    async def spawn(*_args, **kwargs):
        process = await original_spawn(
            sys.executable, "-c", "import time; time.sleep(60)", **kwargs
        )
        processes.append(process)
        original_communicate = process.communicate

        async def communicate():
            entered.set()
            result = await original_communicate()
            await proceed.wait()
            return result

        if phase == "spawn":
            entered.set()
            await proceed.wait()
        else:
            monkeypatch.setattr(process, "communicate", communicate)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(
        SubprocessGitSourceReader().resolve_commit(cwd=str(tmp_path), ref="HEAD")
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        proceed.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert len(processes) == 1
        assert processes[0].returncode is not None
    finally:
        proceed.set()
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def test_spawn_failure_remains_a_typed_read_refusal(tmp_path, monkeypatch):
    async def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("Git unavailable")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unavailable)
    with pytest.raises(GitSourceReadError, match="Git unavailable") as caught:
        await SubprocessGitSourceReader().resolve_commit(cwd=str(tmp_path), ref="HEAD")
    assert isinstance(caught.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize("field", ["commit_sha", "path"])
async def test_foreign_returned_blob_cannot_supply_comparison(repo, monkeypatch, field):
    graded = commit(repo, source(1))
    reader = SubprocessGitSourceReader()
    original = reader.read_source

    async def wrong_identity(**kwargs):
        blob = await original(**kwargs)
        return blob.model_copy(update={field: "foreign"})

    monkeypatch.setattr(reader, "read_source", wrong_identity)
    with pytest.raises(AssertionComparisonError, match="requested address"):
        await AssertionDriftDetector(git=reader).compare(
            repo_path=str(repo),
            graded_sha=graded,
            head_ref="HEAD",
            protected_tests=(PROTECTED,),
        )
