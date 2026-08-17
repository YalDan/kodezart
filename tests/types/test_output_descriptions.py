"""KOD-91-AC-4 — every field the wire carries describes itself.

Field semantics used to live in prompt prose that restated the output shape,
which is a second copy of a contract the schema already carries.  The prose is
deleted by the set rewrite, so the descriptions have to be where the model can
read them: in the schema, on every field, including the nested ones.
"""

import pytest
from pydantic import BaseModel, Field

from kodezart.types.domain.agent import WIRE_SCHEMAS
from tests.types.schema_nodes import DEFS, properties_of, schema_nodes


def described_fields(schema: object, name: str) -> list[tuple[str, bool]]:
    """Every field of every node in *schema*, with whether it describes itself.

    A definition is a schema rather than a field, so it contributes its own
    fields and no entry of its own.
    """
    return [
        (f"{path}.{field}", isinstance(sub, dict) and bool(sub.get("description")))
        for path, node in schema_nodes(schema, name)
        for field, sub in properties_of(node).items()
    ]


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
