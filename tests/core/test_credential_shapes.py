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

from kodezart.adapters.pattern_outbound_gate import PatternOutboundContentGate
from kodezart.adapters.regex_content_scanner import RegexContentScanner
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
    ("knowledge_mcp_token", f"knowledge call rejected: {_KNOWLEDGE_TOKEN}"),
    ("tracker_token", f"tracker call rejected: {_TRACKER_TOKEN}"),
    ("engine credential", f"process error: {_ENGINE_KEY}"),
)


def _gate(config: AppConfig) -> PatternOutboundContentGate:
    return PatternOutboundContentGate(
        scanners=[RegexContentScanner(patterns=config.deny_patterns)],
        verdicts=config.deny_pattern_verdicts,
    )


@pytest.mark.usefixtures("_pristine_environment")
def test_the_gate_and_the_scrubber_read_the_same_table() -> None:
    """Both surfaces derive from the table, so neither can drift off it."""
    shipped = AppConfig().deny_patterns[RedactionCategory.CREDENTIALS]
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
