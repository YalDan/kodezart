"""The pull-request body the harness composes for itself.

The accept gate counts and decides; this renders.  Composed harness-side,
so a flag the reader must act on does not depend on a generator choosing
to mention it — which is also why the heading is a literal here rather
than in the gate's arithmetic, whose only markdown was this one string.
"""

from collections.abc import Sequence

from kodezart.types.domain.accept import FlaggedItem

FLAGGED_HEADING = "## Shipped with flags"


def append_flagged_section(body: str, items: Sequence[FlaggedItem]) -> str:
    """Append the flagged items to a pull-request *body*, verbatim.

    An empty list leaves the body byte-identical.
    """
    if not items:
        return body
    lines = [
        f"- {item.criterion_id}: {item.summary}"
        if item.criterion_id is not None
        else f"- {item.summary}"
        for item in items
    ]
    return "\n\n".join([body, FLAGGED_HEADING, "\n".join(lines)])
