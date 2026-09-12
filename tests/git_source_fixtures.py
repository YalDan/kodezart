"""Real Git fixture helpers shared by immutable source boundary tests."""

import subprocess

import pytest

PATH = "tests/test_contract.py"


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
