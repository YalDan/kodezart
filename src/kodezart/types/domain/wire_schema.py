"""The one place a JSON schema is made fit for the wire.

The engine's strict structured-output mode is all-or-nothing over a fixed
keyword allowlist: a schema carrying ANY keyword outside it abandons
server-side enforcement for the whole schema and silently falls back to
client-side validation.  Every schema this system dispatched was raw
``model_json_schema()`` output — ``$defs``, ``$ref``, ``minLength``,
``maxLength``, numeric bounds — so enforcement was off everywhere, without a
single log line saying so.

:func:`sanitize_schema` normalizes a schema to that allowlist: references are
inlined, non-allowlisted keywords are dropped, and every object node is closed
with ``additionalProperties: false``.  Pydantic keeps validating payloads
against the ORIGINAL model, so nothing a stripped keyword expressed stops being
enforced — it stops being expressed twice.
"""

from typing import Final

#: The keywords the engine's strict mode understands. One keyword outside
#: this set disables server-side enforcement for the entire schema.
STRICT_MODE_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "$schema",
        "type",
        "description",
        "title",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "anyOf",
    }
)

_DEFS: Final[str] = "$defs"
_REF: Final[str] = "$ref"
_ALL_OF: Final[str] = "allOf"
_REF_PREFIX: Final[str] = "#/$defs/"


class WireSchemaError(ValueError):
    """A schema that cannot be expressed in strict mode without guessing."""

    def __init__(self, message: str, *, path: str) -> None:
        super().__init__(message)
        self.path = path


def sanitize_schema(schema: dict[str, object]) -> dict[str, object]:
    """Return *schema* in strict-mode form: inlined, allowlisted, closed."""
    definitions = schema.get(_DEFS)
    known: dict[str, object] = definitions if isinstance(definitions, dict) else {}
    inlined = _inline(schema, known, (), path="")
    if not isinstance(inlined, dict):
        msg = "the root of a wire schema must be an object schema"
        raise WireSchemaError(msg, path="")
    return _strip(inlined)


def _inline(
    node: object,
    definitions: dict[str, object],
    visiting: tuple[str, ...],
    *,
    path: str,
) -> object:
    """Replace every ``$ref`` with the definition it names, recursively."""
    if isinstance(node, list):
        items: list[object] = node
        return [
            _inline(item, definitions, visiting, path=f"{path}[{index}]")
            for index, item in enumerate(items)
        ]
    if not isinstance(node, dict):
        return node

    mapping: dict[str, object] = node
    reference = mapping.get(_REF)
    if isinstance(reference, str):
        return _resolve(reference, mapping, definitions, visiting, path=path)

    merged = _collapse_all_of(mapping, path=path)
    return {
        key: _inline(value, definitions, visiting, path=f"{path}/{key}")
        for key, value in merged.items()
        if key != _DEFS
    }


def _resolve(
    reference: str,
    node: dict[str, object],
    definitions: dict[str, object],
    visiting: tuple[str, ...],
    *,
    path: str,
) -> object:
    """Inline one ``$ref``, keeping the siblings that referenced it."""
    if not reference.startswith(_REF_PREFIX):
        msg = f"unsupported schema reference {reference!r}"
        raise WireSchemaError(msg, path=path)
    name = reference.removeprefix(_REF_PREFIX)
    if name in visiting:
        msg = f"recursive model {name!r} cannot be inlined for strict mode"
        raise WireSchemaError(msg, path=path)
    target = definitions.get(name)
    if target is None:
        msg = f"schema reference {reference!r} names no definition"
        raise WireSchemaError(msg, path=path)

    resolved = _inline(target, definitions, (*visiting, name), path=path)
    siblings = {
        key: _inline(value, definitions, visiting, path=f"{path}/{key}")
        for key, value in node.items()
        if key not in (_REF, _DEFS)
    }
    if not isinstance(resolved, dict):
        return resolved
    inlined: dict[str, object] = resolved
    return {**inlined, **siblings}


def _collapse_all_of(node: dict[str, object], *, path: str) -> dict[str, object]:
    """Fold a single-member ``allOf`` into its parent; refuse the rest."""
    member = node.get(_ALL_OF)
    if member is None:
        return node
    if not isinstance(member, list) or len(member) != 1:
        msg = "allOf with more than one subschema has no strict-mode form"
        raise WireSchemaError(msg, path=path)
    only = member[0]
    if not isinstance(only, dict):
        msg = "allOf member must be an object schema"
        raise WireSchemaError(msg, path=path)
    rest = {key: value for key, value in node.items() if key != _ALL_OF}
    return {**only, **rest}


def _strip(node: dict[str, object]) -> dict[str, object]:
    """Keep only allowlisted keywords and close every object node."""
    kept: dict[str, object] = {}
    for key, value in node.items():
        if key not in STRICT_MODE_KEYWORDS:
            continue
        if key == "properties" and isinstance(value, dict):
            properties: dict[str, object] = value
            kept[key] = {
                name: _strip(sub) if isinstance(sub, dict) else sub
                for name, sub in properties.items()
            }
        elif key == "anyOf" and isinstance(value, list):
            branches: list[object] = value
            kept[key] = [
                _strip(branch) if isinstance(branch, dict) else branch
                for branch in branches
            ]
        elif key == "items" and isinstance(value, dict):
            kept[key] = _strip(value)
        else:
            kept[key] = value
    if "properties" in kept or kept.get("type") == "object":
        kept["additionalProperties"] = False
    return kept
