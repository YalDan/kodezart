import pytest
from kodezart.adapters.subprocess_git_source_reader import SubprocessGitSourceReader
from kodezart.core.protocols import GitSourceReader
from kodezart.domain.errors import GitSourceReadError
from tests.git_source_fixtures import PATH, git
from tests.git_source_fixtures import repo as repo

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
