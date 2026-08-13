"""A JSON schema keyword stripper. UNWIRED — nothing dispatches its output.

TODO(KOD-134): this module is retained, unwired, and annotated. Every claim
the paragraphs below make about why stripping is safe is wrong, and each
defect is named here so the module states what is wrong with it for as long
as it exists. The replacement design is KOD-135 and is not decided here.

TODO(KOD-134) — the information asymmetry. The stripped schema is what the
model was TOLD; the original model is what the response was JUDGED BY. A
response complying perfectly with everything it was given was rejected on
rules it was never shown. It terminated a run of roughly 1723 seconds on one
non-conforming field.

TODO(KOD-134) — "it stops being expressed twice" is FALSE. The two
expressions are not duplicates of one statement. One is the contract stated
to the model, the other is the contract enforced against it. Deleting the
first is information loss, not deduplication.

TODO(KOD-134) — the trade. Its downstream half is a property of THIS
repository and is checkable here: a response violating a stripped constraint
raises out of the node, because every parse site calls the response model's
validate with no handler. Its runtime half — what the engine does with a
schema either way — is stated only under the markers below, and every claim
this module makes about the runtime lives there.

WHAT IS KNOWN ABOUT THE RUNTIME, and nothing outside this block is asserted:

OBSERVED — one live dispatch, 2026-08-13, by
``tests/probes/test_strict_output_enforcement.py`` (marker-gated; it does not
run in the default gate). It sent ``CRITERIA_VALIDATION_SCHEMA``, which
carries ``$defs``, ``minLength`` and a ``pattern``, and asked in the prompt
for a criterion id of ``AC-0``, which that pattern forbids. The run finished
with subtype ``success`` in 3 turns, the result event CARRIED a structured
payload, the payload's criterion id was ``AC-1``, and the response model
accepted it unchanged. So: a schema carrying keywords outside
:data:`STRICT_MODE_KEYWORDS` did not cost the dispatch its structured output,
and a constraint this module used to delete was honoured by the response.

OBSERVED, separately — a static string scan of the CLI binary the SDK bundles
at ``claude_agent_sdk/_bundled/claude`` finds the literal ``Init JSON schema
rejected, structured output disabled: `` beside the event name
``tengu_structured_output_failure`` and the literal ``Invalid JSON schema``,
and finds the identifier ``structuredOutputAttempts`` carried on an agent-run
result and read by a retry loop's stall diagnostics. Presence of strings in a
compiled artifact, nothing more.

STILL UNVERIFIED, and the dispatch above narrows rather than settles it:
WHICH mechanism honoured the pattern — decoding constrained against the
schema, a model that simply read the constraint in the schema it was shown,
or a retry that re-validated — since one dispatch separates none of the
three. Also unverified: that any schema is rejected at all; that a rejected
one turns enforcement off for the whole schema rather than failing the run;
that the attempt counter belongs to a re-validating retry; and that under
structured output the response SHAPE cannot be wrong, which is what would
make the asymmetry above the only remaining explanation for the incident. No
test in this repository fails if any of them is false, which is why they are
marked here rather than asserted.

TODO(KOD-134) — the name is wrong. Sanitizing is a CONTENT operation:
removing harmful or private material from a payload before it is published,
which is what the outbound gate does and is correctly named. This deletes
keywords from a CONTRACT. A sanitizer that removes things is working
correctly; a contract-downgrader that removes constraints is broken, and the
borrowed word decided which one a reviewer saw.

The original rationale, retained verbatim as the record of what was believed:

    The engine's strict structured-output mode is all-or-nothing over a fixed
    keyword allowlist: a schema carrying ANY keyword outside it abandons
    server-side enforcement for the whole schema and silently falls back to
    client-side validation.  Every schema this system dispatched was raw
    ``model_json_schema()`` output — ``$defs``, ``$ref``, ``minLength``,
    ``maxLength``, numeric bounds — so enforcement was off everywhere,
    without a single log line saying so.

    :func:`sanitize_schema` normalizes a schema to that allowlist: references
    are inlined, non-allowlisted keywords are dropped, and every object node
    is closed with ``additionalProperties: false``.  Pydantic keeps
    validating payloads against the ORIGINAL model, so nothing a stripped
    keyword expressed stops being enforced — it stops being expressed twice.
"""

from typing import Final

#: The keywords this stripper keeps. What the engine does with a schema
#: carrying anything else is the UNVERIFIED proposition in the module
#: docstring, not a property of this set.
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
    """A schema this stripper cannot rewrite without guessing."""

    def __init__(self, message: str, *, path: str) -> None:
        super().__init__(message)
        self.path = path


def sanitize_schema(schema: dict[str, object]) -> dict[str, object]:
    """Return *schema* inlined, allowlisted and closed.

    TODO(KOD-134): UNWIRED — no dispatch site calls this. Do not re-wire it.
    Four defects, stated in full in the module docstring: (1) the output is
    what the model is TOLD while the original model is what it is JUDGED BY,
    an information asymmetry; (2) the "stops being expressed twice" claim is
    FALSE, because the two expressions are the stated contract and the
    enforced contract, not one statement written down twice; (3) the trade is
    checkable on one side only — a violation of a stripped constraint raises
    out of the node here, while what the engine does with either schema is
    only ever stated under the markers in the module docstring; (4) the name
    is wrong, because sanitizing is a CONTENT operation and this deletes
    keywords from a CONTRACT. The replacement design is KOD-135.
    """
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
        msg = "allOf with more than one subschema has no single-node form"
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
