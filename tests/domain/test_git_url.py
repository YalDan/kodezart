"""Tests for git URL utility functions."""

import pytest

from kodezart.domain.git_url import (
    cache_dir_for_repo,
    extract_owner_repo,
    parse_repo_url,
    resolve_repo_url,
)


def test_parse_repo_url_https_without_dot_git() -> None:
    assert parse_repo_url("https://github.com/o/r") == "https://github.com/o/r.git"


def test_parse_repo_url_https_trailing_slash() -> None:
    assert parse_repo_url("https://gitlab.com/o/r/") == "https://gitlab.com/o/r.git"


def test_parse_repo_url_https_already_dot_git() -> None:
    assert parse_repo_url("https://github.com/o/r.git") == "https://github.com/o/r.git"


def test_parse_repo_url_file_url() -> None:
    assert parse_repo_url("file:///tmp/repo") == "file:///tmp/repo"


def test_parse_repo_url_rejects_shorthand() -> None:
    with pytest.raises(ValueError, match="Not a recognized git URL scheme"):
        parse_repo_url("owner/repo")


def test_parse_repo_url_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="Not a recognized git URL scheme"):
        parse_repo_url("not-valid")


def test_resolve_repo_url_shorthand_github() -> None:
    result = resolve_repo_url("owner/repo", "https://github.com")
    assert result == "https://github.com/owner/repo.git"


def test_resolve_repo_url_shorthand_gitlab() -> None:
    result = resolve_repo_url("owner/repo", "https://gitlab.com")
    assert result == "https://gitlab.com/owner/repo.git"


def test_resolve_repo_url_shorthand_trailing_slash() -> None:
    result = resolve_repo_url("owner/repo", "https://github.com/")
    assert result == "https://github.com/owner/repo.git"


def test_resolve_repo_url_full_https() -> None:
    result = resolve_repo_url("https://github.com/o/r", "https://gitlab.com")
    assert result == "https://github.com/o/r.git"


def test_resolve_repo_url_file_url() -> None:
    result = resolve_repo_url("file:///tmp/repo", "https://github.com")
    assert result == "file:///tmp/repo"


def test_resolve_repo_url_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Not a valid repo URL"):
        resolve_repo_url("not-valid", "https://github.com")


def test_cache_dir_for_repo() -> None:
    result = cache_dir_for_repo("/cache", "https://github.com/o/r.git")
    assert result == "/cache/github.com--o--r"


# ---------------------------------------------------------------------------
# extract_owner_repo tests
# ---------------------------------------------------------------------------


def test_extract_owner_repo_https_with_dot_git() -> None:
    assert extract_owner_repo("https://github.com/owner/repo.git") == ("owner", "repo")


def test_extract_owner_repo_https_without_dot_git() -> None:
    assert extract_owner_repo("https://github.com/owner/repo") == ("owner", "repo")


def test_extract_owner_repo_shorthand() -> None:
    assert extract_owner_repo("owner/repo") == ("owner", "repo")


def test_extract_owner_repo_https_trailing_slash() -> None:
    assert extract_owner_repo("https://github.com/owner/repo/") == ("owner", "repo")


def test_extract_owner_repo_file_url_raises() -> None:
    with pytest.raises(ValueError, match="Cannot extract owner/repo from file://"):
        extract_owner_repo("file:///tmp/repo")


def test_extract_owner_repo_invalid_raises() -> None:
    with pytest.raises(ValueError, match="Cannot extract owner/repo"):
        extract_owner_repo("not-valid")
