"""The shipped version is one value, sourced once.

No release number is written here.  The project metadata is the single
source; every other surface is asserted to agree with it, so a bump is one
edit in ``pyproject.toml`` and a re-sync, and a literal anywhere else would
be a second source that can drift.
"""

import tomllib
from importlib.metadata import version as installed_version
from pathlib import Path

from httpx import AsyncClient

REPO_ROOT = Path(__file__).resolve().parents[1]


def declared_version() -> str:
    """The version ``pyproject.toml`` declares."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        project: dict[str, str] = tomllib.load(handle)["project"]
    return project["version"]


def test_the_installed_metadata_matches_the_declaration() -> None:
    """A stale environment would otherwise serve the previous release."""
    assert installed_version("kodezart") == declared_version()


def test_the_lock_file_records_the_declared_version() -> None:
    with (REPO_ROOT / "uv.lock").open("rb") as handle:
        lock = tomllib.load(handle)
    entry = next(
        package for package in lock["package"] if package["name"] == "kodezart"
    )
    assert entry["version"] == declared_version()


async def test_health_reports_the_declared_version(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.json()["data"]["version"] == declared_version()


def test_no_second_source_of_the_version_exists() -> None:
    """The declaration is the only place the number is written."""
    number = declared_version()
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in sorted((REPO_ROOT / "src").rglob("*.py"))
        if number in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
