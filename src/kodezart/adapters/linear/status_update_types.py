"""The status-update listing envelope, measured through the connected app.

Read on 2026-09-21 on a project that carries status updates: the listing
answers a mapping whose ``statusUpdates`` member holds the items, with
``hasNextPage`` and ``cursor`` beside it, and each item carries its body and
its creation instant among other members the adapter does not read.

Only the two item fields the suppression compares on are declared, because
``extra="ignore"`` is the base's stance and a field declared here is a field
an absent or mistyped value refuses on.  The CREATE envelope is still
unmeasured — nothing of the write's answer is parsed — which is why this
module answers for the read alone.

Beside the module that names the tools rather than in it, the way the scope
reads' shapes are: the write-surface derivation reads every public class of
a module naming a tracker tool, and a payload shape is not a writer.
"""

from datetime import datetime

from kodezart.adapters.linear.wire import LinearWireModel


class LinearStatusUpdateWire(LinearWireModel):
    """One status update: the body it carries and when it was created.

    ``created_at`` is read because the caller orders by it rather than
    trusting the tool's own ordering, so the instant is part of the shape
    the adapter depends on and not incidental.
    """

    body: str
    created_at: datetime


class LinearStatusUpdatesWire(LinearWireModel):
    """The listing envelope: the items, under the member that carries them.

    Declared as required, so a payload without the member refuses as a
    protocol error instead of reading as a container carrying nothing — the
    one confusion that would let a report be posted twice.
    """

    status_updates: list[LinearStatusUpdateWire]
