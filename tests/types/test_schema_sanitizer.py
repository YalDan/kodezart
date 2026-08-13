"""KOD-91-AC-1, AC-2, AC-3 — the keyword stripper, and what the wire carries.

``sanitize_schema`` is unwired (KOD-134): every dispatched schema is its
model's own, constraints intact.  The stripper's guarantees are asserted
against the stripper, called explicitly on a model's schema — the goldens stay
byte-stable, and stripping a keyword still leaves client-side validation
untouched.  The wire's guarantee is the opposite one and is asserted here too:
a dispatched schema states the constraints its response is judged by, and no
dispatch site filters a schema or builds one of its own.
"""

import json
import re
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from kodezart.types.domain.agent import (
    WIRE_SCHEMAS,
    AcceptanceCriteriaOutput,
    BranchNameOutput,
    CommitMessageOutput,
    ContentAuditFinding,
    ContentAuditOutput,
    CriteriaValidationOutput,
    DraftCritiqueOutput,
    GeneratedCriteriaOutput,
    PRDescriptionOutput,
    TicketDraftOutput,
    TicketReviewOutput,
)
from kodezart.types.domain.criteria import CRITERION_ID_PATTERN
from kodezart.types.domain.wire_schema import (
    STRICT_MODE_KEYWORDS,
    WireSchemaError,
    sanitize_schema,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "kodezart"
GOLDENS = Path(__file__).parent / "schema_goldens"

#: Every ``output_format`` mapping in the sources names its schema by one of
#: the precomputed constants, or forwards the one its caller supplied. The
#: pattern reads the dispatch site as text, so a site that inlines a raw
#: ``model_json_schema()`` call is caught by the same check that catches a
#: site passing a schema through the stripper.
SCHEMA_ARGUMENT = re.compile(r'"schema"\s*:\s*([A-Za-z_][A-Za-z0-9_.()\[\]]*)')
SANITIZER_CALL = "sanitize_schema("
#: The one dispatch site whose schema is not a roster constant: the agent
#: endpoint forwards the schema its caller supplied, unaltered.
CALLER_SUPPLIED_SCHEMA = "request.output_schema"

#: The model each roster schema is derived from. The equality test below
#: parametrizes over ``WIRE_SCHEMAS`` and indexes this, so a roster entry
#: with no model here fails rather than going unchecked.
WIRE_MODELS: dict[str, type[BaseModel]] = {
    "COMMIT_MESSAGE_SCHEMA": CommitMessageOutput,
    "ACCEPTANCE_CRITERIA_SCHEMA": AcceptanceCriteriaOutput,
    "BRANCH_NAME_SCHEMA": BranchNameOutput,
    "GENERATED_CRITERIA_SCHEMA": GeneratedCriteriaOutput,
    "CRITERIA_VALIDATION_SCHEMA": CriteriaValidationOutput,
    "TICKET_DRAFT_SCHEMA": TicketDraftOutput,
    "TICKET_REVIEW_SCHEMA": TicketReviewOutput,
    "PR_DESCRIPTION_SCHEMA": PRDescriptionOutput,
    "CONTENT_AUDIT_SCHEMA": ContentAuditOutput,
    "DRAFT_CRITIQUE_SCHEMA": DraftCritiqueOutput,
}


def is_rostered_argument(argument: str) -> bool:
    """Whether a dispatch site's schema expression is one the roster covers."""
    return argument in WIRE_SCHEMAS or argument == CALLER_SUPPLIED_SCHEMA


def walk(node: object) -> list[dict[str, object]]:
    """Every SCHEMA node, the root included.

    A ``properties`` mapping is keyed by field name rather than by keyword,
    so it is descended into without being read as a schema itself.
    """
    if not isinstance(node, dict):
        return []
    mapping: dict[str, object] = node
    found = [mapping]
    properties = mapping.get("properties")
    if isinstance(properties, dict):
        sub_schemas: dict[str, object] = properties
        for value in sub_schemas.values():
            found.extend(walk(value))
    found.extend(walk(mapping.get("items")))
    branches = mapping.get("anyOf")
    if isinstance(branches, list):
        entries: list[object] = branches
        for branch in entries:
            found.extend(walk(branch))
    return found


# ---------------------------------------------------------------------------
# KOD-91-AC-1 — golden per schema, allowlist-only, inlined
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(WIRE_SCHEMAS))
def test_sanitized_schema_matches_its_golden(name: str) -> None:
    """The stripped form is pinned: a model change shows up as a schema diff."""
    expected = json.loads((GOLDENS / f"{name}.json").read_text(encoding="utf-8"))
    assert sanitize_schema(WIRE_SCHEMAS[name]) == expected


@pytest.mark.parametrize("name", sorted(WIRE_SCHEMAS))
def test_sanitized_schema_uses_only_allowlist_keywords(name: str) -> None:
    """The stripper's own guarantee: nothing outside the allowlist survives."""
    offenders = sorted(
        {
            keyword
            for node in walk(sanitize_schema(WIRE_SCHEMAS[name]))
            for keyword in node
            if keyword not in STRICT_MODE_KEYWORDS
        }
    )
    assert offenders == []


@pytest.mark.parametrize("name", sorted(WIRE_SCHEMAS))
def test_sanitized_schema_has_no_references_left(name: str) -> None:
    """Nested models are inlined; nothing points at a definitions block."""
    serialized = json.dumps(sanitize_schema(WIRE_SCHEMAS[name]))
    assert "$defs" not in serialized
    assert "$ref" not in serialized


@pytest.mark.parametrize("name", sorted(WIRE_SCHEMAS))
def test_every_object_node_is_closed(name: str) -> None:
    """``additionalProperties: false`` on every object, not only the root."""
    open_nodes = [
        node.get("title")
        for node in walk(WIRE_SCHEMAS[name])
        if node.get("type") == "object"
        and node.get("additionalProperties") is not False
    ]
    assert open_nodes == []


def test_the_wire_schema_roster_is_every_precomputed_schema() -> None:
    """The roster is a census of the module, not a sample of it."""
    source = (SRC / "types" / "domain" / "agent.py").read_text(encoding="utf-8")
    declared = set(re.findall(r"^([A-Z_]+_SCHEMA): dict", source, re.MULTILINE))
    assert declared == set(WIRE_SCHEMAS)


def test_a_nested_model_is_inlined_with_its_fields() -> None:
    """Inlining is structural, not a deletion of the reference."""
    audit = sanitize_schema(WIRE_SCHEMAS["CONTENT_AUDIT_SCHEMA"])
    properties = audit["properties"]
    assert isinstance(properties, dict)
    findings = properties["findings"]
    assert isinstance(findings, dict)
    item = findings["items"]
    assert isinstance(item, dict)
    item_properties = item["properties"]
    assert isinstance(item_properties, dict)
    assert set(item_properties) == set(ContentAuditFinding.model_fields)


def test_a_recursive_model_is_refused_rather_than_half_inlined() -> None:
    """A cycle has no strict-mode form; guessing one would be silent damage."""
    recursive: dict[str, object] = {
        "type": "object",
        "properties": {"child": {"$ref": "#/$defs/Node"}},
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {"child": {"$ref": "#/$defs/Node"}},
            }
        },
    }
    with pytest.raises(WireSchemaError):
        sanitize_schema(recursive)


def test_an_unresolvable_reference_is_refused() -> None:
    """A reference naming no definition fails loudly instead of vanishing."""
    with pytest.raises(WireSchemaError):
        sanitize_schema({"type": "object", "properties": {"x": {"$ref": "#/$defs/No"}}})


# ---------------------------------------------------------------------------
# KOD-91-AC-2 — the client keeps validating what the stripped form drops
# ---------------------------------------------------------------------------


def test_client_validation_unchanged() -> None:
    """Stripped constraints are still enforced against the original models."""
    slug_schema = sanitize_schema(WIRE_SCHEMAS["BRANCH_NAME_SCHEMA"])
    assert "maxLength" not in json.dumps(slug_schema)
    with pytest.raises(ValidationError):
        BranchNameOutput(slug="x" * 200)
    with pytest.raises(ValidationError):
        BranchNameOutput(slug="")

    commit_schema = sanitize_schema(WIRE_SCHEMAS["COMMIT_MESSAGE_SCHEMA"])
    assert "minLength" not in json.dumps(commit_schema)
    with pytest.raises(ValidationError):
        CommitMessageOutput(title="", body="body")

    with pytest.raises(ValidationError):
        PRDescriptionOutput(title="t" * 200, description="d")
    with pytest.raises(ValidationError):
        ContentAuditFinding(start=-1, end=2, rationale="why")


# ---------------------------------------------------------------------------
# KOD-134 — the wire states the contract the response is judged by
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(WIRE_SCHEMAS))
def test_each_wire_schema_is_its_own_models_schema(name: str) -> None:
    """The whole roster, not the one schema a later test reads by hand.

    Every other guarantee about the wire is per-schema or per-keyword, so a
    constant put back through the stripper — or built any other way than from
    its model — passes them all. This is the one that fails.
    """
    assert WIRE_SCHEMAS[name] == WIRE_MODELS[name].model_json_schema()


def test_a_dispatched_schema_carries_its_constraints() -> None:
    """The dispatched schema says what the response is judged by.

    ``CriteriaValidationOutput`` carries both kinds the stripper deleted: a
    pattern on a criterion id and a length bound on its sibling. Either one
    vanishing from the wire again fails this.
    """
    definitions = WIRE_SCHEMAS["CRITERIA_VALIDATION_SCHEMA"]["$defs"]
    assert isinstance(definitions, dict)
    contradiction = definitions["Contradiction"]
    assert isinstance(contradiction, dict)
    properties = contradiction["properties"]
    assert isinstance(properties, dict)

    criterion_ids = properties["criterionIds"]
    assert isinstance(criterion_ids, dict)
    item = criterion_ids["items"]
    assert isinstance(item, dict)
    assert item["pattern"] == CRITERION_ID_PATTERN

    explanation = properties["explanation"]
    assert isinstance(explanation, dict)
    assert explanation["minLength"] == 1


# ---------------------------------------------------------------------------
# KOD-91-AC-3 — one construction path
# ---------------------------------------------------------------------------


def test_every_dispatch_site_sends_the_models_own_schema() -> None:
    """Every site names a roster schema; none filters one through the stripper."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            for argument in SCHEMA_ARGUMENT.findall(line):
                if not is_rostered_argument(argument):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT).as_posix()}:{line_number}"
                        f" sends {argument}"
                    )
    assert offenders == []


def test_the_dispatch_site_guard_catches_an_unsanitized_schema() -> None:
    """The guard's own detector fires on the shape it exists to reject."""
    raw = '                    "schema": TicketDraftOutput.model_json_schema(),'
    found = SCHEMA_ARGUMENT.findall(raw)
    assert found
    assert not is_rostered_argument(found[0])


def test_the_dispatch_site_guard_catches_a_filtered_schema() -> None:
    """The half the unwiring added: a re-wired site is rejected too."""
    raw = '                    "schema": sanitize_schema(request.output_schema),'
    found = SCHEMA_ARGUMENT.findall(raw)
    assert found
    assert found[0].startswith(SANITIZER_CALL)
    assert not is_rostered_argument(found[0])


def test_every_dispatch_site_is_accounted_for() -> None:
    """Non-vacuity: the sweep above reads real sites, not an empty tree."""
    sites = [
        argument
        for path in sorted(SRC.rglob("*.py"))
        for argument in SCHEMA_ARGUMENT.findall(path.read_text(encoding="utf-8"))
    ]
    assert len(sites) >= len(WIRE_SCHEMAS)
