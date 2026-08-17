"""The wire states the contract the response is judged by (KOD-134).

Every dispatched schema is its model's own ``model_json_schema()`` output,
constraints intact: the contract the model is shown is the contract its
response is validated against.  The keyword stripper that used to sit
between the two is deleted — a response complying with everything it was
given was rejected on rules it was never shown — and these are the
assertions about the RAW schemas and the dispatch sites that survived it,
relocated from the stripper's test module per KOD-141's fire-ruling:
assertions about the wire are not pins of stripped output and are never
deleted with the mechanism they outlived.
"""

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
from tests.types.schema_nodes import DEFS, schema_nodes

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "kodezart"

#: Every ``output_format`` mapping in the sources names its schema by one of
#: the precomputed constants, or forwards the one its caller supplied. The
#: pattern reads the dispatch site as text, so a site that inlines a raw
#: ``model_json_schema()`` call is caught by the same check that catches a
#: site filtering a schema through a stripper.
SCHEMA_ARGUMENT = re.compile(r'"schema"\s*:\s*([A-Za-z_][A-Za-z0-9_.()\[\]]*)')
#: The deleted stripper's call shape, kept as text: a dispatch site that
#: re-introduces a keyword filter under the old name is rejected by the
#: same sweep that rejects an unrostered schema.
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


# ---------------------------------------------------------------------------
# The roster is the models' own schemas, constraints present
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(WIRE_SCHEMAS))
def test_each_wire_schema_is_its_own_models_schema(name: str) -> None:
    """The whole roster, not the one schema a later test reads by hand.

    Every other guarantee about the wire is per-schema or per-keyword, so a
    constant put back through a stripper — or built any other way than from
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


def test_the_models_enforce_the_constraints_the_wire_states() -> None:
    """A violation of a stated constraint raises at the parse site.

    The wire's guarantee has two halves — the schema states the contract and
    the response model enforces it — and this is the enforcement half, kept
    when the stripper's test went: length bounds and numeric bounds refuse
    exactly as the dispatched schema says they will.
    """
    with pytest.raises(ValidationError):
        BranchNameOutput(slug="x" * 200)
    with pytest.raises(ValidationError):
        BranchNameOutput(slug="")
    with pytest.raises(ValidationError):
        CommitMessageOutput(title="", body="body")
    with pytest.raises(ValidationError):
        PRDescriptionOutput(title="t" * 200, description="d")
    with pytest.raises(ValidationError):
        ContentAuditFinding(start=-1, end=2, rationale="why")


# ---------------------------------------------------------------------------
# The closed-object sweep and its non-vacuity twin (KOD-141)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(WIRE_SCHEMAS))
def test_every_object_node_is_closed(name: str) -> None:
    """``additionalProperties: false`` on every object, not only the root."""
    open_nodes = [
        path
        for path, node in schema_nodes(WIRE_SCHEMAS[name], name)
        if node.get("type") == "object"
        and node.get("additionalProperties") is not False
    ]
    assert open_nodes == []


def test_the_closure_sweep_reads_nested_objects_and_not_only_roots() -> None:
    """Non-vacuity, read off the same raw schemas the sweep above reads.

    A raw schema names a nested model by reference and keeps that model's
    object node in ``$defs``, so a sweep descending properties alone reaches
    exactly one node per schema and closes nothing else.
    """
    objects = [
        path
        for name in WIRE_SCHEMAS
        for path, node in schema_nodes(WIRE_SCHEMAS[name], name)
        if node.get("type") == "object"
    ]
    roots = [path for path in objects if f".{DEFS}." not in path]
    assert len(objects) > len(roots)
    assert f"CONTENT_AUDIT_SCHEMA.{DEFS}.ContentAuditFinding" in objects


def test_the_wire_schema_roster_is_every_precomputed_schema() -> None:
    """The roster is a census of the module, not a sample of it."""
    source = (SRC / "types" / "domain" / "agent.py").read_text(encoding="utf-8")
    declared = set(re.findall(r"^([A-Z_]+_SCHEMA): dict", source, re.MULTILINE))
    assert declared == set(WIRE_SCHEMAS)


# ---------------------------------------------------------------------------
# One construction path at every dispatch site
# ---------------------------------------------------------------------------


def test_every_dispatch_site_sends_the_models_own_schema() -> None:
    """Every site names a roster schema; none filters or builds its own."""
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
    """A re-wired site is rejected too, with the stripper itself long gone."""
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
