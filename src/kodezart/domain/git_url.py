"""Git URL normalization and resolution — pure functions, no I/O."""

import re
from pathlib import PurePosixPath

_SHORTHAND_RE = re.compile(r"^(?P<owner>[a-zA-Z0-9\-_.]+)/(?P<repo>[a-zA-Z0-9\-_.]+)$")


def parse_repo_url(raw: str) -> str:
    """Normalize an HTTPS or file:// git URL. No shorthand handling."""
    if raw.startswith("https://"):
        url = raw.rstrip("/")
        if not url.endswith(".git"):
            url += ".git"
        return url
    if raw.startswith("file://"):
        return raw
    msg = f"Not a recognized git URL scheme: {raw}"
    raise ValueError(msg)


def resolve_repo_url(raw: str, base_url: str) -> str:
    """Resolve a repo reference to a full clone URL.

    Accepts full HTTPS/file:// URLs or owner/repo shorthand.
    Shorthand is expanded using base_url.
    """
    if raw.startswith(("https://", "file://")):
        return parse_repo_url(raw)
    match = _SHORTHAND_RE.match(raw)
    if match:
        host = base_url.rstrip("/")
        return f"{host}/{match.group('owner')}/{match.group('repo')}.git"
    msg = f"Not a valid repo URL or owner/repo shorthand: {raw}"
    raise ValueError(msg)


def extract_owner_repo(url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a git URL or owner/repo shorthand.

    Raises ValueError for file:// URLs or unrecognized formats.
    """
    match = _SHORTHAND_RE.match(url)
    if match:
        return (match.group("owner"), match.group("repo"))
    if url.startswith("file://"):
        msg = f"Cannot extract owner/repo from file:// URL: {url}"
        raise ValueError(msg)
    if url.startswith("https://"):
        # https://github.com/owner/repo.git -> ['github.com', 'owner', 'repo.git']
        without_scheme = url.split("//", maxsplit=1)[1].rstrip("/")
        parts = without_scheme.split("/")
        if len(parts) >= 3:
            owner = parts[1]
            repo = parts[2].removesuffix(".git")
            return (owner, repo)
    msg = f"Cannot extract owner/repo from URL: {url}"
    raise ValueError(msg)


def cache_dir_for_repo(base_cache_dir: str, clone_url: str) -> str:
    """Deterministic cache path from clone URL."""
    path = clone_url
    for prefix in ("https://", "file://"):
        path = path.removeprefix(prefix)
    path = path.removesuffix(".git")
    slug = path.replace("/", "--")
    return str(PurePosixPath(base_cache_dir) / slug)
