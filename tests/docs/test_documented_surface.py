"""The documented surface is checked against the shipped one, at every run.

Documentation drift is not caught by review — it is caught by asking the
code what it ships and asking the documents what they claim, then requiring
the two sets to be equal.  Every assertion here derives BOTH sides: neither
the field list, the route list nor the version number is written down twice.

A new ``AppConfig`` field, a new route, or a release bump makes this module
red until the document catches up.  That is the whole mechanism.
"""

import json
import re
import tomllib
from pathlib import Path

from fastapi.routing import APIRoute

from kodezart.core.config import AppConfig
from kodezart.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGURATION_DOC = REPO_ROOT / "docs" / "configuration.md"
API_DOC = REPO_ROOT / "docs" / "api.md"
README = REPO_ROOT / "README.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

ENV_PREFIX = "KODEZART_"

#: Routes FastAPI mounts that are not part of the documented API surface:
#: the generated schema and its two Swagger UIs, which ``docs/api.md``
#: describes by their configuration knob rather than as endpoints.
_UNDOCUMENTED_BY_DESIGN: frozenset[str] = frozenset(
    {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"},
)

#: Each GitHub REST path shape the forge adapter calls, mapped to the
#: fine-grained token permission that authorises it.  ``README.md`` states
#: the permission set an operator must grant; this table is what makes that
#: statement checkable.  A new path shape under ``src/`` that this table
#: does not name fails the coverage test below.
_FORGE_PATH_PERMISSIONS: dict[str, str] = {
    "/repos/{}/{}": "Metadata: read",
    "/repos/{}/{}/pulls": "Pull requests: read/write",
    "/repos/{}/{}/issues/{}/comments": "Pull requests: read/write",
    "/repos/{}/{}/commits/{}/check-runs": "Actions: read",
    "/repos/{}/{}/actions/workflows": "Actions: read",
}


def _declared_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        project: dict[str, str] = tomllib.load(handle)["project"]
    return project["version"]


def _shipped_config_variables() -> set[str]:
    return {f"{ENV_PREFIX}{name.upper()}" for name in AppConfig.model_fields}


def _config_variables_named_in(path: Path) -> set[str]:
    return set(re.findall(rf"{ENV_PREFIX}[A-Z0-9_]+", path.read_text(encoding="utf-8")))


def _documented_endpoints() -> set[tuple[str, str]]:
    """``(method, normalised path)`` for every ``## METHOD /path`` heading."""
    headings = re.findall(
        r"^## ([A-Z]+) (/\S*)$",
        API_DOC.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    return {(method, _normalise_path(path)) for method, path in headings}


def _mounted_endpoints() -> set[tuple[str, str]]:
    endpoints: set[tuple[str, str]] = set()
    for route in create_app().routes:
        if not isinstance(route, APIRoute) or route.path in _UNDOCUMENTED_BY_DESIGN:
            continue
        for method in route.methods:
            if method == "HEAD":
                continue
            endpoints.add((method, _normalise_path(route.path)))
    return endpoints


def _normalise_path(path: str) -> str:
    """Erase path-parameter names so ``{job_id}`` and ``{jobId}`` compare equal."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def _forge_rest_paths() -> set[str]:
    """Every ``/repos/...`` path shape called from ``src/``, parameters erased."""
    paths: set[str] = set()
    for source in sorted((REPO_ROOT / "src").rglob("*.py")):
        for literal in re.findall(
            r"[\"'](/repos/[^\"'\s]*)[\"']",
            source.read_text(encoding="utf-8"),
        ):
            paths.add(_normalise_path(literal))
    return paths


def test_every_shipped_config_field_is_documented() -> None:
    """A field the code reads that the reference does not name is invisible."""
    undocumented = _shipped_config_variables() - _config_variables_named_in(
        CONFIGURATION_DOC,
    )
    assert undocumented == set()


def test_no_document_names_a_config_variable_that_does_not_exist() -> None:
    """``extra='forbid'`` makes a documented-but-absent variable a boot failure."""
    shipped = _shipped_config_variables()
    for path in (CONFIGURATION_DOC, API_DOC, README, ENV_EXAMPLE):
        assert _config_variables_named_in(path) - shipped == set(), path.name


def test_env_example_assigns_only_real_variables() -> None:
    """The file is copied to ``.env`` verbatim, so an unknown key aborts boot."""
    assigned = {
        line.split("=", 1)[0].strip()
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    assert assigned <= _shipped_config_variables()


def test_env_example_values_load() -> None:
    """Every assignment in the example is a value the field actually accepts."""
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, raw = line.split("=", 1)
        values[key.strip().removeprefix(ENV_PREFIX).lower()] = raw.strip()
    coerced: dict[str, object] = {
        name: json.loads(raw)
        if AppConfig.model_fields[name].annotation not in (str, str | None)
        and raw.startswith(("{", "["))
        else raw
        for name, raw in values.items()
    }
    AppConfig(**coerced)  # type: ignore[arg-type]


def test_every_mounted_endpoint_is_documented() -> None:
    assert _mounted_endpoints() - _documented_endpoints() == set()


def test_no_documented_endpoint_is_absent_from_the_app() -> None:
    assert _documented_endpoints() - _mounted_endpoints() == set()


def test_the_api_reference_health_sample_carries_the_shipped_version() -> None:
    """``docs/api.md``'s sample response is a mirror, and mirrors drift."""
    sample = re.search(
        r'"version": "([0-9][^"]*)"',
        API_DOC.read_text(encoding="utf-8"),
    )
    assert sample is not None
    assert sample.group(1) == _declared_version()


def test_every_forge_rest_path_is_covered_by_a_documented_permission() -> None:
    """The PAT scopes ``README.md`` asks for must cover what the code calls."""
    assert _forge_rest_paths() <= set(_FORGE_PATH_PERMISSIONS)


def test_the_readme_names_every_permission_the_forge_calls_require() -> None:
    readme = README.read_text(encoding="utf-8")
    required = {
        _FORGE_PATH_PERMISSIONS[path]
        for path in _forge_rest_paths()
        if path in _FORGE_PATH_PERMISSIONS
    }
    missing = [
        permission
        for permission in sorted(required)
        if permission.split(":")[0] not in readme
    ]
    assert missing == []
