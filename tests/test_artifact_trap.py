"""The artifact-directory trap's own controls (KOD-96-AC-29, KOD-96-AC-30).

Each event kind the trap records is planted under the directory inside an
active trap and is recorded; the same acts outside a trap record nothing;
a path that only resembles the directory is not recorded; and the one
writer's own writes, driven through ``GitArtifactPersister.persist`` over a
real repository, are recorded and permitted while a read planted beside
them is not.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from kodezart.adapters.git.artifact_persister import GitArtifactPersister
from kodezart.adapters.git.bare_repo_cache import LocalBareRepoCache
from kodezart.adapters.git.service import SubprocessGitService
from kodezart.adapters.git.worktree_provider import GitWorktreeProvider
from kodezart.types.domain.persist import ArtifactPersistStatus
from tests.artifact_trap import (
    CARRIERS,
    DIRECTORY,
    Record,
    nothing_read_under_the_directory,
    permitted,
    trapped,
    unpermitted,
)

#: A file the planted acts reach, under the directory.
PROJECTED = "criteria.json"


@pytest.fixture
def projection(tmp_path: Path) -> Path:
    """A directory named as the artifact directory, holding one file."""
    directory = tmp_path / DIRECTORY
    directory.mkdir()
    (directory / PROJECTED).write_text("[]")
    return directory


def planted_acts(directory: Path) -> dict[str, Callable[[], object]]:
    """One act per recorded event kind, each on a path under *directory*."""
    target = directory / PROJECTED
    return {
        "open": lambda: target.read_text(),
        "os.listdir": lambda: os.listdir(directory),
        "os.scandir": lambda: list(os.scandir(directory)),
        "os.mkdir": lambda: (directory / "made").mkdir(),
        "os.remove": lambda: os.remove(target),
        "os.rename": lambda: os.rename(target, directory.parent / "moved.json"),
        "shutil.rmtree": lambda: shutil.rmtree(directory),
        "subprocess.Popen": lambda: subprocess.run(
            ["cat", str(target)], capture_output=True, check=False
        ),
        # The event ``os.system`` raises, raised as it raises it: the lint
        # refuses a shell call in this tree, and the hook sees only the event.
        "os.system": lambda: sys.audit("os.system", os.fsencode(f"test -e {target}")),
    }


EVENTS = [
    "open",
    "os.listdir",
    "os.scandir",
    "os.mkdir",
    "os.remove",
    "os.rename",
    "shutil.rmtree",
    "subprocess.Popen",
    "os.system",
]


@pytest.mark.parametrize("event", EVENTS)
def test_each_event_kind_under_the_directory_is_recorded(
    projection: Path, event: str
) -> None:
    act = planted_acts(projection)[event]
    with trapped() as records:
        act()
    assert event in {record.event for record in records}, records
    assert unpermitted(records), records


@pytest.mark.parametrize("event", EVENTS)
def test_the_same_act_outside_a_trap_records_nothing(
    projection: Path, event: str
) -> None:
    with trapped() as records:
        pass
    planted_acts(projection)[event]()
    assert records == []


def test_every_event_kind_the_trap_records_has_a_control() -> None:
    """The controls above cover the trap's whole list, and the list is not empty."""
    assert EVENTS
    assert set(EVENTS) == set(CARRIERS)


@pytest.mark.parametrize(
    "act",
    [
        pytest.param(
            lambda directory: _read_bytes_path(os.fsencode(directory / PROJECTED)),
            id="a-bytes-path",
        ),
        pytest.param(
            lambda directory: subprocess.run(
                ["/bin/sh", "-c", f"cat {DIRECTORY}/{PROJECTED}"],
                cwd=directory.parent,
                capture_output=True,
                check=False,
            ),
            id="a-shell-string",
        ),
        pytest.param(
            lambda directory: subprocess.run(
                ["git", "show", f"HEAD:{DIRECTORY}/{PROJECTED}"],
                cwd=directory.parent,
                capture_output=True,
                check=False,
            ),
            id="a-ref-colon-path-argument",
        ),
        pytest.param(
            lambda directory: _read_relative(directory.parent),
            id="a-path-relative-to-the-working-directory",
        ),
    ],
)
def test_the_spellings_a_static_walk_missed_are_recorded(
    projection: Path, act: Callable[[Path], object]
) -> None:
    with trapped() as records:
        act(projection)
    assert unpermitted(records), records


def _read_bytes_path(path: bytes) -> str:
    """Read the projection through a bytes path."""
    with open(path) as handle:
        return handle.read()


def _read_relative(root: Path) -> str:
    """Read the projection by a relative path from inside *root*."""
    previous = Path.cwd()
    os.chdir(root)
    try:
        return Path(DIRECTORY, PROJECTED).read_text()
    finally:
        os.chdir(previous)


def test_a_trapped_block_that_reads_under_the_directory_fails(
    projection: Path,
) -> None:
    """The wrapped modules' fixture refuses a read when its block ends."""
    with pytest.raises(AssertionError), nothing_read_under_the_directory():
        (projection / PROJECTED).read_text()


def test_a_segment_that_only_resembles_the_directory_is_not_recorded(
    tmp_path: Path,
) -> None:
    lookalike = tmp_path / f"{DIRECTORY}-backup"
    lookalike.mkdir()
    (lookalike / PROJECTED).write_text("[]")
    with trapped() as records:
        (lookalike / PROJECTED).read_text()
        os.listdir(lookalike)
    assert records == []


def test_an_act_outside_the_writer_is_not_permitted_even_when_it_writes(
    projection: Path,
) -> None:
    with trapped() as records:
        (projection / PROJECTED).write_text("[1]")
    [record] = records
    assert record == Record(
        event="open", values=record.values, writes=True, from_writer=False
    )
    assert not permitted(record)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A clone with one commit on ``main`` and a bare remote beside it."""
    repo, bare = tmp_path / "repo", tmp_path / "bare.git"
    _git("init", "--bare", str(bare), cwd=tmp_path)
    _git("clone", str(bare), str(repo), cwd=tmp_path)
    (repo / "README.md").write_text("init")
    _git("add", "README.md", cwd=repo)
    _git("-c", "user.name=t", "-c", "user.email=t@t.dev", "commit", "-m", "i", cwd=repo)
    _git("push", "-u", "origin", "HEAD:refs/heads/main", cwd=repo)
    return repo


def persister_over(root: Path) -> GitArtifactPersister:
    git = SubprocessGitService(remote="origin")
    return GitArtifactPersister(
        git=git,
        workspace=GitWorktreeProvider(
            git=git,
            cache=LocalBareRepoCache(git=git, base_dir=str(root / "cache")),
        ),
        committer_name="test",
        committer_email="t@t.dev",
    )


async def test_the_writers_persist_is_recorded_and_permitted(
    repository: Path, tmp_path: Path
) -> None:
    """The writer's flow, driven: its mkdir and writes, and nothing else.

    Persisted twice onto one branch, the second time over the projection the
    first one committed, so a read of what is already there has something
    to read.  Non-vacuity for every trapped flow: what the persister does
    under the directory is recorded, from its own module, and every record
    it makes is one of the permitted writes.
    """
    persister = persister_over(tmp_path)
    projections = [
        {"ticket.json": "{}", PROJECTED: "[]"},
        {"ticket.json": "{}", PROJECTED: '["again"]'},
    ]
    with trapped() as records:
        statuses = [
            await persister.persist(
                repo_path=str(repository),
                repo_url=None,
                branch="trapped-branch",
                base_branch="main",
                artifacts=artifacts,
            )
            for artifacts in projections
        ]
    assert statuses == [ArtifactPersistStatus.PERSISTED] * len(projections)
    assert unpermitted(records) == []
    made = [record for record in records if record.event == "os.mkdir"]
    written = [record for record in records if record.event == "open"]
    assert len(made) == len(projections)
    assert len(written) == sum(len(artifacts) for artifacts in projections)
    assert all(record.writes for record in written)
    assert all(record.from_writer for record in records)


def test_a_read_in_a_module_no_wrapped_flow_calls_is_not_recorded(
    projection: Path, tmp_path: Path
) -> None:
    """The stated limit: the trap sees what runs, and nothing else.

    A module holding a read under the directory is imported, and a trapped
    block runs beside it without calling it: nothing is recorded, because
    the read never happened.  The same read, called, is.
    """
    source = tmp_path / "unreached.py"
    source.write_text(
        "from pathlib import Path\n"
        "def satisfied(root):\n"
        f"    return (Path(root) / {DIRECTORY!r} / {PROJECTED!r}).read_text()\n"
    )
    spec = importlib.util.spec_from_file_location("unreached", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with trapped() as records:
        os.listdir(tmp_path)
    assert records == []
    with trapped() as records:
        module.satisfied(projection.parent)
    assert unpermitted(records), records
