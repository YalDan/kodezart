"""The exact-write helper carries its caller's provenance to the gate."""

import pytest

from kodezart.core.logging import get_logger
from kodezart.core.outbound_write import gated_exact
from kodezart.types.domain.gating import (
    ContentClass,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
)
from tests.fakes import PassThroughGate

FACTS = "the bytes whose worth is their exactness"


def refusal() -> Exception:
    return AssertionError("a clean gate refuses nothing")


@pytest.mark.parametrize(
    ("destination", "content_class"),
    [
        (OutboundDestination.TRACKER_COMMENT, ContentClass.DERIVED),
        (OutboundDestination.PR_BODY, ContentClass.AUTHORED),
    ],
)
async def test_the_gate_is_asked_under_the_destination_and_class_it_was_given(
    destination, content_class
):
    """Neither term is decided here; both travel from the writer that knows them.

    A helper that supplied either itself would gate an authored body as a
    derived one, or a body bound for one surface under another's rules, and
    every writer sharing it would inherit that single wrong answer.
    """
    gate = PassThroughGate()

    result = await gated_exact(
        gate=gate,
        log=get_logger(__name__),
        content=FACTS,
        visibility=RepoVisibility.PRIVATE,
        destination=destination,
        content_class=content_class,
        refusal=refusal,
    )

    assert result == FACTS
    assert gate.destinations == [destination]
    assert gate.content_classes == [content_class]
    assert gate.calls == [(FACTS, RepoVisibility.PRIVATE, WriterShape.PROSE)]
