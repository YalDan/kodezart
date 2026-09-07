"""The single outbound-write path: gate, emit the verdict, raise on BLOCKED.

One implementation, not one per writer.  Every call site that writes outside
the process — the workflow's pull-request and artifact writers, the commit
persister, the tracker lifecycle writer — routes through this function, so
the ``outbound_content_gated`` event and ``OutboundContentBlockedError``
carry the same fields from every surface by construction rather than by each
writer remembering to spell them the same way.

A writer that reached the gate through its own copy of this logic is a
writer whose event can drift, and a gate whose observability drifts per
surface is one an operator cannot reason about.
"""

from kodezart.core.logging import BoundLogger
from kodezart.core.protocols import OutboundContentGate
from kodezart.domain.errors import OutboundContentBlockedError
from kodezart.types.domain.gating import (
    ContentClass,
    GateVerdict,
    OutboundDestination,
    RepoVisibility,
    WriterShape,
    content_digest,
    surface_of,
)


async def gated_write(
    *,
    gate: OutboundContentGate,
    log: BoundLogger,
    content: str,
    visibility: RepoVisibility,
    shape: WriterShape,
    destination: OutboundDestination,
    content_class: ContentClass,
) -> str:
    """Gate *content* for *destination*; return what may be written.

    BLOCKED raises: nothing is written and the failure kind, the categories
    and the per-hit rationales travel on the error, so a human can confirm
    or overrule rather than being told only that something was refused.

    ``content_class`` is passed straight through and never inferred here.
    This function does not know where the bytes came from; the writer that
    called it does, which is why the parameter is required.
    """
    decision = await gate.gate(
        content=content,
        visibility=visibility,
        shape=shape,
        destination=destination,
        content_class=content_class,
    )
    await log.ainfo(
        "outbound_content_gated",
        writer=destination.value,
        surface=surface_of(destination).value,
        verdict=decision.verdict.value,
        visibility=visibility.value,
        categories=[category.value for category in decision.categories],
        failure=None if decision.failure is None else decision.failure.value,
        content_digest=content_digest(content),
        hits=[
            {
                "category": hit.category.value,
                "start": hit.start,
                "end": hit.end,
                "rationale": hit.rationale,
            }
            for hit in decision.hits
        ],
    )
    if decision.verdict is GateVerdict.BLOCKED:
        msg = "Outbound content blocked before write"
        raise OutboundContentBlockedError(
            msg,
            writer=destination.value,
            categories=[category.value for category in decision.categories],
            failure=decision.failure,
            hits=decision.hits,
        )
    return decision.content
