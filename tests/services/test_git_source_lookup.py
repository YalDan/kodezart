"""Optional immutable source lookup separates measured absence from read failure."""

import pytest

from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.domain.errors import GitSourceReadError
from tests.services.test_assertion_drift import PATH, commit, git, source
from tests.services.test_assertion_drift import repo as repo


async def test_actual_deleted_file_is_absent_only_at_the_selected_revision(repo):
    reader = SubprocessGitSourceReader()
    baseline = commit(repo, source(1))
    (repo / PATH).unlink()
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "Delete source")
    head = git(repo, "rev-parse", "HEAD")
    before = await reader.find_source(cwd=str(repo), commit_sha=baseline, path=PATH)
    assert before.content == source(1).encode()
    assert before.commit_sha == baseline and before.path == PATH
    assert await reader.find_source(cwd=str(repo), commit_sha=head, path=PATH) is None
    with pytest.raises(GitSourceReadError, match="missing"):
        await reader.read_source(cwd=str(repo), commit_sha=head, path=PATH)


@pytest.mark.parametrize("damage", ["missing-commit", "directory", "symlink", "path"])
async def test_unreadable_or_unsupported_objects_are_never_missing(repo, damage):
    reader = SubprocessGitSourceReader()
    baseline = commit(repo, source(1))
    path = PATH
    if damage == "missing-commit":
        baseline = "0" * 40
    elif damage == "directory":
        path = "tests"
    elif damage == "path":
        path = "../outside.py"
    else:
        (repo / "link").symlink_to(PATH)
        git(repo, "add", "link")
        git(repo, "commit", "-qm", "Add symlink")
        baseline = git(repo, "rev-parse", "HEAD")
        path = "link"
    with pytest.raises(GitSourceReadError):
        await reader.find_source(cwd=str(repo), commit_sha=baseline, path=path)
