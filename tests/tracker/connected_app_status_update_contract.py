"""Status-update contracts observed through the connected app on 2026-09-20.

This is NOT a fresh capture using the operation's service credential. The
declarations below establish a real public contract for implementation and
synthetic conformance, not deployment compatibility: ``save_status_update``
was on the 2026-08-25 service roster, but service-authenticated access to it
is unverified.

No create calls were performed. The success envelope was therefore not
measured, which is why the adapter reads nothing out of it.
"""

from collections.abc import Mapping

CONNECTED_APP_STATUS_UPDATE_ARGUMENTS: Mapping[str, frozenset[str]] = {
    "save_status_update": frozenset(
        {"body", "health", "id", "initiative", "isDiffHidden", "project", "type"},
    ),
}

CONNECTED_APP_STATUS_UPDATE_REQUIRED: Mapping[str, frozenset[str]] = {
    "save_status_update": frozenset({"type"}),
}

#: The declared values of ``type``. Each one is also the name of the argument
#: carrying that container's target, and each target accepts the same forms
#: the container read takes — a name, an id, an identifier or a slug.
CONNECTED_APP_STATUS_UPDATE_TYPES: frozenset[str] = frozenset({"project", "initiative"})
