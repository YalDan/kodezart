"""One traversal of a JSON schema, read by every sweep in this package.

Four keywords carry a schema, and a RAW schema keeps its nested models
behind ``$defs`` rather than inline.  A sweep that descends its own subset
of the four therefore stops wherever the subset stops — silently, and with
nothing failing to say so: the description sweep and the closure sweep each
grew that hole independently, each while its docstring claimed the whole
schema.  There is one descent now, and each sweep pins its own reach with a
non-vacuity test beside it.
"""

from collections.abc import Iterator
from typing import Final

#: Where a raw schema keeps the models its fields reference.
DEFS: Final[str] = "$defs"
_PROPERTIES: Final[str] = "properties"
_ITEMS: Final[str] = "items"
_ANY_OF: Final[str] = "anyOf"


def schema_nodes(node: object, path: str) -> Iterator[tuple[str, dict[str, object]]]:
    """Every schema node under *node*, *node* itself included, with its path.

    ``properties`` and ``$defs`` are keyed by name rather than by keyword,
    so their values are schemas while the mapping holding them is not.  An
    ``anyOf`` arm describes the same field as the branch point above it, so
    it keeps that field's path.
    """
    if not isinstance(node, dict):
        return
    mapping: dict[str, object] = node
    yield path, mapping
    properties = mapping.get(_PROPERTIES)
    if isinstance(properties, dict):
        fields: dict[str, object] = properties
        for name, field in fields.items():
            yield from schema_nodes(field, f"{path}.{name}")
    definitions = mapping.get(DEFS)
    if isinstance(definitions, dict):
        models: dict[str, object] = definitions
        for name, model in models.items():
            yield from schema_nodes(model, f"{path}.{DEFS}.{name}")
    yield from schema_nodes(mapping.get(_ITEMS), f"{path}[]")
    branches = mapping.get(_ANY_OF)
    if isinstance(branches, list):
        arms: list[object] = branches
        for branch in arms:
            yield from schema_nodes(branch, path)


def properties_of(node: dict[str, object]) -> dict[str, object]:
    """The fields *node* declares, keyed by name; empty when it declares none."""
    properties = node.get(_PROPERTIES)
    if not isinstance(properties, dict):
        return {}
    fields: dict[str, object] = properties
    return fields
