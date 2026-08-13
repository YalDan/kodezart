"""KOD-91-AC-4 — every field the wire carries describes itself.

Field semantics used to live in prompt prose that restated the output shape,
which is a second copy of a contract the schema already carries.  The prose is
deleted by the set rewrite, so the descriptions have to be where the model can
read them: in the schema, on every field, including the nested ones.
"""

import pytest
from pydantic import BaseModel, Field

from kodezart.types.domain.agent import WIRE_SCHEMAS

#: Where a raw schema keeps the models its fields reference.
DEFS = "$defs"


def described_fields(node: object, path: str) -> list[tuple[str, bool]]:
    """Every property of *node*, with whether it carries a description.

    A raw schema carries a nested model as a bare ``$ref`` and keeps its
    fields in ``$defs``, so descending properties alone reaches root fields
    only.  Definitions are descended too — a definition is a schema, not a
    field, so it contributes its own properties and no entry of its own.
    """
    if not isinstance(node, dict):
        return []
    mapping: dict[str, object] = node
    found: list[tuple[str, bool]] = []
    properties = mapping.get("properties")
    if isinstance(properties, dict):
        entries: dict[str, object] = properties
        for name, sub in entries.items():
            described = isinstance(sub, dict) and bool(sub.get("description"))
            found.append((f"{path}.{name}", described))
            found.extend(described_fields(sub, f"{path}.{name}"))
    definitions = mapping.get(DEFS)
    if isinstance(definitions, dict):
        models: dict[str, object] = definitions
        for name, sub in models.items():
            found.extend(described_fields(sub, f"{path}.{DEFS}.{name}"))
    found.extend(described_fields(mapping.get("items"), f"{path}[]"))
    branches = mapping.get("anyOf")
    if isinstance(branches, list):
        arms: list[object] = branches
        for branch in arms:
            found.extend(described_fields(branch, path))
    return found


@pytest.mark.parametrize("name", sorted(WIRE_SCHEMAS))
def test_every_field_on_every_output_model_has_a_description(name: str) -> None:
    """Including nested models: the session reads the whole schema, not the root."""
    undescribed = [
        path
        for path, described in described_fields(WIRE_SCHEMAS[name], name)
        if not described
    ]
    assert undescribed == []


def test_the_sweep_reads_nested_fields_and_not_only_roots() -> None:
    """Non-vacuity, read off the same raw schemas the sweep above reads.

    Descending properties alone stops at the roots, because a raw schema
    names a nested model by reference and keeps its fields in ``$defs``.  The
    named path is a field of a model no root property spells out.
    """
    counted = [
        path
        for name in WIRE_SCHEMAS
        for path, _ in described_fields(WIRE_SCHEMAS[name], name)
    ]
    roots = [path for path in counted if f".{DEFS}." not in path]
    assert len(counted) > len(roots)
    assert f"CONTENT_AUDIT_SCHEMA.{DEFS}.ContentAuditFinding.rationale" in counted


def test_a_new_field_without_a_description_fails_the_sweep() -> None:
    """The rule's own detector, shown firing on the shape it rejects."""

    class Added(BaseModel):
        """A model whose new field forgot its description."""

        described: str = Field(description="this one says what it is")
        undescribed: str

    schema = Added.model_json_schema()
    undescribed = [
        path for path, described in described_fields(schema, "Added") if not described
    ]
    assert undescribed == ["Added.undescribed"]
