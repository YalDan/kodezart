"""One credential table, read by both surfaces that face outward.

The defect this suite exists to make unrepeatable was not a missing
pattern, it was a missing PLACE for a pattern to be missing from: the
outbound gate carried its own shipped credential list and the wire-egress
scrubber carried another, so covering a vendor meant editing two tables and
the tracker credential the deployment actually holds was in neither.

Every assertion below derives both sides from
:data:`kodezart.types.domain.credentials.CREDENTIAL_SHAPES`, so a shape
added for one surface is on both, and the concrete corpus is run through
the REAL gate rather than through a copy of its patterns.
"""

from typing import Final

import pytest

from kodezart.adapters.outbound_admission import _CREDENTIAL_PATTERNS, OutboundAdmission
from kodezart.core.config import AppConfig
from kodezart.core.error_egress import _COMPILED_CREDENTIAL_SHAPES, redact_credentials
from kodezart.types.domain.credentials import CREDENTIAL_SHAPES, REDACTION_SENTINEL
from kodezart.types.domain.gating import (
    ContentClass,
    GateVerdict,
    OutboundDestination,
    RedactionCategory,
    RepoVisibility,
    WriterShape,
)
from kodezart.types.domain.tracker import TrackerBackend
from tests.docs.configuration import model_types
from tests.outbound import make_admission

# Each fixture is assembled by concatenation so no literal in this file has
# the shape of a real credential, and each names the AppConfig field whose
# value it imitates.
_FORGE_TOKEN: Final[str] = "ghp_" + ("A" * 40)
_KNOWLEDGE_TOKEN: Final[str] = "ntn_" + ("B" * 44)
_TRACKER_TOKEN: Final[str] = "lin_api_" + ("C" * 40)
_ENGINE_KEY: Final[str] = "sk-ant-api03-" + ("D" * 90)

#: One sample per credential a running deployment can hold, labelled with
#: the field that holds it.  A vendor whose credential this build can hold
#: and whose shape no table covers fails both halves of the pair test.
_HELD_CREDENTIALS: Final[tuple[tuple[str, str], ...]] = (
    ("github_token", f"git clone https://x-access-token:{_FORGE_TOKEN}@h/o/r.git"),
    ("github_token", f"forge call rejected: {_FORGE_TOKEN}"),
    ("knowledge credential", f"knowledge call rejected: {_KNOWLEDGE_TOKEN}"),
    ("TrackerSettings.token", f"tracker call rejected: {_TRACKER_TOKEN}"),
    ("engine credential", f"process error: {_ENGINE_KEY}"),
)


def _gate(config: AppConfig) -> OutboundAdmission:
    return make_admission()


@pytest.mark.usefixtures("_pristine_environment")
def test_the_gate_and_the_scrubber_read_the_same_table() -> None:
    """Both surfaces derive from the table, so neither can drift off it."""
    shipped = [pattern.pattern for pattern in _CREDENTIAL_PATTERNS]
    compiled = [pattern.pattern for pattern, _ in _COMPILED_CREDENTIAL_SHAPES]

    assert shipped == [shape.pattern for shape in CREDENTIAL_SHAPES]
    assert compiled == [shape.pattern for shape in CREDENTIAL_SHAPES]


@pytest.mark.usefixtures("_pristine_environment")
@pytest.mark.parametrize(("field", "payload"), _HELD_CREDENTIALS)
async def test_each_credential_this_build_can_hold_is_blocked_and_scrubbed(
    field: str,
    payload: str,
) -> None:
    """The pair, per credential: the gate refuses it AND egress scrubs it.

    Asserted together because a shape covering one surface and not the
    other is the state this table exists to make unreachable, and because
    the two surfaces catch a credential on different paths — one on the way
    to a public write, one on the way onto the wire in an error.
    """
    decision = await _gate(AppConfig()).gate(
        content=payload,
        visibility=RepoVisibility.PUBLIC,
        shape=WriterShape.PROSE,
        destination=OutboundDestination.PR_BODY,
        content_class=ContentClass.AUTHORED,
    )

    assert decision.verdict is GateVerdict.BLOCKED, field
    assert RedactionCategory.CREDENTIALS in decision.categories, field

    redacted = redact_credentials(payload)
    assert REDACTION_SENTINEL in redacted, field
    assert redacted != payload, field


def test_the_one_tracker_backend_has_a_credential_shape() -> None:
    """The specific gap: one backend, whose credential shape must be covered.

    Read off the enum rather than written down, so selecting a second
    backend fails here until its credential taxonomy joins the table.
    """
    covered = {shape.vendor for shape in CREDENTIAL_SHAPES}

    assert {backend.value for backend in TrackerBackend} <= covered


#: Every credential-bearing field, mapped to a value in its live shape.
_CREDENTIAL_FIELD_FIXTURES: Final[dict[str, str]] = {
    "github_token": _FORGE_TOKEN,
    "TrackerSettings.token": _TRACKER_TOKEN,
    "HttpKnowledge.credential": _KNOWLEDGE_TOKEN,
    "StdioKnowledge.credential": _KNOWLEDGE_TOKEN,
}
_SHAPELESS_TOKEN_FIELDS = {"HttpKnowledge.gateway_credential"}


def _credential_fields(model=AppConfig):
    fields = {}
    for name, field in model.model_fields.items():
        if name == "token" or name.endswith(("_token", "credential")):
            key = name if model is AppConfig else f"{model.__name__}.{name}"
            fields[key] = field
        for nested in model_types(field.annotation):
            fields.update(_credential_fields(nested))
    return fields


def test_every_token_field_maps_into_the_table_or_names_its_exemption() -> None:
    """The class, closed: a credential field the table does not know fails here.

    The enumeration follows the actual configuration models, including nested
    sections and transport arms. Token and credential fields cannot ship
    without either a shape the scrubber recognises or a recorded shapeless
    exemption, and neither can this test go vacuous when one is renamed.
    """
    token_fields = set(_credential_fields())

    assert token_fields == set(_CREDENTIAL_FIELD_FIXTURES) | _SHAPELESS_TOKEN_FIELDS
    for field, value in _CREDENTIAL_FIELD_FIXTURES.items():
        scrubbed = redact_credentials(f"the {field} value {value} leaked")
        assert value not in scrubbed, field
        assert REDACTION_SENTINEL in scrubbed, field


def test_every_shapeless_token_field_is_a_secret_that_never_serializes() -> None:
    """The exemption's ground, asserted: shapeless means guarded another way."""
    for field in _SHAPELESS_TOKEN_FIELDS:
        info = _credential_fields()[field]

        assert info.exclude is True, field
        assert "SecretStr" in str(info.annotation), field
