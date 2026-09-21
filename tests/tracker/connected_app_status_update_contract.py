"""Status-update contracts observed through the connected app.

The create declaration was read on 2026-09-20 and the listing declaration on
2026-09-21. Neither is a fresh capture using the operation's service
credential. The declarations below establish a real public contract for
implementation and synthetic conformance, not deployment compatibility:
``save_status_update`` was on the 2026-08-25 service roster, but
service-authenticated access to it is unverified.

No create calls were performed. The CREATE envelope was therefore not
measured, which is why the adapter reads nothing out of it. The LISTING
envelope was measured — one read on a project that carries status updates,
and nothing was written to make it — which is why the adapter parses that
one and :data:`CONNECTED_APP_STATUS_UPDATES_ENVELOPE` records its members.
"""

from collections.abc import Mapping

CONNECTED_APP_STATUS_UPDATE_ARGUMENTS: Mapping[str, frozenset[str]] = {
    "save_status_update": frozenset(
        {"body", "health", "id", "initiative", "isDiffHidden", "project", "type"},
    ),
    "get_status_updates": frozenset(
        {
            "createdAt",
            "cursor",
            "id",
            "includeArchived",
            "initiative",
            "limit",
            "orderBy",
            "project",
            "type",
            "updatedAt",
            "user",
        },
    ),
}

CONNECTED_APP_STATUS_UPDATE_REQUIRED: Mapping[str, frozenset[str]] = {
    "save_status_update": frozenset({"type"}),
    "get_status_updates": frozenset({"type"}),
}

#: The listing envelope, by member name and nothing else: the member carrying
#: the items, the two item members the adapter reads, and the two page members
#: beside the list.  Read on 2026-09-21 through the connected app.  Names only
#: — no body of anybody's status update is recorded here.
CONNECTED_APP_STATUS_UPDATES_ENVELOPE: Mapping[str, frozenset[str]] = {
    "listing": frozenset({"statusUpdates", "hasNextPage", "cursor"}),
    "item": frozenset({"body", "createdAt"}),
}

#: The declared values of ``type``. Each one is also the name of the argument
#: carrying that container's target, and each target accepts the same forms
#: the container read takes — a name, an id, an identifier or a slug.
CONNECTED_APP_STATUS_UPDATE_TYPES: frozenset[str] = frozenset({"project", "initiative"})
