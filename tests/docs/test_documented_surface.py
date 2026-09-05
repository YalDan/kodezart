"""The documented surface is checked against the shipped one, at every run.

Documentation drift is not caught by review — it is caught by asking the
code what it ships and asking the documents what they claim, then requiring
the two sets to be equal.  Every assertion here derives BOTH sides: neither
the field list, the route list nor the version number is written down twice.

A new ``AppConfig`` field, a new route, or a release bump makes this module
red until the document catches up.  That is the whole mechanism.
"""

import ast
import json
import re
import tomllib
from pathlib import Path

from fastapi.routing import APIRoute

from kodezart.core.config import AppConfig
from kodezart.main import create_app
from kodezart.types.domain.agent import AgentEvent
from tests.docs.test_api_event_reference import SECTION as API_EVENT_HEADING

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGURATION_DOC = REPO_ROOT / "docs" / "configuration.md"
API_DOC = REPO_ROOT / "docs" / "api.md"
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture.md"
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


def _env_example_assignments() -> dict[str, object]:
    """Every uncommented assignment, keyed by field name and JSON-coerced."""
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, raw = line.split("=", 1)
        values[key.strip().removeprefix(ENV_PREFIX).lower()] = raw.strip()
    return {
        name: json.loads(raw)
        if AppConfig.model_fields[name].annotation not in (str, str | None)
        and raw.startswith(("{", "["))
        else raw
        for name, raw in values.items()
    }


def test_env_example_values_load() -> None:
    """Every assignment in the example is a value the field actually accepts."""
    AppConfig(**_env_example_assignments())  # type: ignore[arg-type]


def test_every_env_example_value_is_the_fields_shipped_default() -> None:
    """``README.md`` promises copying the file changes no behaviour.

    Compared after validation rather than as text, so ``30`` against a float
    default of ``30.0`` is equal — which is what "changes no behaviour"
    means — while a genuinely drifted value is not.  Nothing here restates
    a default: the expected side is the field's own, read off ``AppConfig()``.
    """
    defaults = AppConfig()
    drifted = [
        name
        for name, value in _env_example_assignments().items()
        if getattr(AppConfig(**{name: value}), name)  # type: ignore[arg-type]
        != getattr(defaults, name)
    ]

    assert drifted == []


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


def _documented_event_types() -> set[str]:
    """Every event name the API reference's SSE table carries."""
    section = API_DOC.read_text(encoding="utf-8").split("## SSE Event Types")[1]
    names: set[str] = set()
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 2 or cells[0] in {"Event Type", "---"}:
            continue
        if set(cells[0]) <= {"-", " "}:
            continue
        names.add(cells[0].strip("`"))
    return names


def _shipped_event_types() -> set[str]:
    """Every discriminator a concrete ``AgentEvent`` subclass declares.

    Read off the models rather than off the module text: a class whose
    ``type`` field the doc table forgot is exactly the drift this catches.
    """
    return {
        str(field.default)
        for model in _agent_event_models()
        if (field := model.model_fields.get("type")) is not None
        and field.default is not None
    }


def _agent_event_models() -> list[type[AgentEvent]]:
    subclasses: list[type[AgentEvent]] = []
    pending: list[type[AgentEvent]] = [AgentEvent]
    while pending:
        current = pending.pop()
        for child in current.__subclasses__():
            subclasses.append(child)
            pending.append(child)
    return subclasses


def test_every_shipped_sse_event_type_is_documented() -> None:
    """A new event model with no row in the reference fails the suite."""
    assert _shipped_event_types() - _documented_event_types() == set()


def test_no_documented_sse_event_type_is_absent_from_the_code() -> None:
    """The other direction: a row naming an event nothing emits is a lie."""
    assert _documented_event_types() - _shipped_event_types() == set()


def test_the_sse_event_table_has_rows_at_all() -> None:
    """Guards the two set comparisons: an unparsed table would pass both."""
    assert len(_documented_event_types()) > 10


def _attribute_reads_of(field_name: str) -> list[str]:
    """Every module under ``src/`` that reads ``<something>.<field_name>``."""
    sites: list[str] = []
    for source in sorted((REPO_ROOT / "src").rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Attribute) and node.attr == field_name
            for node in ast.walk(tree)
        ):
            sites.append(source.relative_to(REPO_ROOT).as_posix())
    return sites


def test_the_tracker_server_name_has_exactly_the_consumers_its_description_claims() -> (
    None
):
    """The field's description says two consumers — factory and record sink.

    An earlier claim — a consumer in session attachment — was contradicted
    by exactly this derivation and corrected; the tracker-side record sink
    then became a real second reader (KOD-170), and the description names
    it.  A reader appearing or vanishing makes this red until the
    description tells the truth again.
    """
    assert _attribute_reads_of("tracker_mcp_server_name") == [
        "src/kodezart/composition/records.py",
        "src/kodezart/composition/tracker.py",
    ]


def _documented_protocols() -> set[str]:
    """Every protocol named in the architecture doc's Protocol Map."""
    section = ARCHITECTURE_DOC.read_text(encoding="utf-8").split("## Protocol Map")[1]
    names: set[str] = set()
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] in {"Protocol", "---"}:
            continue
        if set(cells[0]) <= {"-", " "}:
            continue
        names.add(cells[0])
    return names


def _shipped_protocols() -> set[str]:
    """Every ``Protocol`` class ``core/protocols.py`` defines."""
    tree = ast.parse(
        (REPO_ROOT / "src" / "kodezart" / "core" / "protocols.py").read_text(
            encoding="utf-8",
        ),
    )
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases
        )
    }


def test_every_shipped_protocol_has_a_row_in_the_protocol_map() -> None:
    """The doc said "all 12" over a file that defines far more than twelve."""
    assert _shipped_protocols() - _documented_protocols() == set()


def test_no_documented_protocol_is_absent_from_the_port_module() -> None:
    """A row for a deleted protocol reads as a port the system still has."""
    assert _documented_protocols() - _shipped_protocols() == set()


# ---------------------------------------------------------------------------
# The class behind the "all 12 protocols" and "18 event types" instances
# ---------------------------------------------------------------------------

#: Sets whose size a shipped test above already derives from the code. A
#: number quantifying one of these in prose is a SECOND statement of a
#: value that already has a source of truth, and the second statement is
#: the one that goes stale — silently, because no test reads prose.
#:
#: A list of CLASSES, not of instances. `ead78c3` fixed the two instances a
#: findings list named and left the class in the file it named first; three
#: survived at head. What is enumerated here is the kind of noun a count may
#: not attach to, so a fourth instance fails rather than being rediscovered.
_DERIVED_SET_NOUNS: tuple[str, ...] = (
    "event types?",
    "events?",
    "protocol-based ports?",
    "protocols?",
    "ports?",
    "config(?:uration)? fields?",
    "fields?",
    "environment variables?",
    "endpoints?",
    "adapters?",
    "criteria",
)

_NOUNS = "|".join(_DERIVED_SET_NOUNS)

#: TWO shapes, because the claim reads both ways round and the first
#: pattern could see only one of them.  Digit first — a digit, up to two
#: adjectives, then one of those nouns: the window is what makes "12
#: protocol-based ports" and "18 SSE event types" both reachable without
#: degenerating into "any number near any word", and a hyphen separates as
#: a space does because the attributive form — "a 15-field reference" — is
#: the one this lane shipped and then had to delete.  Noun first, count
#: parenthesised after it — "Event Types (18 total)", "Workflow events (6)"
#: — which is the form three stale instances sat in, inside a file this
#: guard already scanned, while it passed.
_COUNTED_CLAIM = re.compile(
    r"\b\d[\d,]*[\s-]+(?:[A-Za-z][A-Za-z-]*[\s-]+){0,2}(?:" + _NOUNS + r")\b"
    r"|\b(?:" + _NOUNS + r")\s*\(\s*\d[\d,]*(?:[\s-]+[A-Za-z][A-Za-z-]*){0,2}\s*\)",
    re.IGNORECASE,
)

#: Every prose file a reader is pointed at. Not `rglob`: the check is about
#: authored prose, and a fixture or a golden that happens to contain a
#: number is not a claim anyone reads as documentation.
_PROSE_FILES: tuple[Path, ...] = (
    README,
    REPO_ROOT / "CONTRIBUTING.md",
    REPO_ROOT / "SECURITY.md",
    *sorted((REPO_ROOT / "docs").glob("*.md")),
)


def _counted_claims_in(path: Path) -> list[str]:
    """Every counted claim in *path*, minus the ones another guard recomputes.

    ``docs/api.md``'s ``### X Events (N)`` headings carry a count and are
    the one instance of this shape that is NOT a second statement: the
    event-reference guard derives each of those counts from the rows under
    it, so a stale one reddens there rather than surviving.  The exemption
    is expressed by importing that guard's own heading pattern, so it can
    never outlive the check it defers to.
    """
    claims: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if path == API_DOC and API_EVENT_HEADING.match(line) is not None:
            continue
        claims.extend(
            f"{path.relative_to(REPO_ROOT).as_posix()}:{number}: {match.group(0)!r}"
            for match in _COUNTED_CLAIM.finditer(line)
        )
    return claims


def test_no_prose_file_asserts_a_count_of_something_the_code_owns() -> None:
    """A count in prose is an assertion nothing recomputes."""
    claims = [claim for path in _PROSE_FILES for claim in _counted_claims_in(path)]

    assert claims == []


def test_the_counted_claim_pattern_matches_the_instances_it_was_written_for() -> None:
    """The sentences that were live at `f7ce6cc` and `b19afde`.

    A guard that cannot match the defect it names demonstrates nothing, so
    the shapes are stated rather than assumed — including the two-adjective
    form, which is why the window exists, and the parenthesised form, which
    is how three stale counts sat in ``docs/architecture.md`` inside this
    guard's own scan set while it passed.
    """
    for claim in (
        "SSE streaming of 18 event types for real-time progress visibility",
        "Hexagonal architecture with 12 protocol-based ports",
        "the full SSE event schema (18 event types)",
        "a 15-field reference",
        "All 12 protocols are listed below",
        "### Event Types (18 total)",
        "**Streaming events (11)**:",
        "**Workflow events (6)**:",
    ):
        assert _COUNTED_CLAIM.search(claim) is not None, claim


def test_the_only_exempt_counted_claims_are_the_ones_another_guard_derives() -> None:
    """Non-vacuity for the exemption, in both directions.

    The exempted lines really do carry the shape — so the exemption is
    doing work rather than describing lines the pattern never saw — and
    ``docs/api.md`` is clean once they are set aside, so the exemption is
    not hiding a second, undeserved instance beside them.
    """
    exempted = [
        line
        for line in API_DOC.read_text(encoding="utf-8").splitlines()
        if API_EVENT_HEADING.match(line) is not None
    ]

    assert exempted, "the guard those headings defer to has nothing to derive"
    for line in exempted:
        assert _COUNTED_CLAIM.search(line) is not None, line
    assert _counted_claims_in(API_DOC) == []


def test_the_counted_claim_pattern_leaves_ordinary_prose_alone() -> None:
    """A gate that flags ordinary writing gets turned off, and then protects nothing."""
    for innocent in (
        "Python 3.12+ is required",
        "released after 15 minutes against 24 hours by default",
        "retries 3 times before giving up",
        "the event types are tabulated below",
    ):
        assert _COUNTED_CLAIM.search(innocent) is None, innocent
